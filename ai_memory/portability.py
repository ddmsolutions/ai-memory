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
    }


def import_store(conn: sqlite3.Connection, data: dict) -> dict:
    if data.get("format") != "ai-memory-export":
        raise ValueError("not an ai-memory export file")

    mem_map: dict[int, int] = {}
    imported = deduped = 0
    for m in data.get("memories", []):
        existing = conn.execute(
            "SELECT id FROM memories WHERE type = ? AND scope = ? AND content = ?"
            " AND origin_session IS ? AND created_at = ?",
            (m["type"], m["scope"], m["content"], m.get("origin_session"), m["created_at"]),
        ).fetchone()
        if existing:
            mem_map[m["id"]] = existing["id"]
            deduped += 1
            continue
        cur = conn.execute(
            "INSERT INTO memories (type, scope, content, origin_session, confidence,"
            " pinned, consolidated, recall_count, last_recalled_at, created_at,"
            " valence, verify_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (m["type"], m["scope"], m["content"], m.get("origin_session"),
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

    conn.commit()
    return {"imported": imported, "deduplicated": deduped,
            "entities": len(ent_map), "edges": len(data.get("edges", [])),
            "mentions": len(data.get("memory_entities", []))}


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
    from . import store

    text = Path(path).read_text(encoding="utf-8")
    imported = skipped = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith(("- ", "* ")) and len(line) > 15):
            continue
        content = line[2:].strip().lstrip("*").strip()
        if not content or content.startswith(("[", "#")):
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
    return {"imported": imported, "skipped": skipped}


def export_to_file(conn: sqlite3.Connection, path: Path) -> None:
    Path(path).write_text(json.dumps(export_store(conn), indent=1), encoding="utf-8")


def import_from_file(conn: sqlite3.Connection, path: Path) -> dict:
    return import_store(conn, json.loads(Path(path).read_text(encoding="utf-8")))
