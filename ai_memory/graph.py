"""Entity memory: a typed knowledge graph of people, projects, systems and links."""

from __future__ import annotations

import re
import sqlite3


def add_entity(
    conn: sqlite3.Connection,
    name: str,
    etype: str = "thing",
    summary: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO entities (name, etype, summary) VALUES (?, ?, ?)"
        " ON CONFLICT(name, etype) DO UPDATE SET"
        " summary = COALESCE(excluded.summary, entities.summary)"
        " RETURNING id",
        (name, etype, summary),
    )
    eid = cur.fetchone()[0]
    conn.commit()
    return eid


def find_entity(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM entities WHERE name = ? COLLATE NOCASE ORDER BY id LIMIT 1",
        (name,),
    ).fetchone()


def link(
    conn: sqlite3.Connection,
    src_name: str,
    dst_name: str,
    rel: str,
    weight: float = 1.0,
    memory_id: int | None = None,
) -> int:
    src = find_entity(conn, src_name) or None
    dst = find_entity(conn, dst_name) or None
    src_id = src["id"] if src else add_entity(conn, src_name)
    dst_id = dst["id"] if dst else add_entity(conn, dst_name)
    cur = conn.execute(
        "INSERT INTO edges (src, dst, rel, weight, memory_id) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(src, dst, rel) DO UPDATE SET"
        " weight = excluded.weight, memory_id = COALESCE(excluded.memory_id, edges.memory_id)"
        " RETURNING id",
        (src_id, dst_id, rel, weight, memory_id),
    )
    edge_id = cur.fetchone()[0]
    conn.commit()
    return edge_id


def neighbours(conn: sqlite3.Connection, name: str) -> list[dict]:
    ent = find_entity(conn, name)
    if ent is None:
        return []
    rows = conn.execute(
        """
        SELECT e.rel, e.weight, 'out' AS direction, o.name AS other, o.etype AS other_type
          FROM edges e JOIN entities o ON o.id = e.dst WHERE e.src = :id
        UNION ALL
        SELECT e.rel, e.weight, 'in' AS direction, o.name AS other, o.etype AS other_type
          FROM edges e JOIN entities o ON o.id = e.src WHERE e.dst = :id
        ORDER BY weight DESC
        """,
        {"id": ent["id"]},
    ).fetchall()
    return [dict(r) for r in rows]


_ENTITY_LINE_RE = re.compile(r"^entities:\s*(.+)$", re.I | re.M)


def _valid_entity_name(name: str) -> bool:
    """Entity names are later injected via graph lines, so they get the same
    guards as content: length caps and the instruction screen."""
    from . import redact

    return 3 <= len(name) <= 60 and redact.screen_instructions(name) is None


def parse_entity_names(text: str) -> list[str]:
    """FR-N4: the memo format already names its entities on an `entities:` line;
    parse it deterministically. Returns validated, deduped names in order."""
    names: list[str] = []
    seen: set[str] = set()
    for match in _ENTITY_LINE_RE.finditer(text):
        for raw in match.group(1).split(","):
            name = raw.strip().strip(".")
            key = name.lower()
            if name and key not in seen and _valid_entity_name(name):
                seen.add(key)
                names.append(name)
    return names


def mention_from_content(conn: sqlite3.Connection, memory_id: int, content: str) -> int:
    """Create mentions for every entity the content's entities: line names."""
    added = 0
    for name in parse_entity_names(content):
        mention(conn, memory_id, name)
        added += 1
    return added


def backfill_mentions(conn: sqlite3.Connection) -> dict:
    """One-off (idempotent) sweep: mention-link existing active memories whose
    content carries entities: lines."""
    before_entities = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    before_mentions = conn.execute("SELECT COUNT(*) FROM memory_entities").fetchone()[0]
    scanned = 0
    for row in conn.execute("SELECT id, content FROM v_active_memories"):
        scanned += 1
        mention_from_content(conn, row["id"], row["content"])
    return {
        "memories_scanned": scanned,
        "mentions_added": conn.execute(
            "SELECT COUNT(*) FROM memory_entities").fetchone()[0] - before_mentions,
        "entities_created": conn.execute(
            "SELECT COUNT(*) FROM entities").fetchone()[0] - before_entities,
    }


