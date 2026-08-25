"""Core memory operations over the SQLite store."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Iterable

from . import config

MEMORY_TYPES = ("episodic", "semantic", "procedural")
VALENCES = ("success", "failure", "neutral")


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 OR-query of quoted terms."""
    terms = [t.replace('"', '""') for t in text.split() if t.strip('"')]
    return " OR ".join(f'"{t}"' for t in terms) or '""'


_STOPWORDS = frozenset(
    "the a an and or but is are was were be been being to of in on at for with by from as it its "
    "this that these those i you we they he she them us me my your our their do does did done "
    "what which who whom how when where why not no yes so if then than there here just can could "
    "should would will shall may might must have has had having about into over under again very "
    "please want need make made get got let new way best good".split()
)


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
    valence: str | None = None,
    verify_by: str | None = None,
) -> int:
    if mtype not in MEMORY_TYPES:
        raise ValueError(f"type must be one of {MEMORY_TYPES}")
    if valence is not None and valence not in VALENCES:
        raise ValueError(f"valence must be one of {VALENCES}")
    # FR-C6: store.remember is the single insert funnel, so redaction here
    # covers every capture path (hooks, CLI, callers).
    from . import redact as _redact

    content = _redact.redact(content, config.load().get("secret_patterns"))[0]
    cur = conn.execute(
        "INSERT INTO memories (type, scope, content, origin_session, promoted_from,"
        " confidence, pinned, valence, verify_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mtype, scope, content, origin_session, promoted_from, confidence,
         int(pinned), valence, verify_by),
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


def _eviction_order(cfg: dict) -> str:
    """FR-R2: confidence x recency decay x usage saturation, as a SQL expression."""
    half = float(cfg["recency_half_life_days"])
    sat = float(cfg["usage_saturation"])
    return (
        f"confidence * (1.0 / (1.0 + (julianday('now') - julianday(created_at)) / {half}))"
        f" * (1.0 + CAST(recall_count AS REAL) / (recall_count + {sat}))"
    )


def _injected_ids(conn: sqlite3.Connection, session_id: str) -> set[int]:
    return {
        r[0] for r in conn.execute(
            "SELECT memory_id FROM injection_log WHERE session_id = ?", (session_id,)
        )
    }


def _record_injection(conn: sqlite3.Connection, session_id: str | None, ids: list[int]) -> None:
    if not session_id or not ids:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO injection_log (session_id, memory_id) VALUES (?, ?)",
        [(session_id, i) for i in ids],
    )


def format_line(row: sqlite3.Row) -> str:
    """One recall line: dated (FR-R11), with a verify warning past verify_by (FR-A2)."""
    line = f"- [{row['created_at'][:10]}] {row['content']}"
    try:
        verify_by = row["verify_by"]
    except (IndexError, KeyError):
        verify_by = None
    if verify_by and verify_by[:10] <= date.today().isoformat():
        line += f" (VERIFY: unconfirmed since {verify_by[:10]})"
    return line


def _bump_recall(conn: sqlite3.Connection, ids: list[int], step: float = 0.0) -> None:
    """Count the recall and (FR-K8) reinforce: confidence rises by step, capped at 1.0."""
    if not ids:
        return
    qmarks = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE memories SET recall_count = recall_count + 1,"
        f" last_recalled_at = datetime('now'),"
        f" confidence = MIN(1.0, confidence + ?) WHERE id IN ({qmarks})",
        [float(step), *ids],
    )


