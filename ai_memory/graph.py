"""Entity memory: a typed knowledge graph of people, projects, systems and links."""

from __future__ import annotations

import re
import sqlite3


class AmbiguousEntity(ValueError):
    """#69: an alias maps to more than one entity. Interactive callers show
    the candidates for the model/user to disambiguate; headless callers link
    NOTHING, because a wrong merge is worse than a missed mention."""

    def __init__(self, name: str, candidates: list[sqlite3.Row]):
        self.name = name
        self.candidates = candidates
        options = ", ".join(f"#{c['id']} {c['name']} ({c['etype']})" for c in candidates)
        super().__init__(f"'{name}' is ambiguous: {options}")


_ALIAS_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# Trailing legal suffixes stripped for fuzzy SUGGESTIONS only - a suffix
# match never auto-links (#69).
_LEGAL_SUFFIXES = (
    "ltd", "limited", "plc", "inc", "incorporated", "llc", "llp", "gmbh",
    "co", "corp", "corporation", "company", "group", "holdings",
)


def normalise_alias(name: str) -> str:
    """#69 lookup key: lowercase, punctuation to space, whitespace collapsed."""
    return _WS_RE.sub(" ", _ALIAS_PUNCT_RE.sub(" ", name.lower())).strip()


def _strip_legal_suffix(norm: str) -> str:
    words = norm.split()
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words = words[:-1]
    return " ".join(words)


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


def _follow_merge(conn: sqlite3.Connection, row: sqlite3.Row, depth: int = 0) -> sqlite3.Row:
    """A merged entity is a redirect: follow merged_into to the survivor."""
    while row is not None and row["status"] == "merged" and row["merged_into"] and depth < 10:
        nxt = conn.execute(
            "SELECT * FROM entities WHERE id = ?", (row["merged_into"],)
        ).fetchone()
        if nxt is None:
            break
        row, depth = nxt, depth + 1
    return row


def resolve(conn: sqlite3.Connection, name: str) -> dict:
    """#69 resolution: exact canonical > alias > fuzzy suggestion.

    Returns {"entity": Row|None, "candidates": [Row], "suggestions": [Row]}.
    entity is set only when resolution is UNAMBIGUOUS. candidates carries the
    ambiguous set; suggestions are legal-suffix fuzzy matches that only ever
    suggest, never auto-link.
    """
    canonical = conn.execute(
        "SELECT * FROM entities WHERE name = ? COLLATE NOCASE AND status = 'active'"
        " ORDER BY id",
        (name,),
    ).fetchall()
    if len(canonical) >= 1:
        # An exact canonical match beats aliases; same-name/different-etype
        # rows keep the long-standing lowest-id behaviour.
        return {"entity": canonical[0], "candidates": canonical, "suggestions": []}
    norm = normalise_alias(name)
    alias_rows = conn.execute(
        "SELECT DISTINCT e.* FROM entity_aliases a JOIN entities e ON e.id = a.entity_id"
        " WHERE a.alias_norm = ? ORDER BY e.id",
        (norm,),
    ).fetchall()
    resolved = []
    seen: set[int] = set()
    for row in alias_rows:
        target = _follow_merge(conn, row)
        if target is not None and target["id"] not in seen:
            seen.add(target["id"])
            resolved.append(target)
    if len(resolved) == 1:
        return {"entity": resolved[0], "candidates": resolved, "suggestions": []}
    if len(resolved) > 1:
        return {"entity": None, "candidates": resolved, "suggestions": []}
    # Nothing matched: offer suffix-stripped SUGGESTIONS (never auto-linked).
    stripped = _strip_legal_suffix(norm)
    suggestions: list[sqlite3.Row] = []
    if stripped:
        for row in conn.execute(
            "SELECT * FROM entities WHERE status = 'active' ORDER BY id"
        ):
            if _strip_legal_suffix(normalise_alias(row["name"])) == stripped:
                suggestions.append(row)
    return {"entity": None, "candidates": [], "suggestions": suggestions}