def mention(
    conn: sqlite3.Connection,
    memory_id: int,
    entity_name: str,
    etype: str | None = None,
) -> int:
    """FR-N1: link a memory to an entity it mentions, auto-creating the entity."""
    if conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,)).fetchone() is None:
        raise ValueError(f"no memory with id {memory_id}")
    ent = find_entity(conn, entity_name)
    entity_id = ent["id"] if ent else add_entity(conn, entity_name, etype=etype or "thing")
    conn.execute(
        "INSERT OR IGNORE INTO memory_entities (memory_id, entity_id) VALUES (?, ?)",
        (memory_id, entity_id),
    )
    conn.commit()
    return entity_id


def memories_about(conn: sqlite3.Connection, entity_name: str) -> list[sqlite3.Row]:
    """Everything we know about X, in one query. v_entity_memories reads
    through v_active_memories, so superseded and quarantined rows never appear."""
    return conn.execute(
        "SELECT * FROM v_entity_memories WHERE entity_name = ? COLLATE NOCASE"
        " ORDER BY created_at DESC",
        (entity_name,),
    ).fetchall()


def purge_subject(
    conn: sqlite3.Connection,
    entity_name: str | None = None,
    session_id: str | None = None,
    dry_run: bool = False,
) -> dict:
    """FR-N2: erase everything about an entity (memories that mention it, its
    edges, the entity itself) or everything captured in a session. Hard delete;
    FTS and joins are cleaned by triggers and cascades. Returns counts."""
    if not entity_name and not session_id:
        raise ValueError("purge needs an entity name or a session id")
    memory_ids: set[int] = set()
    entity_ids: list[int] = []
    edge_count = 0
    if entity_name:
        entity_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM entities WHERE name = ? COLLATE NOCASE", (entity_name,)
            )
        ]
        for eid in entity_ids:
            memory_ids.update(
                r[0] for r in conn.execute(
                    "SELECT memory_id FROM memory_entities WHERE entity_id = ?", (eid,)
                )
            )
            edge_count += conn.execute(
                "SELECT COUNT(*) FROM edges WHERE src = ? OR dst = ?", (eid, eid)
            ).fetchone()[0]
    if session_id:
        memory_ids.update(
            r[0] for r in conn.execute(
                "SELECT id FROM memories WHERE origin_session = ?", (session_id,)
            )
        )
    intention_ids: list[int] = []
    if session_id:
        intention_ids += [
            r[0] for r in conn.execute(
                "SELECT id FROM intentions WHERE origin_session = ?", (session_id,)
            )
        ]
    if entity_name:
        intention_ids += [
            r[0] for r in conn.execute(
                "SELECT id FROM intentions WHERE lower(content) LIKE ?",
                (f"%{entity_name.lower()}%",),
            )
        ]
    report = {
        "memories": len(memory_ids),
        "entities": len(entity_ids),
        "edges": edge_count,
        "intentions": len(set(intention_ids)),
        "dry_run": dry_run,
    }
    if dry_run:
        return report
    # Plain DELETE leaves row bytes in freed pages; a purge must actually
    # remove them. secure_delete zeroes freed content, VACUUM rebuilds the file.
    conn.execute("PRAGMA secure_delete = ON")
    if memory_ids:
        qmarks = ",".join("?" * len(memory_ids))
        conn.execute(f"DELETE FROM memories WHERE id IN ({qmarks})", list(memory_ids))
    for eid in entity_ids:
        conn.execute("DELETE FROM entities WHERE id = ?", (eid,))
    if session_id:
        conn.execute("DELETE FROM injection_log WHERE session_id = ?", (session_id,))
    if intention_ids:
        qmarks = ",".join("?" * len(set(intention_ids)))
        conn.execute(f"DELETE FROM intentions WHERE id IN ({qmarks})", list(set(intention_ids)))
    conn.commit()
    conn.execute("VACUUM")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return report


