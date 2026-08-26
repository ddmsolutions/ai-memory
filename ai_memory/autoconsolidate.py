"""Autonomous consolidation (FR-SL3), conservative by decision on epic #40.

Gate order: snapshot the file before any write; run the deterministic hygiene
pass (duplicate supersession, stale-fact triage, decay); optionally distil the
backlog through a supplied distiller callable (model work, engine-verified);
then re-run the eval set, and if retrieval degraded, RESTORE the snapshot and
report the revert. Uncertain distillations land in quarantine, never in a
recallable scope. Every promotion is traceable via promoted_from.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Callable

from . import config as config_mod, db, evalharness, store

# distiller(content) -> (mtype, distilled_content, certain) | None
Distiller = Callable[[str], tuple[str, str, bool] | None]


def _hygiene(conn: sqlite3.Connection, cfg: dict, dry_run: bool) -> dict:
    actions = {"duplicates_superseded": 0, "stale_triaged": 0, "decayed": 0}
    dup_groups = conn.execute(
        "SELECT MAX(id) AS keeper, GROUP_CONCAT(id) AS ids FROM v_active_memories"
        " GROUP BY type, scope, lower(content) HAVING COUNT(*) > 1"
    ).fetchall()
    for group in dup_groups:
        ids = [int(x) for x in group["ids"].split(",") if int(x) != group["keeper"]]
        if not dry_run:
            qmarks = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE memories SET superseded_by = ? WHERE id IN ({qmarks})",
                [group["keeper"], *ids],
            )
        actions["duplicates_superseded"] += len(ids)
    stale = conn.execute(
        "SELECT id FROM v_active_memories WHERE type = 'semantic' AND verify_by IS NULL"
        " AND recall_count = 0 AND created_at < datetime('now', '-180 days')"
    ).fetchall()
    if stale and not dry_run:
        qmarks = ",".join("?" * len(stale))
        conn.execute(
            f"UPDATE memories SET verify_by = date('now') WHERE id IN ({qmarks})",
            [r["id"] for r in stale],
        )
    actions["stale_triaged"] = len(stale)
    if not dry_run:
        conn.commit()
    actions["decayed"] = len(store.decay(conn, cfg, dry_run=dry_run))
    return actions


def _distil(conn: sqlite3.Connection, distiller: Distiller | None, dry_run: bool) -> dict:
    result = {"promoted": 0, "quarantined": 0, "left": 0}
    if distiller is None:
        result["left"] = len(store.unconsolidated(conn))
        return result
    for row in store.unconsolidated(conn):
        verdict = distiller(row["content"])
        if verdict is None:
            result["left"] += 1
            continue
        mtype, content, certain = verdict
        if dry_run:
            result["promoted" if certain else "quarantined"] += 1
            continue
        new_id = store.promote(conn, row["id"], mtype, content=content)
        if not certain:
            # Uncertain promotions are reviewable, never recallable (FR-SL3).
            conn.execute("UPDATE memories SET scope = 'quarantine' WHERE id = ?", (new_id,))
            conn.commit()
            result["quarantined"] += 1
        else:
            result["promoted"] += 1
    return result


def run(
    db_path: Path,
    questions: list[dict] | None = None,
    cfg: dict | None = None,
    k: int = 5,
    dry_run: bool = False,
    distiller: Distiller | None = None,
) -> dict:
    db_path = Path(db_path)
    if cfg is None:
        cfg = config_mod.load()
    snapshot = db_path.with_name(db_path.name + ".autoconsolidate.bak")
    conn = db.connect(db_path)
    if not dry_run:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy2(db_path, snapshot)
    baseline = evalharness.run_eval(conn, questions, k=k, cfg=cfg) if questions else None

    report = {
        "dry_run": dry_run,
        "snapshot": str(snapshot) if not dry_run else None,
        "hygiene": _hygiene(conn, cfg, dry_run),
        "distillation": _distil(conn, distiller, dry_run),
        "reverted": False,
    }

    if questions and not dry_run:
        after = evalharness.run_eval(conn, questions, k=k, cfg=cfg)
        report["eval_before"] = {"hit_rate": baseline["hit_rate"], "mrr": baseline["mrr"]}
        report["eval_after"] = {"hit_rate": after["hit_rate"], "mrr": after["mrr"]}
        if after["hit_rate"] < baseline["hit_rate"]:
            conn.close()
            shutil.copy2(snapshot, db_path)
            report["reverted"] = True
            return report
    conn.close()
    return report