def find_entity(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """Resolve a name to a single entity through canonical names and aliases.
    Raises AmbiguousEntity when an alias maps to several entities; returns
    None when nothing matches (suggestions are resolve()'s business)."""
    result = resolve(conn, name)
    if result["entity"] is not None:
        return result["entity"]
    if len(result["candidates"]) > 1:
        raise AmbiguousEntity(name, result["candidates"])
    return None


def add_alias(
    conn: sqlite3.Connection,
    alias: str,
    canonical_name: str,
    source: str = "manual",
) -> int:
    """#69: register an alias for an entity. The alias is a lookup key only;
    the canonical name stays on the entity row."""
    ent = find_entity(conn, canonical_name)
    if ent is None:
        raise ValueError(f"no entity named '{canonical_name}'")
    norm = normalise_alias(alias)
    if not norm:
        raise ValueError("alias normalises to nothing")
    if norm == normalise_alias(ent["name"]):
        raise ValueError("alias equals the canonical name")
    conn.execute(
        "INSERT OR IGNORE INTO entity_aliases (entity_id, alias_norm, alias_raw, source)"
        " VALUES (?, ?, ?, ?)",
        (ent["id"], norm, alias, source),
    )
    conn.commit()
    return ent["id"]


def add_ref(conn: sqlite3.Connection, name: str, kind: str, value: str) -> int:
    """#73: attach a hard identifier (domain, company number, email, CRM id)
    to an entity. Refs are authoritative and unique: a second entity claiming
    the same ref is a split entity - the error says merge, not insert."""
    ent = find_entity(conn, name)
    if ent is None:
        raise ValueError(f"no entity named '{name}'")
    kind = kind.strip().lower()
    value = value.strip()
    if not kind or not value:
        raise ValueError("ref needs a kind and a value")
    holder = resolve_ref(conn, kind, value)
    if holder is not None and holder["id"] != ent["id"]:
        raise ValueError(
            f"{kind}={value} already identifies '{holder['name']}' (#{holder['id']});"
            f" if these are the same entity: entity merge '{ent['name']}' '{holder['name']}'"
        )
    conn.execute(
        "INSERT OR IGNORE INTO entity_refs (entity_id, kind, value) VALUES (?, ?, ?)",
        (ent["id"], kind, value),
    )
    conn.commit()
    return ent["id"]


def resolve_ref(conn: sqlite3.Connection, kind: str, value: str) -> sqlite3.Row | None:
    """#73: a ref match is authoritative - it resolves what an entity IS.
    Merged tombstones redirect to the survivor."""
    row = conn.execute(
        "SELECT e.* FROM entity_refs r JOIN entities e ON e.id = r.entity_id"
        " WHERE r.kind = ? AND r.value = ?",
        (kind.strip().lower(), value.strip()),
    ).fetchone()
    return _follow_merge(conn, row) if row is not None else None


def entity_refs(conn: sqlite3.Connection, entity_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT kind, value FROM entity_refs WHERE entity_id = ? ORDER BY kind",
        (entity_id,),
    ).fetchall()


def merge_entities(conn: sqlite3.Connection, loser_name: str, winner_name: str) -> dict:
    """#69: retroactive repair for a split entity. Repoints mentions and
    edges, moves aliases, demotes the loser's name to an alias of the winner,
    and leaves the loser as a merged tombstone (redirect), never deleted."""
    loser = find_entity(conn, loser_name)
    winner = find_entity(conn, winner_name)
    if loser is None or winner is None:
        raise ValueError(f"unknown entity: {loser_name if loser is None else winner_name}")
    if loser["id"] == winner["id"]:
        raise ValueError("loser and winner are the same entity")
    lid, wid = loser["id"], winner["id"]
    mentions = conn.execute(
        "UPDATE OR IGNORE memory_entities SET entity_id = ? WHERE entity_id = ?",
        (wid, lid),
    ).rowcount
    conn.execute("DELETE FROM memory_entities WHERE entity_id = ?", (lid,))
    edges_moved = 0
    for col in ("src", "dst"):
        edges_moved += conn.execute(
            f"UPDATE OR IGNORE edges SET {col} = ? WHERE {col} = ?", (wid, lid)
        ).rowcount
        conn.execute(f"DELETE FROM edges WHERE {col} = ?", (lid,))
    conn.execute(
        "UPDATE OR IGNORE entity_aliases SET entity_id = ? WHERE entity_id = ?",
        (wid, lid),
    )
    conn.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (lid,))
    # #73: hard identifiers follow the survivor.
    conn.execute(
        "UPDATE OR IGNORE entity_refs SET entity_id = ? WHERE entity_id = ?",
        (wid, lid),
    )
    conn.execute("DELETE FROM entity_refs WHERE entity_id = ?", (lid,))
    conn.execute(
        "INSERT OR IGNORE INTO entity_aliases (entity_id, alias_norm, alias_raw, source)"
        " VALUES (?, ?, ?, 'merge')",
        (wid, normalise_alias(loser["name"]), loser["name"]),
    )
    conn.execute(
        "UPDATE entities SET summary = COALESCE(summary, ?) WHERE id = ?",
        (loser["summary"], wid),
    )
    conn.execute(
        "UPDATE entities SET status = 'merged', merged_into = ? WHERE id = ?",
        (wid, lid),
    )
    conn.commit()
    return {"loser": lid, "winner": wid, "mentions_moved": mentions,
            "edges_moved": edges_moved}


