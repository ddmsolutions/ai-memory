"""#61: the MCP tool functions - thin wrappers over engine functions.

Invariants carried through unchanged from the CLI/hook surfaces:
- every write passes the SAME capture funnel (redaction inside
  store.remember, the deterministic injection screen here, dedup);
  a model writing via MCP is model-generated content, so instruction-shaped
  input lands in quarantine exactly as hook capture does, and origin can
  never claim 'owner' (#64 - laundering path).
- reads go through the shared trust/quarantine view predicate
  (v_active_memories) because they reuse the engine read functions verbatim.
- scope semantics are the CLI's: default resolved from the server's cwd via
  scope_map, explicit scope wins, exclude_paths refuses writes.
- destructive/self-learning surfaces (init, purge, import, tune,
  autoconsolidate, embed-index) are deliberately ABSENT: humans run those.

No mcp import here: these are plain functions so the funnel/parity tests run
without the optional dependency installed.
"""

from __future__ import annotations

import os
from typing import Any

from ai_memory import config, db, graph, redact, store


def _conn():
    return db.connect()


def _default_scope(cfg: dict) -> str:
    return config.resolve_scope(os.getcwd(), cfg)


def _writes_blocked(cfg: dict) -> bool:
    return config.is_excluded(os.getcwd(), cfg)


def remember(
    content: str,
    type: str = "episodic",
    scope: str | None = None,
    pin: bool = False,
    confidence: float = 0.7,
    valence: str | None = None,
    verify_by: str | None = None,
    supersedes: int | None = None,
    origin: str = "agent",
) -> dict[str, Any]:
    """Store a memory. origin may be 'agent' or 'external' only: an MCP
    caller is a model session and cannot claim owner trust (#64)."""
    cfg = config.load()
    if _writes_blocked(cfg):
        return {"error": "this directory is excluded from ai-memory (exclude_paths)"}
    if origin not in ("agent", "external"):
        return {"error": "origin must be agent or external (owner is CLI-only, #64)"}
    resolved_scope = scope or _default_scope(cfg)
    flag = redact.screen_instructions(content, cfg.get("instruction_patterns"))
    conn = _conn()
    try:
        mid = store.remember(
            conn, content, mtype=type,
            scope="quarantine" if flag else resolved_scope,
            confidence=confidence, pinned=pin, supersedes=supersedes,
            valence=valence, verify_by=verify_by, origin=origin,
        )
        if not flag:
            graph.mention_from_content(conn, mid, content)
    finally:
        conn.close()
    if flag:
        return {"id": mid, "quarantined": True, "screen": flag,
                "note": "instruction-shaped content stored to quarantine, not recallable"}
    return {"id": mid, "scope": resolved_scope, "type": type}


def search(query: str, scope: str | None = None, type: str | None = None,
           limit: int = 10) -> list[dict]:
    """Full-text + semantic hybrid search over active memories."""
    cfg = config.load()
    conn = _conn()
    try:
        rows = store.search(conn, query, mtype=type, scope=scope, limit=limit,
                            cfg=cfg, preferred_scope=None if scope else _default_scope(cfg))
        return [
            {"id": r["id"], "type": r["type"], "scope": r["scope"],
             "content": r["content"], "created_at": r["created_at"],
             "origin": r["origin"]}
            for r in rows
        ]
    finally:
        conn.close()


def recall(task: str | None = None, limit: int | None = None,
           scope: str | None = None) -> str:
    """Compile the markdown recall pack (sessionless: counters untouched)."""
    cfg = config.load()
    conn = _conn()
    try:
        return store.recall_pack(conn, task=task, limit=limit,
                                 scope=scope or _default_scope(cfg), cfg=cfg)
    finally:
        conn.close()


def entity_add(name: str, etype: str = "thing", summary: str | None = None) -> dict:
    conn = _conn()
    try:
        eid = graph.add_entity(conn, name, etype=etype, summary=summary)
        return {"id": eid, "name": name, "etype": etype}
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def entity_link(src: str, dst: str, rel: str, weight: float = 1.0,
                valid_from: str = "", replaces: bool = False) -> dict:
    conn = _conn()
    try:
        edge_id = graph.link(conn, src, dst, rel=rel, weight=weight,
                             valid_from=valid_from, replaces=replaces,
                             source="extract")  # #71: MCP writes are machine-sourced
        return {"edge_id": edge_id}
    except (ValueError, graph.AmbiguousEntity) as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def entity_mention(memory_id: int, name: str, etype: str | None = None,
                   subject: bool = False) -> dict:
    conn = _conn()
    try:
        eid = graph.mention(conn, memory_id, name, etype=etype,
                            role="subject" if subject else "mentioned")
        return {"entity_id": eid}
    except (ValueError, graph.AmbiguousEntity) as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def entity_about(name: str) -> list[dict]:
    conn = _conn()
    try:
        return [
            {"id": r["id"], "type": r["type"], "content": r["content"],
             "created_at": r["created_at"], "mention_role": r["mention_role"]}
            for r in graph.memories_about(conn, name)
        ]
    except graph.AmbiguousEntity as exc:
        return [{"error": str(exc)}]
    finally:
        conn.close()