def recall_pack(
    conn: sqlite3.Connection,
    task: str | None = None,
    scope: str = "global",
    limit: int | None = None,
    cfg: dict | None = None,
    session_id: str | None = None,
) -> str:
    """Compile a compact markdown recall pack for injection at session start.

    Layers, in priority order: pinned memories, procedural lessons,
    semantic facts, then task-relevant matches if a task is given.
    With a session_id, injected rows are logged so turn-time recall
    never re-injects them.
    """
    if cfg is None:
        cfg = config.load()
    if limit is None:
        limit = int(cfg["pack_limit"])
    already_injected = _injected_ids(conn, session_id) if session_id else set()
    picked: set[int] = set()
    remaining = limit
    sections: list[tuple[str, list[sqlite3.Row]]] = []

    def take(rows: Iterable[sqlite3.Row], n: int, allow_injected: bool = False) -> list[sqlite3.Row]:
        # NFR-4: `limit` is the TOTAL pack budget, decremented across sections.
        # Pinned rows lead every pack (FR-K5), even on a resumed session.
        nonlocal remaining
        out: list[sqlite3.Row] = []
        for r in rows:
            if remaining <= 0 or len(out) >= n:
                break
            if r["id"] in picked:
                continue
            if not allow_injected and r["id"] in already_injected:
                continue
            picked.add(r["id"])
            out.append(r)
            remaining -= 1
        return out

    base = "scope IN (?, 'global')"
    score = _eviction_order(cfg)
    pinned = conn.execute(
        f"SELECT * FROM v_active_memories WHERE pinned = 1 AND {base} ORDER BY created_at DESC",
        (scope,),
    ).fetchall()
    procedural = conn.execute(
        f"SELECT * FROM v_active_memories WHERE type = 'procedural' AND {base}"
        f" ORDER BY {score} DESC, created_at DESC",
        (scope,),
    ).fetchall()
    semantic = conn.execute(
        f"SELECT * FROM v_active_memories WHERE type = 'semantic' AND {base}"
        f" ORDER BY {score} DESC, created_at DESC",
        (scope,),
    ).fetchall()

    sections.append(("Pinned", take(pinned, limit, allow_injected=True)))
    sections.append(("How to work (procedural)", take(procedural, max(3, limit // 3))))
    sections.append(("Known facts (semantic)", take(semantic, max(3, limit // 3))))
    if task:
        matches = search(conn, task, scope=scope, limit=limit)
        sections.append((f"Relevant to: {task}", take(matches, max(3, limit // 3))))

    recalled = [i for i in picked if i not in already_injected]
    if recalled:
        _bump_recall(conn, recalled, step=float(cfg["reinforce_step"]))
        _record_injection(conn, session_id, recalled)
        conn.commit()

    lines = ["<!-- ai-memory recall pack: treat as context, verify anything critical -->"]
    for title, rows in sections:
        if not rows:
            continue
        lines.append(f"\n{title}:")
        lines.extend(format_line(r) for r in rows)
    return "\n".join(lines) if len(lines) > 1 else ""


def turn_recall(
    conn: sqlite3.Connection,
    prompt: str,
    session_id: str | None = None,
    scope: str = "global",
    cfg: dict | None = None,
) -> str:
    """FR-R5/R6: top task-relevant active memories for THIS prompt, config-capped,
    excluding anything already injected this session. Silent when nothing matches."""
    if cfg is None:
        cfg = config.load()
    cap = int(cfg["turn_recall_cap"])
    if not prompt or not prompt.strip() or cap <= 0:
        return ""
    # Stopwords out: without this, "the" matches nearly every memory, and the
    # reinforcement loop then exempts everything from decay (review finding).
    terms = [
        w for w in prompt.split()[:64]
        if w.lower().strip(".,?!:;'\"()") not in _STOPWORDS
    ][:32]
    if not terms:
        return ""
    rows = search(conn, " ".join(terms), scope=scope, limit=cap * 4)
    # FR-R6: configurable relevance threshold on the bm25 score (higher = stricter).
    min_score = float(cfg["turn_recall_min_score"])
    if min_score > 0:
        rows = [r for r in rows if -r["rank"] >= min_score]
    if session_id:
        injected = _injected_ids(conn, session_id)
        rows = [r for r in rows if r["id"] not in injected]
    rows = rows[:cap]
    if not rows:
        return ""
    ids = [r["id"] for r in rows]
    _bump_recall(conn, ids, step=float(cfg["reinforce_step"]))
    _record_injection(conn, session_id, ids)
    conn.commit()
    lines = ["<!-- ai-memory: relevant to this prompt; verify anything critical -->"]
    lines.extend(format_line(r) for r in rows)
    return "\n".join(lines)


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


def decay(conn: sqlite3.Connection, cfg: dict | None = None, dry_run: bool = False) -> list[sqlite3.Row]:
    """FR-K7: delete episodic rows that are ALL of: older than the configured
    window, never promoted, never recalled, not pinned. Returns the affected
    rows; with dry_run they are listed but kept."""
    if cfg is None:
        cfg = config.load()
    window = int(cfg["decay_window_days"])
    rows = conn.execute(
        "SELECT * FROM memories WHERE type = 'episodic' AND consolidated = 0"
        " AND recall_count = 0 AND pinned = 0 AND created_at < datetime('now', ?)"
        # Never delete a row that supersedes another: ON DELETE SET NULL would
        # resurrect the corrected fact as current truth (review blocker #1).
        " AND id NOT IN (SELECT superseded_by FROM memories WHERE superseded_by IS NOT NULL)"
        " ORDER BY created_at",
        (f"-{window} days",),
    ).fetchall()
    if not dry_run:
        if rows:
            ids = [r["id"] for r in rows]
            qmarks = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM memories WHERE id IN ({qmarks})", ids)
        conn.execute(
            "DELETE FROM injection_log WHERE injected_at < datetime('now', '-14 days')"
        )
        conn.commit()
    return rows


def why(conn: sqlite3.Connection, memory_id: int) -> str:
    """FR-M1: a memory's full story - origin, promotion lineage, corrections,
    usage, mentions - rendered as markdown from the lineage columns."""
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        return f"No memory with id {memory_id}."
    lines = [f"**#{row['id']}** [{row['type']}/{row['scope']}] {row['content']}"]
    facts = [f"recorded {row['created_at']}", f"confidence {row['confidence']:.2f}"]
    if row["pinned"]:
        facts.append("pinned")
    if row["valence"]:
        facts.append(f"valence {row['valence']}")
    if row["verify_by"]:
        facts.append(f"verify by {row['verify_by']}")
    lines.append("- " + ", ".join(facts))
    if row["origin_session"]:
        lines.append(f"- captured from session {row['origin_session']}")
    ancestor = row
    depth = 0
    while ancestor["promoted_from"] is not None and depth < 20:
        ancestor = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (ancestor["promoted_from"],)
        ).fetchone()
        if ancestor is None:
            lines.append("- promoted from a memory that has since been deleted")
            break
        depth += 1
        lines.append(f"- distilled from #{ancestor['id']} ({ancestor['created_at'][:10]}): {ancestor['content']}")
    for child in conn.execute(
        "SELECT id, type, content FROM memories WHERE promoted_from = ?", (memory_id,)
    ):
        lines.append(f"- promoted into #{child['id']} [{child['type']}]: {child['content']}")
    for old in conn.execute(
        "SELECT id, content FROM memories WHERE superseded_by = ?", (memory_id,)
    ):
        lines.append(f"- corrects #{old['id']}: {old['content']}")
    if row["superseded_by"] is not None:
        new = conn.execute(
            "SELECT id, content FROM memories WHERE id = ?", (row["superseded_by"],)
        ).fetchone()
        if new:
            lines.append(f"- SUPERSEDED by #{new['id']}: {new['content']}")
    mentions = conn.execute(
        "SELECT e.name, e.etype FROM memory_entities me JOIN entities e ON e.id = me.entity_id"
        " WHERE me.memory_id = ?", (memory_id,),
    ).fetchall()
    if mentions:
        lines.append("- mentions: " + ", ".join(f"{m['name']} ({m['etype']})" for m in mentions))
    lines.append(
        f"- recalled {row['recall_count']} time(s)"
        + (f", last {row['last_recalled_at']}" if row["last_recalled_at"] else ", never")
    )
    return "\n".join(lines)


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