# #71: default confidence per source channel; manual (a human typed it)
# outranks the machine paths, mirroring the #64 origin trust ordering.
EDGE_SOURCES = ("manual", "consolidate", "extract")
_SOURCE_CONFIDENCE = {"manual": 0.9, "consolidate": 0.7, "extract": 0.6}


def link(
    conn: sqlite3.Connection,
    src_name: str,
    dst_name: str,
    rel: str,
    weight: float = 1.0,
    memory_id: int | None = None,
    valid_from: str = "",
    replaces: bool = False,
    source: str = "manual",
) -> int:
    """Create or update an edge. #68: edges carry a valid-time window.

    valid_from '' means the window opened at an unknown time (the common
    case); a dated valid_from allows the SAME relationship to recur (left,
    rejoined) as distinct rows. replaces=True closes any other open window
    of this (src, dst, rel) first: supersession, never deletion.

    #71: source is the provenance channel; evidence memories accumulate in
    edge_sources, and each NEW piece of evidence reinforces confidence
    asymptotically (corroboration is deterministic, never invented).
    """
    if source not in EDGE_SOURCES:
        raise ValueError(f"source must be one of {EDGE_SOURCES}")
    src = find_entity(conn, src_name) or None
    dst = find_entity(conn, dst_name) or None
    src_id = src["id"] if src else add_entity(conn, src_name)
    dst_id = dst["id"] if dst else add_entity(conn, dst_name)
    if replaces:
        conn.execute(
            "UPDATE edges SET t_invalid = COALESCE(NULLIF(?, ''), date('now'))"
            " WHERE src = ? AND dst = ? AND rel = ? AND t_invalid IS NULL"
            " AND t_valid <> ?",
            (valid_from, src_id, dst_id, rel, valid_from),
        )
    cur = conn.execute(
        "INSERT INTO edges (src, dst, rel, weight, memory_id, t_valid, source, confidence)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(src, dst, rel, t_valid) DO UPDATE SET"
        " weight = excluded.weight, memory_id = COALESCE(excluded.memory_id, edges.memory_id)"
        " RETURNING id",
        (src_id, dst_id, rel, weight, memory_id, valid_from, source,
         _SOURCE_CONFIDENCE[source]),
    )
    edge_id = cur.fetchone()[0]
    if memory_id is not None:
        add_edge_evidence(conn, edge_id, memory_id)
    conn.commit()
    return edge_id