def entity_show(name: str, history: bool = False) -> str:
    conn = _conn()
    try:
        return graph.describe(conn, name, history=history)
    except graph.AmbiguousEntity as exc:
        return f"error: {exc}"
    finally:
        conn.close()


def consolidate_list(limit: int = 50) -> list[dict]:
    conn = _conn()
    try:
        return [
            {"id": r["id"], "content": r["content"], "created_at": r["created_at"],
             "valence": r["valence"]}
            for r in store.unconsolidated(conn, limit=limit)
        ]
    finally:
        conn.close()


def promote(id: int, type: str, content: str | None = None) -> dict:
    """Distil an episodic into a durable memory. #64: the promoted row
    inherits the source origin; #67: episodic sources only, and model-written
    content gets the deterministic screen regardless of certainty claims."""
    cfg = config.load()
    conn = _conn()
    try:
        new_id = store.promote(conn, id, type, content=content)
        if content and redact.screen_instructions(content, cfg.get("instruction_patterns")):
            conn.execute("UPDATE memories SET scope = 'quarantine' WHERE id = ?", (new_id,))
            conn.commit()
            return {"id": new_id, "quarantined": True}
        return {"id": new_id, "promoted_from": id, "type": type}
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def forget(id: int) -> dict:
    conn = _conn()
    try:
        store.forget(conn, id)
        return {"forgot": id}
    finally:
        conn.close()


def pin(id: int, off: bool = False) -> dict:
    conn = _conn()
    try:
        store.set_pin(conn, id, not off)
        return {"id": id, "pinned": not off}
    finally:
        conn.close()


def intend_add(content: str, when: str | None = None, on: str | None = None,
               scope: str | None = None) -> dict:
    cfg = config.load()
    if _writes_blocked(cfg):
        return {"error": "this directory is excluded from ai-memory (exclude_paths)"}
    if bool(when) == bool(on):
        return {"error": "exactly one of when (ISO date) or on (context words)"}
    conn = _conn()
    try:
        iid = store.intend(conn, content, "time" if when else "context",
                           when or on, scope=scope or _default_scope(cfg))
        return {"id": iid}
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def intend_list(all: bool = False) -> list[dict]:
    conn = _conn()
    try:
        where = "" if all else "WHERE status = 'pending'"
        return [
            dict(r) for r in conn.execute(
                f"SELECT id, status, trigger_kind, trigger_value, content"
                f" FROM intentions {where} ORDER BY id")
        ]
    finally:
        conn.close()


def intend_done(id: int) -> dict:
    conn = _conn()
    try:
        store.resolve_intention(conn, id, "done")
        return {"id": id, "status": "done"}
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def handoff_add(content: str, scope: str | None = None) -> dict:
    cfg = config.load()
    if _writes_blocked(cfg):
        return {"error": "this directory is excluded from ai-memory (exclude_paths)"}
    conn = _conn()
    try:
        hid = store.handoff_write(conn, content, scope=scope or _default_scope(cfg))
        return {"id": hid}
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def handoff_list() -> list[dict]:
    conn = _conn()
    try:
        return [
            {"id": r["id"], "scope": r["scope"], "content": r["content"],
             "consumed": r["consumed_at"] is not None}
            for r in conn.execute("SELECT * FROM handoffs ORDER BY id")
        ]
    finally:
        conn.close()


def trace_list(limit: int = 20) -> list[dict]:
    conn = _conn()
    try:
        return [
            dict(r) for r in conn.execute(
                "SELECT id, surface, cue, injected, was_useful, created_at"
                " FROM recall_trace ORDER BY id DESC LIMIT ?", (limit,))
        ]
    finally:
        conn.close()


def feedback(trace_id: int, useful: bool, note: str | None = None) -> dict:
    conn = _conn()
    try:
        return store.feedback(conn, trace_id, useful=useful, note=note)
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def status() -> dict:
    conn = _conn()
    try:
        return store.status(conn)
    finally:
        conn.close()


def why(id: int) -> str:
    conn = _conn()
    try:
        return store.why(conn, id)
    finally:
        conn.close()


# The complete tool surface, used by server registration and the
# no-destructive-tools negative test.
TOOL_FUNCTIONS = (
    remember, search, recall,
    entity_add, entity_link, entity_mention, entity_about, entity_show,
    consolidate_list, promote, forget, pin,
    intend_add, intend_list, intend_done,
    handoff_add, handoff_list,
    trace_list, feedback, status, why,
)
