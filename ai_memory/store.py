"""Core memory operations over the SQLite store."""

from __future__ import annotations

import sqlite3
from typing import Iterable

MEMORY_TYPES = ("episodic", "semantic", "procedural")


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 OR-query of quoted terms."""
    terms = [t.strip('"') for t in text.split() if t.strip('"')]
    return " OR ".join(f'"{t}"' for t in terms) or '""'


def remember(
    conn: sqlite3.Connection,
    content: str,
    mtype: str = "episodic",
    scope: str = "global",
    origin_session: str | None = None,
    promoted_from: int | None = None,
    confidence: float = 0.7,
    pinned: bool = False,
    supersedes: int | None = None,
) -> int:
    if mtype not in MEMORY_TYPES:
        raise ValueError(f"type must be one of {MEMORY_TYPES}")
    cur = conn.execute(
        "INSERT INTO memories (type, scope, content, origin_session, promoted_from,"
        " confidence, pinned) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (mtype, scope, content, origin_session, promoted_from, confidence, int(pinned)),
    )
    new_id = cur.lastrowid
    if supersedes is not None:
        conn.execute(
            "UPDATE memories SET superseded_by = ? WHERE id = ?", (new_id, supersedes)
        )
    conn.commit()
    return new_id


def search(
    conn: sqlite3.Connection,
    query: str,
    mtype: str | None = None,
    scope: str | None = None,
    limit: int = 20,
    include_superseded: bool = False,
) -> list[sqlite3.Row]:
    table = "memories" if include_superseded else "v_active_memories"
    sql = (
        f"SELECT m.*, bm25(memories_fts) AS rank FROM memories_fts f"
        f" JOIN {table} m ON m.id = f.rowid"
        " WHERE memories_fts MATCH ?"
    )
    params: list = [_fts_query(query)]
    if mtype:
        sql += " AND m.type = ?"
        params.append(mtype)
    if scope:
        sql += " AND m.scope IN (?, 'global')"
        params.append(scope)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def recall_pack(
    conn: sqlite3.Connection,
    task: str | None = None,
    scope: str = "global",
    limit: int = 12,
) -> str:
    """Compile a compact markdown recall pack for injection at session start.

    Layers, in priority order: pinned memories, procedural lessons,
    semantic facts, then task-relevant matches if a task is given.
    """
    seen: set[int] = set()
    sections: list[tuple[str, list[sqlite3.Row]]] = []

    def take(rows: Iterable[sqlite3.Row], n: int) -> list[sqlite3.Row]:
        out = []
        for r in rows:
            if r["id"] not in seen:
                seen.add(r["id"])
                out.append(r)
            if len(out) >= n:
                break
        return out

    base = "scope IN (?, 'global')"
    pinned = conn.execute(
        f"SELECT * FROM v_active_memories WHERE pinned = 1 AND {base} ORDER BY created_at DESC",
        (scope,),
    ).fetchall()
    procedural = conn.execute(
        f"SELECT * FROM v_active_memories WHERE type = 'procedural' AND {base}"
        " ORDER BY confidence DESC, recall_count DESC, created_at DESC",
        (scope,),
    ).fetchall()
    semantic = conn.execute(
        f"SELECT * FROM v_active_memories WHERE type = 'semantic' AND {base}"
        " ORDER BY confidence DESC, created_at DESC",
        (scope,),
    ).fetchall()

    sections.append(("Pinned", take(pinned, limit)))
    sections.append(("How to work (procedural)", take(procedural, max(3, limit // 3))))
    sections.append(("Known facts (semantic)", take(semantic, max(3, limit // 3))))
    if task:
        matches = search(conn, task, scope=scope, limit=limit)
        sections.append((f"Relevant to: {task}", take(matches, max(3, limit // 3))))

    recalled = list(seen)
    if recalled:
        qmarks = ",".join("?" * len(recalled))
        conn.execute(
            f"UPDATE memories SET recall_count = recall_count + 1,"
            f" last_recalled_at = datetime('now') WHERE id IN ({qmarks})",
            recalled,
        )
        conn.commit()

    lines = ["<!-- ai-memory recall pack: treat as context, verify anything critical -->"]
    for title, rows in sections:
        if not rows:
            continue
        lines.append(f"\n{title}:")
        lines.extend(f"- {r['content']}" for r in rows)
    return "\n".join(lines) if len(lines) > 1 else ""


def forget(conn: sqlite3.Connection, memory_id: int) -> None:
    conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.commit()


def set_pin(conn: sqlite3.Connection, memory_id: int, pinned: bool) -> None:
    conn.execute("UPDATE memories SET pinned = ? WHERE id = ?", (int(pinned), memory_id))
    conn.commit()


def promote(
    conn: sqlite3.Connection, memory_id: int, mtype: str, content: str | None = None
) -> int:
    """Consolidation primitive: promote an episodic row into a durable
    semantic or procedural memory, marking the source consolidated."""
    if mtype not in ("semantic", "procedural"):
        raise ValueError("promote target must be semantic or procedural")
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        raise ValueError(f"no memory with id {memory_id}")
    new_id = remember(
        conn,
        content or row["content"],
        mtype=mtype,
        scope=row["scope"],
        promoted_from=memory_id,
        confidence=min(1.0, row["confidence"] + 0.1),
    )
    conn.execute("UPDATE memories SET consolidated = 1 WHERE id = ?", (memory_id,))
    conn.commit()
    return new_id


def unconsolidated(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM v_consolidation_backlog ORDER BY created_at LIMIT ?",
        (limit,),
    ).fetchall()


def status(conn: sqlite3.Connection) -> dict:
    counts = dict(
        conn.execute("SELECT type, COUNT(*) FROM memories GROUP BY type").fetchall()
    )
    return {
        "episodic": counts.get("episodic", 0),
        "semantic": counts.get("semantic", 0),
        "procedural": counts.get("procedural", 0),
        "pinned": conn.execute("SELECT COUNT(*) FROM memories WHERE pinned = 1").fetchone()[0],
        "unconsolidated": conn.execute(
            "SELECT COUNT(*) FROM v_consolidation_backlog"
        ).fetchone()[0],
        "entities": conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        "edges": conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
    }