def add_edge_evidence(conn: sqlite3.Connection, edge_id: int, memory_id: int) -> None:
    """#71: attach an evidencing memory to an edge. A genuinely new piece of
    evidence (not a re-insert) reinforces confidence, approaching 1.0."""
    added = conn.execute(
        "INSERT OR IGNORE INTO edge_sources (edge_id, memory_id) VALUES (?, ?)",
        (edge_id, memory_id),
    ).rowcount
    if added:
        # The first evidence is priced into the source-channel default;
        # corroboration starts at the second independent memory.
        count = conn.execute(
            "SELECT COUNT(*) FROM edge_sources WHERE edge_id = ?", (edge_id,)
        ).fetchone()[0]
        if count > 1:
            conn.execute(
                "UPDATE edges SET confidence = MIN(1.0, confidence + (1.0 - confidence) * 0.2)"
                " WHERE id = ?",
                (edge_id,),
            )


def close_edge(
    conn: sqlite3.Connection,
    src_name: str,
    dst_name: str,
    rel: str,
    on: str | None = None,
) -> int:
    """#68: close the open validity window(s) of an edge. Non-destructive:
    the row survives with t_invalid set, excluded from default reads."""
    src, dst = find_entity(conn, src_name), find_entity(conn, dst_name)
    if src is None or dst is None:
        return 0
    count = conn.execute(
        "UPDATE edges SET t_invalid = COALESCE(?, date('now'))"
        " WHERE src = ? AND dst = ? AND rel = ? AND t_invalid IS NULL",
        (on, src["id"], dst["id"], rel),
    ).rowcount
    conn.commit()
    return count


def neighbours(
    conn: sqlite3.Connection, name: str, include_closed: bool = False
) -> list[dict]:
    ent = find_entity(conn, name)
    if ent is None:
        return []
    closed = "" if include_closed else " AND e.t_invalid IS NULL"
    # #71: suspended edges (evidence quarantined) never surface here.
    closed += " AND e.status = 'active'"
    rows = conn.execute(
        f"""
        SELECT e.rel, e.weight, 'out' AS direction, o.name AS other, o.etype AS other_type,
               e.t_valid, e.t_invalid, e.source, e.confidence
          FROM edges e JOIN entities o ON o.id = e.dst WHERE e.src = :id{closed}
        UNION ALL
        SELECT e.rel, e.weight, 'in' AS direction, o.name AS other, o.etype AS other_type,
               e.t_valid, e.t_invalid, e.source, e.confidence
          FROM edges e JOIN entities o ON o.id = e.src WHERE e.dst = :id{closed}
        ORDER BY weight DESC
        """,
        {"id": ent["id"]},
    ).fetchall()
    return [dict(r) for r in rows]


def edge_why(conn: sqlite3.Connection, src_name: str, rel: str, dst_name: str) -> str:
    """#71: why do we believe src -rel-> dst - every window with its source
    channel, confidence, and the evidencing memories."""
    src, dst = find_entity(conn, src_name), find_entity(conn, dst_name)
    if src is None or dst is None:
        return f"unknown entity: {src_name if src is None else dst_name}"
    rows = conn.execute(
        "SELECT * FROM edges WHERE src = ? AND dst = ? AND rel = ? ORDER BY t_valid",
        (src["id"], dst["id"], rel),
    ).fetchall()
    if not rows:
        return f"no edge {src_name} -{rel}-> {dst_name}"
    lines = [f"**{src['name']} -{rel}-> {dst['name']}**"]
    for e in rows:
        window = f"from {e['t_valid'][:10]}" if e["t_valid"] else "window open-ended"
        if e["t_invalid"]:
            window += f" until {e['t_invalid'][:10]}"
        lines.append(
            f"- edge #{e['id']}: {window}, source {e['source']},"
            f" confidence {e['confidence']:.2f}"
            + (", SUSPENDED (evidence quarantined)" if e["status"] == "suspended" else "")
        )
        for ev in conn.execute(
            "SELECT m.id, m.created_at, m.content FROM edge_sources es"
            " JOIN memories m ON m.id = es.memory_id WHERE es.edge_id = ?",
            (e["id"],),
        ):
            lines.append(f"  - evidence #{ev['id']} [{ev['created_at'][:10]}]: {ev['content'][:80]}")
    return "\n".join(lines)


