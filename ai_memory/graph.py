"""Entity memory: a typed knowledge graph of people, projects, systems and links."""

from __future__ import annotations

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
