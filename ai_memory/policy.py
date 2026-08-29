"""Policy learning (FR-SL4): the defence patterns improve from evidence,
never from vibes. Quarantine outcomes become labels; a proposed pattern must
survive the full historical corpus with zero regressions; adoption is
human-approved (running `policy adopt` IS the approval) and revertible.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import config


def release(conn: sqlite3.Connection, memory_id: int, scope: str = "global") -> None:
    """A quarantined row judged harmless: restore it to a recallable scope and
    record the false-positive label the learner will train on."""
    row = conn.execute("SELECT scope FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        raise ValueError(f"no memory with id {memory_id}")
    if row["scope"] != "quarantine":
        raise ValueError(f"memory {memory_id} is not quarantined")
    conn.execute("UPDATE memories SET scope = ? WHERE id = ?", (scope, memory_id))
    conn.execute(
        "INSERT INTO policy_labels (memory_id, label) VALUES (?, 'false_positive')",
        (memory_id,),
    )
    # PR75 review #5: releasing a memory restores standing to the edges it
    # evidences - a suspended edge with at least one non-quarantined evidence
    # memory is active again. Without this, suspension was a one-way door and
    # 'everything reviewable' held for memories only.
    conn.execute(
        "UPDATE edges SET status = 'active' WHERE status = 'suspended'"
        " AND id IN (SELECT es.edge_id FROM edge_sources es"
        "  JOIN memories m ON m.id = es.memory_id WHERE m.scope <> 'quarantine')",
    )
    conn.commit()


def confirm_hostile(conn: sqlite3.Connection, memory_id: int) -> None:
    row = conn.execute("SELECT scope FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None or row["scope"] != "quarantine":
        raise ValueError(f"memory {memory_id} is not quarantined")
    conn.execute(
        "INSERT INTO policy_labels (memory_id, label) VALUES (?, 'confirmed_hostile')",
        (memory_id,),
    )
    conn.commit()


def validate(conn: sqlite3.Connection, regex: str) -> dict:
    """Corpus check for a proposed instruction pattern: it must match zero
    active (non-quarantine) rows and zero released false positives. Matching
    confirmed-hostile rows is the upside being bought."""
    try:
        # Compile EXACTLY as the production screen does (_compile_extra: no
        # flags), or validated coverage lies for case variants (review finding).
        rx = re.compile(regex)
    except re.error as exc:
        return {"valid": False, "error": f"bad regex: {exc}"}
    fp_ids = {
        r[0] for r in conn.execute(
            "SELECT memory_id FROM policy_labels WHERE label = 'false_positive'")
    }
    hostile_ids = {
        r[0] for r in conn.execute(
            "SELECT memory_id FROM policy_labels WHERE label = 'confirmed_hostile'")
    }
    active_hits, fp_hits, hostile_hits = [], [], []
    for row in conn.execute("SELECT id, scope, content FROM memories"):
        if not rx.search(row["content"]):
            continue
        if row["id"] in fp_ids:
            fp_hits.append(row["id"])
        elif row["scope"] != "quarantine":
            active_hits.append(row["id"])
        if row["id"] in hostile_ids:
            hostile_hits.append(row["id"])
    valid = not active_hits and not fp_hits
    return {
        "valid": valid,
        "active_regressions": active_hits,
        "false_positive_regressions": fp_hits,
        "confirmed_hostile_caught": hostile_hits,
    }


def sweep(conn: sqlite3.Connection, regex: str, dry_run: bool = False) -> dict:
    """#65 safety-triggered forgetting: quarantine-cascade every ACTIVE row a
    hostile pattern matches. Human-invoked (running it is the approval); each
    hit takes its promotion/derivation descendants with it via
    store.quarantine_cascade, and machine edges lose standing with them."""
    try:
        rx = re.compile(regex)
    except re.error as exc:
        raise ValueError(f"bad regex: {exc}") from exc
    from . import store

    hits = [
        row["id"] for row in conn.execute(
            "SELECT id, content FROM memories WHERE scope <> 'quarantine'")
        if rx.search(row["content"])
    ]
    swept: list[int] = []
    edges = 0
    for memory_id in hits:
        report = store.quarantine_cascade(conn, memory_id, dry_run=dry_run)
        swept.extend(report["memories"])
        edges += report["edges_suspended"]
    return {"pattern_hits": hits, "quarantined": sorted(set(swept)),
            "edges_suspended": edges, "dry_run": dry_run}


def adopt(regex: str, label: str, kind: str = "instruction", path: Path | None = None) -> Path:
    """Append an approved pattern to config (previous file kept at .prev)."""
    from . import tuning

    key = "instruction_patterns" if kind == "instruction" else "secret_patterns"
    target = path or config.config_path()
    current = {}
    if target.exists():
        current = json.loads(target.read_text(encoding="utf-8") or "{}")
    patterns = list(current.get(key) or [])
    patterns.append({"label": label, "regex": regex})
    return tuning.adopt({key: patterns}, path=target)