def suspend_edges_for_memories(conn: sqlite3.Connection, memory_ids: list[int]) -> int:
    """#71/#65: suspend machine-sourced edges whose ENTIRE evidence set sits in
    the given (quarantined) memories. Manual edges stand on the human's word
    and are never suspended by this path."""
    if not memory_ids:
        return 0
    qmarks = ",".join("?" * len(memory_ids))
    suspended = conn.execute(
        f"""
        UPDATE edges SET status = 'suspended'
         WHERE source <> 'manual' AND status = 'active'
           AND id IN (SELECT edge_id FROM edge_sources)
           AND id NOT IN (
             SELECT edge_id FROM edge_sources WHERE memory_id NOT IN ({qmarks})
           )
        """,
        memory_ids,
    ).rowcount
    return suspended


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
    """Create mentions for every entity the content's entities: line names.
    #69: this is a HEADLESS path - an ambiguous alias links nothing (a wrong
    merge is worse than a missed mention); lint surfaces ambiguous aliases."""
    added = 0
    for name in parse_entity_names(content):
        try:
            mention(conn, memory_id, name)
        except AmbiguousEntity:
            continue
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
    """Everything we know about X, in one query, resolved through aliases
    (#69). Reads through v_active_memories, so superseded and quarantined
    rows never appear."""
    ent = find_entity(conn, entity_name)
    if ent is None:
        return []
    return conn.execute(
        "SELECT m.* FROM memory_entities me"
        " JOIN v_active_memories m ON m.id = me.memory_id"
        " WHERE me.entity_id = ? ORDER BY m.created_at DESC",
        (ent["id"],),
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
        # #69: a purge by alias must reach the canonical entity (and its
        # merge tombstones), or the GDPR-shaped forget silently misses.
        entity_ids += [
            r["entity_id"] for r in conn.execute(
                "SELECT entity_id FROM entity_aliases WHERE alias_norm = ?",
                (normalise_alias(entity_name),),
            ) if r["entity_id"] not in entity_ids
        ]
        entity_ids += [
            r["id"] for r in conn.execute(
                f"SELECT id FROM entities WHERE merged_into IN"
                f" ({','.join('?' * len(entity_ids))})", entity_ids
            ) if r["id"] not in entity_ids
        ] if entity_ids else []
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


def describe(conn: sqlite3.Connection, name: str, history: bool = False) -> str:
    """One-paragraph markdown summary of an entity and its relationships.
    history=True includes closed validity windows, marked (#68)."""
    ent = find_entity(conn, name)
    if ent is None:
        return f"No entity named '{name}'."
    lines = [f"**{ent['name']}** ({ent['etype']})"]
    if ent["summary"]:
        lines.append(ent["summary"])
    refs = entity_refs(conn, ent["id"])
    if refs:
        lines.append("refs: " + ", ".join(f"{r['kind']}={r['value']}" for r in refs))
    for n in neighbours(conn, name, include_closed=history):
        arrow = "->" if n["direction"] == "out" else "<-"
        line = f"- {arrow} {n['rel']} {arrow} {n['other']} ({n['other_type']})"
        if n.get("t_valid"):
            line += f" (from {n['t_valid'][:10]})"
        if n.get("t_invalid"):
            line += f" (ENDED {n['t_invalid'][:10]})"
        if n.get("source") and n["source"] != "manual":
            line += f" [{n['source']} {n['confidence']:.2f}]"
        lines.append(line)
    return "\n".join(lines)
