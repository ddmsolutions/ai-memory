"""Core memory operations over the SQLite store."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Iterable

from . import config

MEMORY_TYPES = ("episodic", "semantic", "procedural")
VALENCES = ("success", "failure", "neutral")
LINK_RELS = ("derives_from", "supports", "contradicts", "follows", "co_session")

# #64: origin trust, lowest first. Bound at write time; no machine path may
# raise it (Biba integrity) - only the human `trust` command elevates.
ORIGINS = ("external", "agent", "owner")


def _least_trusted(*origins: str) -> str:
    """The minimum trust level among the given origins."""
    return min(origins, key=ORIGINS.index)


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
    line_hash: str | None = None,
    origin: str = "agent",
) -> int:
    if mtype not in MEMORY_TYPES:
        raise ValueError(f"type must be one of {MEMORY_TYPES}")
    if valence is not None and valence not in VALENCES:
        raise ValueError(f"valence must be one of {VALENCES}")
    if origin not in ORIGINS:
        raise ValueError(f"origin must be one of {ORIGINS}")
    # FR-C6: store.remember is the single insert funnel, so redaction here
    # covers every capture path (hooks, CLI, callers).
    from . import redact as _redact

    content = _redact.redact(content, config.load().get("secret_patterns"))[0]
    # #74 idempotence: a caller-supplied content hash makes the insert
    # re-runnable; replaying a transcript or double-firing a hook returns
    # the existing row instead of duplicating it.
    if line_hash is not None:
        existing = conn.execute(
            "SELECT id FROM memories WHERE line_hash = ?", (line_hash,)
        ).fetchone()
        if existing:
            return existing["id"]
    try:
        cur = conn.execute(
            "INSERT INTO memories (type, scope, content, origin_session, promoted_from,"
            " confidence, pinned, valence, verify_by, line_hash, origin)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mtype, scope, content, origin_session, promoted_from, confidence,
             int(pinned), valence, verify_by, line_hash, origin),
        )
    except sqlite3.IntegrityError:
        # Concurrent hooks (Stop + SubagentStop) can race the pre-check;
        # the partial unique index is the arbiter.
        row = conn.execute(
            "SELECT id FROM memories WHERE line_hash = ?", (line_hash,)
        ).fetchone()
        if row:
            return row["id"]
        raise
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
    cfg: dict | None = None,
    preferred_scope: str | None = None,
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
    params.append(limit * 3)
    fts_rows = conn.execute(sql, params).fetchall()
    if cfg is None:
        return fts_rows[:limit]

    # #59: Reciprocal Rank Fusion of the bm25 and cosine orderings. Scale-free
    # (no score normalisation needed), one tunable blend weight, and weight 0
    # or a dead backend degrades to pure bm25 (fail-soft).
    K = 60.0
    weight = 0.0
    sem_pairs: list[tuple[int, float]] = []
    if cfg.get("embed_enabled"):
        weight = max(0.0, min(1.0, float(cfg.get("hybrid_semantic_weight", 0.0))))
        if weight > 0:
            try:
                from . import embeddings

                sem_pairs = embeddings.semantic_candidates(conn, query, cfg, limit * 3)
            except Exception:
                sem_pairs = []
    rows_by_id: dict[int, sqlite3.Row] = {}
    scores: dict[int, float] = {}
    for i, r in enumerate(fts_rows):
        rows_by_id[r["id"]] = r
        scores[r["id"]] = (1.0 - weight) * (1.0 / (K + i + 1))
    for j, (memory_id, sim) in enumerate(sem_pairs):
        if memory_id not in rows_by_id:
            # rank sentinel: -(1 + cosine) so -rank stays positive and
            # semantic hits survive a turn_recall_min_score threshold.
            extra_sql = (
                f"SELECT *, {-(1.0 + float(sim))} AS rank FROM "
                + ("memories" if include_superseded else "v_active_memories")
                + " WHERE id = ?"
            )
            extra_params: list = [memory_id]
            if mtype:
                extra_sql += " AND type = ?"
                extra_params.append(mtype)
            if scope:
                extra_sql += " AND scope IN (?, 'global')"
                extra_params.append(scope)
            extra = conn.execute(extra_sql, extra_params).fetchone()
            if extra is None:
                continue
            rows_by_id[memory_id] = extra
        scores[memory_id] = scores.get(memory_id, 0.0) + weight * (1.0 / (K + j + 1))

    # #60: soft scope relevance. Without an explicit scope filter, rows outside
    # the preferred scope and global are down-weighted, never removed.
    penalty = max(0.0, min(1.0, float(cfg.get("foreign_scope_penalty", 1.0))))
    if preferred_scope and scope is None and penalty < 1.0:
        for memory_id, r in rows_by_id.items():
            if r["scope"] not in (preferred_scope, "global"):
                scores[memory_id] *= penalty

    ordered = sorted(rows_by_id.values(), key=lambda r: -scores[r["id"]])
    return ordered[:limit]


def _eviction_order(cfg: dict) -> str:
    """FR-R2: confidence x recency decay x usage saturation x origin trust,
    as a SQL expression. #64: lower-trust rows rank lower, never vanish."""
    half = float(cfg["recency_half_life_days"])
    sat = float(cfg["usage_saturation"])
    w_agent = float(cfg.get("origin_weight_agent", 0.9))
    w_external = float(cfg.get("origin_weight_external", 0.5))
    return (
        f"confidence * (1.0 / (1.0 + (julianday('now') - julianday(created_at)) / {half}))"
        f" * (1.0 + CAST(recall_count AS REAL) / (recall_count + {sat}))"
        f" * (CASE origin WHEN 'owner' THEN 1.0 WHEN 'external' THEN {w_external}"
        f" ELSE {w_agent} END)"
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
    """One recall line: dated (FR-R11), with a verify warning past verify_by
    (FR-A2) and an untrusted-source marker on external rows (#64)."""
    line = f"- [{row['created_at'][:10]}] {row['content']}"
    try:
        verify_by = row["verify_by"]
    except (IndexError, KeyError):
        verify_by = None
    if verify_by and verify_by[:10] <= date.today().isoformat():
        line += f" (VERIFY: unconfirmed since {verify_by[:10]})"
    try:
        origin = row["origin"]
    except (IndexError, KeyError):
        origin = None
    if origin == "external":
        line += " (EXTERNAL SOURCE: weigh accordingly)"
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

    handoffs = open_handoffs(conn, scope)[: max(0, remaining)]
    remaining -= len(handoffs)  # handoffs and intentions consume the total budget (NFR-4)
    due = due_intentions(conn, scope)[: max(0, remaining)]
    remaining -= len(due)
    sections.append(("Pinned", take(pinned, limit, allow_injected=True)))
    sections.append(("How to work (procedural)", take(procedural, max(3, limit // 3))))
    sections.append(("Known facts (semantic)", take(semantic, max(3, limit // 3))))
    if task:
        matches = search(conn, task, scope=scope, limit=limit, cfg=cfg)
        sections.append((f"Relevant to: {task}", take(matches, max(3, limit // 3))))

    graph_lines: list[str] = []
    if task and remaining > 0:
        from . import graph as _graph

        graph_lines = _graph.task_neighbourhood(conn, task, remaining)
        remaining -= len(graph_lines)

    # Sessionless compiles (CLI preview, spawn injection) must not distort
    # reinforcement or decay exemption: counters move only for real sessions.
    recalled = [i for i in picked if i not in already_injected] if session_id else []
    if recalled:
        _bump_recall(conn, recalled, step=float(cfg["reinforce_step"]))
        _record_injection(conn, session_id, recalled)
        _log_trace(conn, session_id, "pack", task, [{"id": i} for i in sorted(picked)], recalled)
        conn.commit()

    lines = ["<!-- ai-memory recall pack: treat as context, verify anything critical -->"]
    if handoffs:
        lines.append("\nHandoff from your previous session (one-time, now consumed):")
        lines.extend(f"- [{h['created_at'][:10]}] {h['content']}" for h in handoffs)
        if session_id:
            _consume_handoffs(conn, [h["id"] for h in handoffs], session_id)
    if due:
        lines.append("\nPending intentions (you asked to be reminded):")
        lines.extend(
            f"- [due {r['trigger_value'][:10]}] {r['content']}" for r in due
        )
        # Fire only for a real session: a CLI preview or subagent spawn must
        # not consume a reminder the user never saw (review finding).
        if session_id:
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


def _log_trace(
    conn: sqlite3.Connection,
    session_id: str | None,
    surface: str,
    cue: str | None,
    candidates: list[dict],
    injected: list[int],
) -> None:
    """FR-M4: record what retrieval considered and what it injected (ids and
    scores only, never content). Sessionless compiles are previews: no trace."""
    if not session_id or not injected:
        return
    import json as _json

    conn.execute(
        "INSERT INTO recall_trace (session_id, surface, cue, candidates, injected)"
        " VALUES (?, ?, ?, ?, ?)",
        (session_id, surface, cue, _json.dumps(candidates), _json.dumps(injected)),
    )


def feedback(
    conn: sqlite3.Connection,
    trace_id: int,
    useful: bool,
    note: str | None = None,
    cfg: dict | None = None,
) -> dict:
    """FR-M4: judge a trace. A rejection applies real penalties: the injected
    rows lose confidence and their co_session links lose weight, because
    reinforce-only systems drift toward plausible nonsense."""
    if cfg is None:
        cfg = config.load()
    import json as _json

    trace = conn.execute("SELECT * FROM recall_trace WHERE id = ?", (trace_id,)).fetchone()
    if trace is None:
        raise ValueError(f"no trace with id {trace_id}")
    conn.execute(
        "UPDATE recall_trace SET was_useful = ?, feedback_note = ? WHERE id = ?",
        (int(useful), note, trace_id),
    )
    penalised = 0
    if not useful:
        ids = _json.loads(trace["injected"])
        if ids:
            qmarks = ",".join("?" * len(ids))
            penalised = conn.execute(
                f"UPDATE memories SET confidence = MAX(0.1, confidence - ?)"
                f" WHERE id IN ({qmarks})",
                [float(cfg["feedback_penalty"]), *ids],
            ).rowcount
            factor = float(cfg["link_reinforce_factor"])
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    lo, hi = sorted((a, b))
                    conn.execute(
                        "UPDATE memory_links SET weight = MAX(0.01, weight * (1.0 - 2.0 * ?))"
                        " WHERE src_memory = ? AND dst_memory = ? AND rel = 'co_session'",
                        (factor, lo, hi),
                    )
    conn.commit()
    return {"trace": trace_id, "useful": useful, "penalised_memories": penalised}


def handoff_write(
    conn: sqlite3.Connection,
    content: str,
    scope: str = "global",
    origin_session: str | None = None,
) -> int:
    """UC-35: state of play for the next session. Same funnel rules as every
    other injectable surface: redacted, and instruction-shaped content refused."""
    from . import redact as _redact

    _cfg = config.load()
    content = _redact.redact(content, _cfg.get("secret_patterns"))[0]
    if _redact.screen_instructions(content, _cfg.get("instruction_patterns")):
        raise ValueError("instruction-shaped content refused for a handoff")
    existing = conn.execute(
        "SELECT id FROM handoffs WHERE content = ? AND consumed_at IS NULL", (content,)
    ).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO handoffs (content, scope, origin_session) VALUES (?, ?, ?)",
        (content, scope, origin_session),
    )
    conn.commit()
    return cur.lastrowid


def open_handoffs(conn: sqlite3.Connection, scope: str = "global") -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM handoffs WHERE consumed_at IS NULL AND scope IN (?, 'global')"
        " ORDER BY created_at",
        (scope,),
    ).fetchall()


def _consume_handoffs(conn: sqlite3.Connection, ids: list[int], session_id: str) -> None:
    if ids:
        qmarks = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE handoffs SET consumed_at = datetime('now'), consumed_by = ?"
            f" WHERE id IN ({qmarks})",
            [session_id, *ids],
        )
        conn.commit()


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
          FROM memory_links l JOIN v_active_memories m ON m.id = l.dst_memory
         WHERE l.src_memory = :id
        UNION ALL
        SELECT m.*, l.rel, {eff} AS score, 'in' AS direction
          FROM memory_links l JOIN v_active_memories m ON m.id = l.src_memory
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
    # Review fix: intend is a second insert funnel and injects verbatim into
    # packs; it gets the same redaction and instruction screen as remember.
    from . import redact as _redact

    _cfg = config.load()
    content = _redact.redact(content, _cfg.get("secret_patterns"))[0]
    if _redact.screen_instructions(content, _cfg.get("instruction_patterns")):
        raise ValueError("instruction-shaped content refused for an intention")
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
    rows = search(conn, " ".join(terms), scope=scope, limit=cap * 4, cfg=cfg)
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
    _log_trace(
        conn, session_id, "turn", " ".join(terms),
        [{"id": r["id"], "rank": round(-r["rank"], 4)} for r in rows], ids,
    )
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


def _penalise_children(conn: sqlite3.Connection, ids: list[int]) -> None:
    """Evidence decay (FR-M3): rules outliving their deleted source lose standing."""
    if not ids:
        return
    qmarks = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE memories SET confidence = MAX(0.1, confidence - 0.1)"
        f" WHERE promoted_from IN ({qmarks})",
        ids,
    )


def forget(conn: sqlite3.Connection, memory_id: int) -> None:
    _penalise_children(conn, [memory_id])
    conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.commit()


def set_pin(conn: sqlite3.Connection, memory_id: int, pinned: bool) -> None:
    conn.execute("UPDATE memories SET pinned = ? WHERE id = ?", (int(pinned), memory_id))
    conn.commit()


def contamination_set(conn: sqlite3.Connection, memory_id: int) -> list[int]:
    """#65: the transitive closure of everything derived from a memory -
    promotion children (promoted_from) and derivation links (derives_from),
    followed recursively. The root is included."""
    seen: set[int] = set()
    frontier = [memory_id]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        for row in conn.execute(
            "SELECT id FROM memories WHERE promoted_from = ?", (current,)
        ):
            frontier.append(row["id"])
        # src derives_from dst: src was derived from dst, so contamination
        # flows dst -> src.
        for row in conn.execute(
            "SELECT src_memory FROM memory_links WHERE dst_memory = ?"
            " AND rel = 'derives_from'",
            (current,),
        ):
            frontier.append(row["src_memory"])
    return sorted(seen)


def quarantine_cascade(
    conn: sqlite3.Connection, memory_id: int, dry_run: bool = False
) -> dict:
    """#65 safety-triggered forgetting: quarantine a memory AND everything
    promoted or derived from it, and suspend machine-sourced graph edges whose
    entire evidence set is contaminated. Nothing is deleted: quarantine is
    reviewable (policy release / policy hostile), deletion is the human's call.
    """
    if conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,)).fetchone() is None:
        raise ValueError(f"no memory with id {memory_id}")
    ids = contamination_set(conn, memory_id)
    pinned = [
        r["id"] for r in conn.execute(
            f"SELECT id FROM memories WHERE pinned = 1 AND id IN"
            f" ({','.join('?' * len(ids))})", ids)
    ]
    report = {"memories": ids, "pinned_included": pinned, "dry_run": dry_run,
              "edges_suspended": 0}
    if dry_run:
        return report
    qmarks = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE memories SET scope = 'quarantine' WHERE id IN ({qmarks})", ids
    )
    from . import graph as _graph

    report["edges_suspended"] = _graph.suspend_edges_for_memories(conn, ids)
    conn.commit()
    return report


def set_trust(conn: sqlite3.Connection, memory_id: int, origin: str) -> dict:
    """#64: the ONLY elevation path, and it is human-invoked (running the
    command is the approval, like `policy adopt`). Downgrades are always
    allowed; machine callers must never route through this function."""
    if origin not in ORIGINS:
        raise ValueError(f"origin must be one of {ORIGINS}")
    row = conn.execute("SELECT origin FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        raise ValueError(f"no memory with id {memory_id}")
    before = row["origin"]
    conn.execute("UPDATE memories SET origin = ? WHERE id = ?", (origin, memory_id))
    conn.commit()
    return {"id": memory_id, "before": before, "after": origin}


def corroborated_external(conn: sqlite3.Connection) -> list[dict]:
    """#64: external rows whose content also arrived from a different session.
    The machine SUGGESTS elevation; the human `trust` command performs it."""
    out: list[dict] = []
    for row in conn.execute(
        "SELECT id, content, origin_session FROM v_active_memories WHERE origin = 'external'"
    ):
        peer = conn.execute(
            "SELECT id FROM v_active_memories WHERE lower(content) = lower(?)"
            " AND id <> ? AND origin_session IS NOT ?",
            (row["content"], row["id"], row["origin_session"]),
        ).fetchone()
        if peer:
            out.append({"id": row["id"], "peer": peer["id"], "content": row["content"]})
    return out


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
    # #64 Biba non-elevation: the distilled row inherits its source's origin.
    # A rewrite cannot launder external content into owner-trusted memory;
    # elevation is the human `trust` command only.
    try:
        source_origin = row["origin"]
    except (IndexError, KeyError):
        source_origin = "agent"
    new_id = remember(
        conn,
        content or row["content"],
        mtype=mtype,
        scope=row["scope"],
        promoted_from=memory_id,
        confidence=min(1.0, row["confidence"] + 0.1),
        origin=source_origin,
    )
    # FR-N1: the distilled row inherits its parent's entity mentions. Only memo
    # capture writes mentions, and distillation output carries no entities:
    # line, so without this every promoted fact drops out of the entity graph.
    conn.execute(
        "INSERT OR IGNORE INTO memory_entities (memory_id, entity_id, role, confidence)"
        " SELECT ?, entity_id, role, confidence FROM memory_entities WHERE memory_id = ?",
        (new_id, memory_id),
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
            _penalise_children(conn, ids)
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
        conn.execute(
            "DELETE FROM recall_trace WHERE created_at < datetime('now', ?)",
            (f"-{int(cfg['trace_retention_days'])} days",),
        )
        # Consumed handoffs served their one reader; stale unconsumed ones
        # would mislead a future session about the state of play.
        conn.execute("DELETE FROM handoffs WHERE consumed_at IS NOT NULL")
        conn.execute(
            "DELETE FROM handoffs WHERE consumed_at IS NULL AND created_at < datetime('now', ?)",
            (f"-{int(cfg['handoff_ttl_days'])} days",),
        )
        conn.commit()
    return rows


def _significant_terms(text: str) -> set[str]:
    return {
        w.lower().strip(".,?!:;'\"()")
        for w in text.split()
        if len(w) > 2 and w.lower().strip(".,?!:;'\"()") not in _STOPWORDS
    }


def detect_reexplanations(conn: sqlite3.Connection, days: int = 7, overlap: float = 0.6) -> list[dict]:
    """FR-SL2: a freshly captured memo that near-duplicates an older active
    memory means recall failed, the user re-explained something the store knew."""
    found: list[dict] = []
    recent = conn.execute(
        "SELECT * FROM v_active_memories WHERE type = 'episodic'"
        " AND origin_session IS NOT NULL AND created_at >= datetime('now', ?)",
        (f"-{int(days)} days",),
    ).fetchall()
    for row in recent:
        terms = _significant_terms(row["content"])
        if len(terms) < 3:
            continue
        for match in search(conn, " ".join(sorted(terms)), limit=5):
            if match["id"] == row["id"] or match["created_at"] >= row["created_at"]:
                continue
            if match["origin_session"] == row["origin_session"] and match["origin_session"]:
                continue
            match_terms = _significant_terms(match["content"])
            if not match_terms:
                continue
            ratio = len(terms & match_terms) / len(terms)
            if ratio >= overlap:
                found.append({
                    "new_id": row["id"], "old_id": match["id"],
                    "new_content": row["content"], "old_content": match["content"],
                    "overlap": round(ratio, 2),
                })
                break
    return found


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
        findings.append({
            "issue": "quarantined", "ids": str(row["id"]),
            # Never reprint hostile content in full: truncated and labelled.
            "detail": f"[UNTRUSTED, truncated] {row['content'][:80]}",
        })
    last_capture = conn.execute(
        "SELECT MAX(created_at) FROM memories WHERE origin_session IS NOT NULL"
    ).fetchone()[0]
    quiet_days = (
        conn.execute("SELECT julianday('now') - julianday(?)", (last_capture,)).fetchone()[0]
        if last_capture else None
    )
    for pair in detect_reexplanations(conn):
        findings.append({
            "issue": "re_explained",
            "ids": f"{pair['new_id']},{pair['old_id']}",
            "detail": f"recall failure: '{pair['new_content'][:60]}' re-explains #{pair['old_id']}",
        })
    if quiet_days is None or quiet_days > 7:
        detail = (
            f"no hook-captured memo for {quiet_days:.0f} days" if quiet_days
            else "no hook-captured memo EVER"
        )
        findings.append({
            "issue": "no_capture", "ids": "-",
            "detail": detail + " - capture may be silently dead (fail-soft hides breakage)",
        })
    for row in conn.execute(
        "SELECT id, content, confidence FROM v_active_memories"
        " WHERE type IN ('semantic','procedural') AND confidence < 0.4"
    ):
        findings.append({
            "issue": "weak_evidence", "ids": str(row["id"]),
            "detail": f"{row['content']} (confidence {row['confidence']:.2f})",
        })
    for pair in corroborated_external(conn):
        findings.append({
            "issue": "corroborated_external",
            "ids": f"{pair['id']},{pair['peer']}",
            "detail": f"independently corroborated; consider: trust {pair['id']}"
                      f" --origin agent ({pair['content'][:60]})",
        })
    # #70: type governance. Unregistered or retired types are warnings (the
    # default mode is permissive); endpoint violations mean the edge claims a
    # relationship its own registration forbids.
    from . import graph as _graph

    for row in conn.execute(
        "SELECT etype, COUNT(*) AS n, GROUP_CONCAT(id) AS ids FROM entities"
        " WHERE status = 'active' AND etype NOT IN"
        " (SELECT name FROM graph_types WHERE kind = 'entity' AND status = 'active')"
        " GROUP BY etype"
    ):
        findings.append({
            "issue": "unregistered_type", "ids": row["ids"],
            "detail": f"entity type '{row['etype']}' not in the registry"
                      f" ({row['n']} rows); register or rename (entity type add)",
        })
    for row in conn.execute(
        "SELECT rel, COUNT(*) AS n, GROUP_CONCAT(id) AS ids FROM edges"
        " WHERE status = 'active' AND rel NOT IN"
        " (SELECT name FROM graph_types WHERE kind = 'edge' AND status = 'active')"
        " GROUP BY rel"
    ):
        findings.append({
            "issue": "unregistered_type", "ids": row["ids"],
            "detail": f"edge type '{row['rel']}' not in the registry"
                      f" ({row['n']} rows); register or rename (entity type add)",
        })
    for row in conn.execute(
        "SELECT e.id, e.rel, gt.src_types, gt.dst_types,"
        " s.name AS src_name, s.etype AS src_etype,"
        " d.name AS dst_name, d.etype AS dst_etype"
        " FROM edges e JOIN graph_types gt ON gt.kind = 'edge' AND gt.name = e.rel"
        " JOIN entities s ON s.id = e.src JOIN entities d ON d.id = e.dst"
        " WHERE e.status = 'active'"
        " AND (gt.src_types IS NOT NULL OR gt.dst_types IS NOT NULL)"
    ):
        bad = []
        if not _graph._endpoint_ok(conn, row["src_types"], row["src_etype"]):
            bad.append(f"src {row['src_name']} ({row['src_etype']}) not in [{row['src_types']}]")
        if not _graph._endpoint_ok(conn, row["dst_types"], row["dst_etype"]):
            bad.append(f"dst {row['dst_name']} ({row['dst_etype']}) not in [{row['dst_types']}]")
        if bad:
            findings.append({
                "issue": "edge_endpoint_violation", "ids": str(row["id"]),
                "detail": f"{row['rel']}: " + "; ".join(bad),
            })
    # #69: an alias mapping to several active entities can never resolve
    # headlessly; every capture that used it linked nothing.
    for row in conn.execute(
        "SELECT a.alias_norm, COUNT(DISTINCT COALESCE(e.merged_into, e.id)) AS n,"
        " GROUP_CONCAT(DISTINCT a.entity_id) AS ids"
        " FROM entity_aliases a JOIN entities e ON e.id = a.entity_id"
        " GROUP BY a.alias_norm HAVING n > 1"
    ):
        findings.append({
            "issue": "ambiguous_alias", "ids": row["ids"],
            "detail": f"alias '{row['alias_norm']}' maps to {row['n']} entities;"
                      " headless captures link nothing until resolved (entity merge"
                      " or alias removal)",
        })
    # #71: a machine-sourced edge whose evidence memories have all been
    # deleted (decay, forget, purge) asserts a claim nothing backs any more.
    for row in conn.execute(
        "SELECT e.id, e.rel, s.name AS src_name, d.name AS dst_name FROM edges e"
        " JOIN entities s ON s.id = e.src JOIN entities d ON d.id = e.dst"
        " WHERE e.source <> 'manual' AND e.status = 'active'"
        " AND e.id NOT IN (SELECT edge_id FROM edge_sources)"
    ):
        findings.append({
            "issue": "edge_evidence_gone", "ids": str(row["id"]),
            "detail": f"{row['src_name']} -{row['rel']}-> {row['dst_name']}"
                      " (machine-sourced, all evidence deleted)",
        })
    return findings


def why(conn: sqlite3.Connection, memory_id: int) -> str:
    """FR-M1: a memory's full story - origin, promotion lineage, corrections,
    usage, mentions - rendered as markdown from the lineage columns."""
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        return f"No memory with id {memory_id}."
    lines = [f"**#{row['id']}** [{row['type']}/{row['scope']}] {row['content']}"]
    facts = [f"recorded {row['created_at']}", f"confidence {row['confidence']:.2f}",
             f"origin {row['origin']}"]
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


def scorecard(conn: sqlite3.Connection, days: int = 7) -> dict:
    """FR-M6: the weekly dogfood scorecard. Read-only, like the eval harness:
    measuring must never distort what is measured."""
    window = f"-{int(days)} days"

    def one(sql: str, *params) -> float | int:
        return conn.execute(sql, params).fetchone()[0]

    per_surface = {
        r["surface"]: {"judged": r["judged"], "precision": round(r["precision"], 3)}
        for r in conn.execute(
            "SELECT surface, COUNT(*) AS judged, AVG(was_useful) AS precision"
            " FROM recall_trace WHERE was_useful IS NOT NULL"
            " AND created_at >= datetime('now', ?) GROUP BY surface", (window,))
    }
    import time as _time

    last_capture = conn.execute(
        "SELECT MAX(created_at) FROM memories WHERE origin_session IS NOT NULL"
    ).fetchone()[0]
    days_since_capture = (
        round(conn.execute(
            "SELECT julianday('now') - julianday(?)", (last_capture,)).fetchone()[0], 1)
        if last_capture else None
    )
    injected_tokens = one(
        "SELECT COALESCE(SUM(length(m.content)) / 4, 0) FROM injection_log il"
        " JOIN memories m ON m.id = il.memory_id WHERE il.injected_at >= datetime('now', ?)",
        window)
    started = _time.perf_counter()
    recall_pack(conn, cfg=config.load())  # sessionless: read-only probe
    latency_ms = round((_time.perf_counter() - started) * 1000, 1)
    return {
        "period_days": int(days),
        "days_since_last_capture": days_since_capture,
        "injected_tokens_estimate": injected_tokens,
        "recall_latency_ms": latency_ms,
        "injections": one(
            "SELECT COUNT(*) FROM injection_log WHERE injected_at >= datetime('now', ?)", window),
        "traces": one(
            "SELECT COUNT(*) FROM recall_trace WHERE created_at >= datetime('now', ?)", window),
        "traces_judged": one(
            "SELECT COUNT(*) FROM recall_trace WHERE was_useful IS NOT NULL"
            " AND created_at >= datetime('now', ?)", window),
        "precision_by_surface": per_surface,
        "new_memories": one(
            "SELECT COUNT(*) FROM memories WHERE created_at >= datetime('now', ?)", window),
        "consolidation_backlog": one("SELECT COUNT(*) FROM v_consolidation_backlog"),
        "quarantined": one("SELECT COUNT(*) FROM memories WHERE scope = 'quarantine'"),
        "open_handoffs": one("SELECT COUNT(*) FROM handoffs WHERE consumed_at IS NULL"),
        "due_intentions": one(
            "SELECT COUNT(*) FROM intentions WHERE status = 'pending'"
            " AND trigger_kind = 'time' AND substr(trigger_value,1,10) <= date('now')"),
        "memories_total": one("SELECT COUNT(*) FROM memories"),
    }


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
