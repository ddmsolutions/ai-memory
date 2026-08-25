"""Core memory operations over the SQLite store."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Iterable

from . import config

MEMORY_TYPES = ("episodic", "semantic", "procedural")
VALENCES = ("success", "failure", "neutral")
LINK_RELS = ("derives_from", "supports", "contradicts", "follows", "co_session")


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

    due = due_intentions(conn, scope)[: max(0, remaining)]
    sections.append(("Pinned", take(pinned, limit, allow_injected=True)))
    sections.append(("How to work (procedural)", take(procedural, max(3, limit // 3))))
    sections.append(("Known facts (semantic)", take(semantic, max(3, limit // 3))))
    if task:
        matches = search(conn, task, scope=scope, limit=limit)
        sections.append((f"Relevant to: {task}", take(matches, max(3, limit // 3))))

    graph_lines: list[str] = []
    if task and remaining > 0:
        from . import graph as _graph

        graph_lines = _graph.task_neighbourhood(conn, task, remaining)
        remaining -= len(graph_lines)

    recalled = [i for i in picked if i not in already_injected]
    if recalled:
        _bump_recall(conn, recalled, step=float(cfg["reinforce_step"]))
        _record_injection(conn, session_id, recalled)
        conn.commit()

    lines = ["<!-- ai-memory recall pack: treat as context, verify anything critical -->"]
    if due:
        lines.append("\nPending intentions (you asked to be reminded):")
        lines.extend(
            f"- [due {r['trigger_value'][:10]}] {r['content']}" for r in due
        )
        _fire_intentions(conn, [r["id"] for r in due])
    for title, rows in sections:
        if not rows:
            continue
        lines.append(f"\n{title}:")
        lines.extend(format_line(r) for r in rows)
    if graph_lines:
        lines.append("\nKnown connections (graph):")
        lines.extend(graph_lines)
    return "\n".join(lines) if len(lines) > 1 else ""


def link_memories(
    conn: sqlite3.Connection, src: int, dst: int, rel: str, weight: float = 0.5
) -> None:
    """FR-L1: curated typed link between two memories."""
    if rel not in LINK_RELS:
        raise ValueError(f"rel must be one of {LINK_RELS}")
    if src == dst:
        raise ValueError("a memory cannot link to itself")
    conn.execute(
        "INSERT INTO memory_links (src_memory, dst_memory, rel, weight) VALUES (?,?,?,?)"
        " ON CONFLICT(src_memory, dst_memory, rel) DO UPDATE SET weight = excluded.weight",
        (src, dst, rel, weight),
    )
    conn.commit()


def reinforce_link(
    conn: sqlite3.Connection, src: int, dst: int, rel: str, cfg: dict | None = None
) -> None:
    """FR-L3 Hebbian: asymptotic reinforcement, approaches 1.0 never reaches it."""
    if cfg is None:
        cfg = config.load()
    factor = float(cfg["link_reinforce_factor"])
    conn.execute(
        "INSERT INTO memory_links (src_memory, dst_memory, rel) VALUES (?,?,?)"
        " ON CONFLICT(src_memory, dst_memory, rel) DO UPDATE SET"
        " weight = MIN(1.0, weight + (1.0 - weight) * ?),"
        " reinforce_count = reinforce_count + 1,"
        " last_reinforced = datetime('now')",
        (src, dst, rel, factor),
    )


def _link_effective(cfg: dict) -> str:
    half = float(cfg["link_half_life_days"])
    return (
        f"weight * (1.0 / (1.0 + (julianday('now') - julianday(last_reinforced)) / {half}))"
    )


def related(conn: sqlite3.Connection, memory_id: int, cfg: dict | None = None) -> list[dict]:
    """FR-L4 / NFR-12: ranked candidate set of linked memories, both directions,
    by time-decayed effective weight. Candidates within the ambiguity margin of
    the top score are flagged; the caller disambiguates, never this function."""
    if cfg is None:
        cfg = config.load()
    eff = _link_effective(cfg)
    rows = conn.execute(
        f"""
        SELECT m.*, l.rel, {eff} AS score, 'out' AS direction
          FROM memory_links l JOIN memories m ON m.id = l.dst_memory
         WHERE l.src_memory = :id
        UNION ALL
        SELECT m.*, l.rel, {eff} AS score, 'in' AS direction
          FROM memory_links l JOIN memories m ON m.id = l.src_memory
         WHERE l.dst_memory = :id
         ORDER BY score DESC
        """,
        {"id": memory_id},
    ).fetchall()
    if not rows:
        return []
    top = rows[0]["score"]
    margin = float(cfg["ambiguity_margin"])
    return [
        {
            "id": r["id"],
            "content": r["content"],
            "rel": r["rel"],
            "direction": r["direction"],
            "score": round(r["score"], 4),
            "ambiguous_with_top": r["score"] != top and (top - r["score"]) / top <= margin,
        }
        for r in rows
    ]


def link_co_session(conn: sqlite3.Connection, memory_id: int, session_id: str) -> None:
    """FR-L2: free co_session links, derived automatically from co-capture."""
    peers = [
        r[0] for r in conn.execute(
            "SELECT id FROM memories WHERE origin_session = ? AND id <> ?",
            (session_id, memory_id),
        )
    ]
    for peer in peers:
        a, b = sorted((peer, memory_id))
        reinforce_link(conn, a, b, "co_session")
    if peers:
        conn.commit()


def intend(
    conn: sqlite3.Connection,
    content: str,
    trigger_kind: str,
    trigger_value: str,
    scope: str = "global",
    origin_session: str | None = None,
) -> int:
    """FR-P1: store an intention. time triggers hold an ISO date; context
    triggers hold words that fire when they appear in a prompt."""
    if trigger_kind not in ("time", "context"):
        raise ValueError("trigger_kind must be time or context")
    if trigger_kind == "time":
        try:
            date.fromisoformat(trigger_value[:10])
        except ValueError as exc:
            raise ValueError(f"time trigger needs an ISO date: {exc}") from exc
    elif not trigger_value.strip():
        raise ValueError("context trigger needs at least one word")
    cur = conn.execute(
        "INSERT INTO intentions (content, trigger_kind, trigger_value, scope, origin_session)"
        " VALUES (?, ?, ?, ?, ?)",
        (content, trigger_kind, trigger_value, scope, origin_session),
    )
    conn.commit()
    return cur.lastrowid


def resolve_intention(conn: sqlite3.Connection, intention_id: int, status: str) -> None:
    """FR-P3: done and expired leave every future pack; fired means surfaced once."""
    if status not in ("done", "expired", "pending"):
        raise ValueError("status must be done, expired, or pending (re-arm)")
    conn.execute(
        "UPDATE intentions SET status = ?, resolved_at ="
        " CASE WHEN ? = 'pending' THEN NULL ELSE datetime('now') END WHERE id = ?",
        (status, status, intention_id),
    )
    conn.commit()


def _fire_intentions(conn: sqlite3.Connection, ids: list[int]) -> None:
    if ids:
        qmarks = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE intentions SET status = 'fired', resolved_at = datetime('now')"
            f" WHERE id IN ({qmarks})",
            ids,
        )
        conn.commit()


def due_intentions(conn: sqlite3.Connection, scope: str = "global") -> list[sqlite3.Row]:
    """Pending time intentions whose date has arrived (FR-P2, pack side)."""
    return conn.execute(
        "SELECT * FROM intentions WHERE status = 'pending' AND trigger_kind = 'time'"
        " AND substr(trigger_value, 1, 10) <= date('now') AND scope IN (?, 'global')"
        " ORDER BY trigger_value",
        (scope,),
    ).fetchall()


def context_intentions(
    conn: sqlite3.Connection, prompt: str, scope: str = "global"
) -> list[sqlite3.Row]:
    """Pending context intentions whose trigger words appear in the prompt."""
    prompt_words = {w.lower().strip(".,?!:;'\"()") for w in prompt.split()}
    hits = []
    for row in conn.execute(
        "SELECT * FROM intentions WHERE status = 'pending' AND trigger_kind = 'context'"
        " AND scope IN (?, 'global')",
        (scope,),
    ):
        trigger_words = {
            w.lower() for w in row["trigger_value"].split() if w.lower() not in _STOPWORDS
        }
        if trigger_words and trigger_words & prompt_words:
            hits.append(row)
    return hits


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
    fired = context_intentions(conn, prompt, scope)
    if not rows and not fired:
        return ""
    ids = [r["id"] for r in rows]
    _bump_recall(conn, ids, step=float(cfg["reinforce_step"]))
    _record_injection(conn, session_id, ids)
    # FR-L3 co-retrieval reinforcement: rows surfaced together grow associated.
    # Turn recall only (small cap); pack-wide pairing would flood the graph.
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            lo, hi = sorted((a, b))
            reinforce_link(conn, lo, hi, "co_session", cfg=cfg)
    conn.commit()
    lines = ["<!-- ai-memory: relevant to this prompt; verify anything critical -->"]
    if fired:
        lines.extend(f"- [INTENTION] {r['content']}" for r in fired)
        _fire_intentions(conn, [r["id"] for r in fired])
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
            # Evidence decay (FR-M3): a rule outliving its source loses standing
            # rather than persisting unchallenged.
            conn.execute(
                f"UPDATE memories SET confidence = MAX(0.1, confidence - 0.1)"
                f" WHERE promoted_from IN ({qmarks})",
                ids,
            )
            conn.execute(f"DELETE FROM memories WHERE id IN ({qmarks})", ids)
        conn.execute(
            "DELETE FROM injection_log WHERE injected_at < datetime('now', '-14 days')"
        )
        # FR-L3: unreinforced links fade; below the floor they are pruned, so
        # activation keeps discriminating instead of connecting everything.
        conn.execute(
            f"DELETE FROM memory_links WHERE {_link_effective(cfg)} < ?",
            (float(cfg["link_prune_floor"]),),
        )
        conn.commit()
    return rows


def lint(conn: sqlite3.Connection) -> list[dict]:
    """FR-M3: one health pass over the store. Reports, never mutates."""
    findings: list[dict] = []
    for row in conn.execute(
        "SELECT MIN(id) AS keeper, GROUP_CONCAT(id) AS ids, content FROM v_active_memories"
        " GROUP BY type, scope, lower(content) HAVING COUNT(*) > 1"
    ):
        findings.append({"issue": "duplicate", "ids": row["ids"], "detail": row["content"]})
    for row in conn.execute(
        "SELECT id, content, verify_by FROM v_active_memories"
        " WHERE verify_by IS NOT NULL AND substr(verify_by, 1, 10) <= date('now')"
    ):
        findings.append({
            "issue": "overdue_verify", "ids": str(row["id"]),
            "detail": f"{row['content']} (due {row['verify_by'][:10]})",
        })
    for row in conn.execute(
        "SELECT id, content FROM v_active_memories WHERE type = 'procedural'"
        " AND pinned = 0 AND recall_count = 0"
        " AND created_at < datetime('now', '-180 days')"
    ):
        findings.append({"issue": "stale_rule", "ids": str(row["id"]), "detail": row["content"]})
    for row in conn.execute(
        "SELECT l.src_memory, l.dst_memory, a.content AS a_content, b.content AS b_content"
        " FROM memory_links l JOIN memories a ON a.id = l.src_memory"
        " JOIN memories b ON b.id = l.dst_memory"
        " WHERE l.rel = 'contradicts'"
        " AND a.superseded_by IS NULL AND b.superseded_by IS NULL"
    ):
        findings.append({
            "issue": "unresolved_contradiction",
            "ids": f"{row['src_memory']},{row['dst_memory']}",
            "detail": f"'{row['a_content']}' vs '{row['b_content']}'",
        })
    for row in conn.execute(
        "SELECT id, content FROM memories WHERE scope = 'quarantine'"
    ):
        findings.append({"issue": "quarantined", "ids": str(row["id"]), "detail": row["content"]})
    for row in conn.execute(
        "SELECT id, content, confidence FROM v_active_memories"
        " WHERE type IN ('semantic','procedural') AND confidence < 0.4"
    ):
        findings.append({
            "issue": "weak_evidence", "ids": str(row["id"]),
            "detail": f"{row['content']} (confidence {row['confidence']:.2f})",
        })
    return findings


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