def add_role(
    conn: sqlite3.Connection,
    holder: str,
    title: str,
    org: str | None = None,
) -> int:
    """#57: roles are first-class nodes, not edge labels. Creates the role
    entity (etype role), holder -holds-> role, and role -at-> org when given."""
    if not _valid_entity_name(title):
        raise ValueError(f"invalid role title: {title!r}")
    role_name = f"{title} @ {org}" if org else title
    role_id = add_entity(conn, role_name, etype="role")
    link(conn, holder, role_name, rel="holds")
    if org:
        link(conn, role_name, org, rel="at")
    return role_id


def reify_edge(conn: sqlite3.Connection, src: str, rel: str, dst: str) -> int:
    """Convert an existing edge into a per-instance role node: the edge
    (src -rel-> dst) becomes src -has_role-> [rel: src + dst] -with-> dst.
    Per-instance naming keeps who-with-whom distinct across couples/pairs."""
    src_ent, dst_ent = find_entity(conn, src), find_entity(conn, dst)
    if src_ent is None or dst_ent is None:
        raise ValueError(f"unknown entity: {src if src_ent is None else dst}")
    edge = conn.execute(
        "SELECT 1 FROM edges WHERE src = ? AND dst = ? AND rel = ?",
        (src_ent["id"], dst_ent["id"], rel),
    ).fetchone()
    if edge is None:
        raise ValueError(f"no edge {src} -{rel}-> {dst}")
    role_name = f"{rel.replace('_', ' ')}: {src_ent['name']} + {dst_ent['name']}"
    role_id = add_entity(conn, role_name, etype="role")
    link(conn, src_ent["name"], role_name, rel="has_role")
    link(conn, role_name, dst_ent["name"], rel="with")
    conn.execute(
        "DELETE FROM edges WHERE src = ? AND dst = ? AND rel = ?",
        (src_ent["id"], dst_ent["id"], rel),
    )
    conn.commit()
    return role_id


def task_neighbourhood(conn: sqlite3.Connection, task: str, cap: int) -> list[str]:
    """FR-N3: graph lines for entities the task mentions, budget-capped.
    Entity match is name-substring against the task, so multi-word names work."""
    if cap <= 0 or not task:
        return []
    import re as _re

    task_lower = task.lower()
    lines: list[str] = []
    for ent in conn.execute("SELECT * FROM entities ORDER BY length(name) DESC"):
        name = ent["name"]
        # Word-boundary match: entity "AI" must not fire on "maintain".
        if len(name) < 3 or not _re.search(
            r"(?<!\w)" + _re.escape(name.lower()) + r"(?!\w)", task_lower
        ):
            continue
        for n in neighbours(conn, ent["name"])[:3]:
            arrow = "->" if n["direction"] == "out" else "<-"
            lines.append(f"- {ent['name']} {arrow} {n['rel']} {arrow} {n['other']} ({n['other_type']})")
            if len(lines) >= cap:
                return lines
        about = memories_about(conn, ent["name"])[:1]
        if about:
            lines.append(
                f"- about {ent['name']} [{about[0]['created_at'][:10]}]: {about[0]['content']}"
            )
            if len(lines) >= cap:
                return lines
    return lines


def describe(conn: sqlite3.Connection, name: str) -> str:
    """One-paragraph markdown summary of an entity and its relationships."""
    ent = find_entity(conn, name)
    if ent is None:
        return f"No entity named '{name}'."
    lines = [f"**{ent['name']}** ({ent['etype']})"]
    if ent["summary"]:
        lines.append(ent["summary"])
    for n in neighbours(conn, name):
        arrow = "->" if n["direction"] == "out" else "<-"
        lines.append(f"- {arrow} {n['rel']} {arrow} {n['other']} ({n['other_type']})")
    return "\n".join(lines)
