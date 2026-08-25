"""Export / import (FR-X1, FR-X2): full-store JSON, lossless round trip.

Export carries every table except injection_log (session-ephemeral by
design). Import deduplicates memories on (type, scope, content,
origin_session, created_at) and remaps all internal references
(promoted_from, superseded_by, edges, mentions), so importing the same
file twice is a no-op. Rows are inserted verbatim, not via remember():
content was already redacted at original capture and must round-trip
byte-identical.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

MEMORY_COLS = (
    "id", "type", "scope", "content", "origin_session", "promoted_from",
    "confidence", "pinned", "consolidated", "superseded_by", "recall_count",
    "last_recalled_at", "created_at", "valence", "verify_by",
)


def export_store(conn: sqlite3.Connection) -> dict:
    def rows(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(sql).fetchall()]

    return {
        "format": "ai-memory-export",
        "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
        "memories": rows("SELECT * FROM memories ORDER BY id"),
        "entities": rows("SELECT * FROM entities ORDER BY id"),
        "edges": rows("SELECT * FROM edges ORDER BY id"),
        "memory_entities": rows("SELECT * FROM memory_entities"),
        "memory_links": rows("SELECT * FROM memory_links"),
        "intentions": rows("SELECT * FROM intentions ORDER BY id"),
        "memory_embeddings": rows("SELECT * FROM memory_embeddings"),
    }


def import_store(conn: sqlite3.Connection, data: dict) -> dict:
    if data.get("format") != "ai-memory-export":
        raise ValueError("not an ai-memory export file")

    from . import redact as _redact

    mem_map: dict[int, int] = {}
    imported = deduped = quarantined = 0
    for m in data.get("memories", []):
        scope = m["scope"]
        # FR-C8 applies to every insert funnel: instruction-shaped imported
        # rows land in quarantine, not in a recallable scope.
        if scope != "quarantine" and _redact.screen_instructions(m["content"]):
            scope = "quarantine"
            quarantined += 1
        existing = conn.execute(
            "SELECT id FROM memories WHERE type = ? AND scope = ? AND content = ?"
            " AND origin_session IS ? AND created_at = ?",
            (m["type"], scope, m["content"], m.get("origin_session"), m["created_at"]),
        ).fetchone()
        if existing:
            mem_map[m["id"]] = existing["id"]
            deduped += 1
            continue
        cur = conn.execute(
            "INSERT INTO memories (type, scope, content, origin_session, confidence,"
            " pinned, consolidated, recall_count, last_recalled_at, created_at,"
            " valence, verify_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (m["type"], scope, m["content"], m.get("origin_session"),
             m["confidence"], m["pinned"], m["consolidated"], m["recall_count"],
             m.get("last_recalled_at"), m["created_at"], m.get("valence"),
             m.get("verify_by")),
        )
        mem_map[m["id"]] = cur.lastrowid
        imported += 1

    # Second pass: remap internal references now every id is known.
    for m in data.get("memories", []):
        new_id = mem_map[m["id"]]
        for col in ("promoted_from", "superseded_by"):
            old_ref = m.get(col)
            if old_ref is not None and old_ref in mem_map:
                conn.execute(
                    f"UPDATE memories SET {col} = ? WHERE id = ? AND {col} IS NULL",
                    (mem_map[old_ref], new_id),
                )

    ent_map: dict[int, int] = {}
    for e in data.get("entities", []):
        cur = conn.execute(
            "INSERT INTO entities (name, etype, summary, created_at) VALUES (?,?,?,?)"
            " ON CONFLICT(name, etype) DO UPDATE SET"
            " summary = COALESCE(excluded.summary, entities.summary) RETURNING id",
            (e["name"], e["etype"], e.get("summary"), e["created_at"]),
        )
        ent_map[e["id"]] = cur.fetchone()[0]

    for edge in data.get("edges", []):
        if edge["src"] not in ent_map or edge["dst"] not in ent_map:
            continue
        conn.execute(
            "INSERT INTO edges (src, dst, rel, weight, memory_id, created_at)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(src, dst, rel) DO NOTHING",
            (ent_map[edge["src"]], ent_map[edge["dst"]], edge["rel"], edge["weight"],
             mem_map.get(edge.get("memory_id")), edge["created_at"]),
        )

    for me in data.get("memory_entities", []):
        if me["memory_id"] in mem_map and me["entity_id"] in ent_map:
            conn.execute(
                "INSERT OR IGNORE INTO memory_entities (memory_id, entity_id, created_at)"
                " VALUES (?,?,?)",
                (mem_map[me["memory_id"]], ent_map[me["entity_id"]], me["created_at"]),
            )

    for link in data.get("memory_links", []):
        if link["src_memory"] in mem_map and link["dst_memory"] in mem_map:
            conn.execute(
                "INSERT INTO memory_links (src_memory, dst_memory, rel, weight,"
                " reinforce_count, last_reinforced, created_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(src_memory, dst_memory, rel) DO NOTHING",
                (mem_map[link["src_memory"]], mem_map[link["dst_memory"]], link["rel"],
                 link["weight"], link["reinforce_count"], link["last_reinforced"],
                 link["created_at"]),
            )

    intentions_in = 0
    for it in data.get("intentions", []):
        if conn.execute(
            "SELECT 1 FROM intentions WHERE content = ? AND trigger_kind = ?"
            " AND trigger_value = ? AND created_at = ?",
            (it["content"], it["trigger_kind"], it["trigger_value"], it["created_at"]),
        ).fetchone():
            continue
        conn.execute(
            "INSERT INTO intentions (content, trigger_kind, trigger_value, scope,"
            " status, origin_session, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?)",
            (it["content"], it["trigger_kind"], it["trigger_value"], it["scope"],
             it["status"], it.get("origin_session"), it["created_at"], it.get("resolved_at")),
        )
        intentions_in += 1

    for emb in data.get("memory_embeddings", []):
        if emb["memory_id"] in mem_map:
            conn.execute(
                "INSERT OR REPLACE INTO memory_embeddings (memory_id, model, vector, created_at)"
                " VALUES (?,?,?,?)",
                (mem_map[emb["memory_id"]], emb["model"], emb["vector"], emb["created_at"]),
            )

    conn.commit()
    return {"imported": imported, "deduplicated": deduped, "quarantined": quarantined,
            "entities": len(ent_map), "edges": len(data.get("edges", [])),
            "mentions": len(data.get("memory_entities", [])),
            "links": len(data.get("memory_links", [])),
            "intentions": intentions_in,
            "embeddings": len(data.get("memory_embeddings", []))}


_PROCEDURAL_MARKERS = (
    "always", "never", "must", "don't", "do not", "avoid", "prefer", "use ",
    "run ", "check ", "before ", "ensure",
)


def seed_from_markdown(
    conn: sqlite3.Connection, path: Path, scope: str = "global"
) -> dict:
    """FR-X3: onboarding importer. Bullet lines from an existing CLAUDE.md or
    notes file become memories: rule-shaped lines procedural, the rest semantic.
    Dedup by content; goes through remember() so redaction applies."""
    from . import redact as _redact, store

    text = Path(path).read_text(encoding="utf-8")
    imported = skipped = screened = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith(("- ", "* ")) and len(line) > 15):
            continue
        content = line[2:].strip().lstrip("*").strip()
        if not content or content.startswith(("[", "#")):
            continue
        if _redact.screen_instructions(content):
            screened += 1
            continue
        if conn.execute(
            "SELECT 1 FROM memories WHERE content = ? AND scope = ?", (content, scope)
        ).fetchone():
            skipped += 1
            continue
        lowered = content.lower()
        mtype = "procedural" if any(m in lowered for m in _PROCEDURAL_MARKERS) else "semantic"
        store.remember(conn, content, mtype=mtype, scope=scope)
        imported += 1
    return {"imported": imported, "skipped": skipped, "screened": screened}


def export_to_file(conn: sqlite3.Connection, path: Path) -> None:
    Path(path).write_text(json.dumps(export_store(conn), indent=1), encoding="utf-8")


def import_from_file(conn: sqlite3.Connection, path: Path) -> dict:
    return import_store(conn, json.loads(Path(path).read_text(encoding="utf-8")))
