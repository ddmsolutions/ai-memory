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
# #67 contract: PREFER VERBATIM - return the memo's own wording wherever it
# already reads as a clean fact/rule; rewrite only what genuinely needs
# distilling. Summarisation compounds errors across consolidation cycles.
Distiller = Callable[[str], tuple[str, str, bool] | None]
# entity_extractor(content) -> [(name, etype)] - FR-N5, engine-verified upserts
Extractor = Callable[[str], list[tuple[str, str]]]


def _hygiene(conn: sqlite3.Connection, cfg: dict, dry_run: bool) -> dict:
    actions = {"duplicates_superseded": 0, "stale_triaged": 0, "decayed": 0}
    groups: dict[tuple, list] = {}
    for row in conn.execute("SELECT id, type, scope, content, pinned FROM v_active_memories"):
        groups.setdefault((row["type"], row["scope"], row["content"].lower()), []).append(row)
    for members in groups.values():
        if len(members) < 2:
            continue
        # Keeper prefers pinned, then newest: a pinned fact must never be
        # silently superseded by an unpinned duplicate (review finding).
        keeper = max(members, key=lambda r: (r["pinned"], r["id"]))["id"]
        losers = [r["id"] for r in members if r["id"] != keeper]
        if not dry_run:
            qmarks = ",".join("?" * len(losers))
            conn.execute(
                f"UPDATE memories SET superseded_by = ? WHERE id IN ({qmarks})",
                [keeper, *losers],
            )
        actions["duplicates_superseded"] += len(losers)
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


def _distil(
    conn: sqlite3.Connection,
    distiller: Distiller | None,
    cfg: dict,
    dry_run: bool,
    entity_extractor: Extractor | None = None,
) -> dict:
    from . import graph, redact

    result = {"promoted": 0, "quarantined": 0, "left": 0, "entities_mentioned": 0,
              "deduped": 0}
    if distiller is None:
        result["left"] = len(store.unconsolidated(conn))
        return result
    for row in store.unconsolidated(conn):
        verdict = distiller(row["content"])
        if verdict is None:
            result["left"] += 1
            continue
        mtype, content, certain = verdict
        # #67 dedupe-first: when the distilled content already exists as an
        # active durable row, do NOT create a rewritten near-duplicate - mark
        # the episode consolidated and attach it as evidence (derives_from),
        # so repetition strengthens the existing fact instead of forking it.
        existing = conn.execute(
            "SELECT id FROM v_active_memories WHERE type = ? AND scope IN (?, 'global')"
            " AND lower(content) = lower(?)",
            (mtype, row["scope"], content),
        ).fetchone()
        if existing is not None:
            if not dry_run:
                conn.execute(
                    "UPDATE memories SET consolidated = 1 WHERE id = ?", (row["id"],)
                )
                store.link_memories(conn, existing["id"], row["id"], "derives_from",
                                    weight=0.8)
                conn.execute(
                    "UPDATE memories SET confidence = MIN(1.0, confidence + 0.05)"
                    " WHERE id = ?", (existing["id"],)
                )
                conn.commit()
            result["deduped"] += 1
            continue
        # Review blocker: distiller output is MODEL-GENERATED content and gets
        # the deterministic instruction screen regardless of the model's own
        # certainty claim; a screened hit is quarantined, never recallable.
        if redact.screen_instructions(content, cfg.get("instruction_patterns")):
            certain = False
        if dry_run:
            result["promoted" if certain else "quarantined"] += 1
            continue
        new_id = store.promote(conn, row["id"], mtype, content=content)
        if not certain:
            conn.execute("UPDATE memories SET scope = 'quarantine' WHERE id = ?", (new_id,))
            conn.commit()
            result["quarantined"] += 1
        else:
            result["promoted"] += 1
            if entity_extractor is not None:
                # FR-N5: model proposes, engine validates. Same name guards as
                # the deterministic path; quarantined promotions get nothing.
                for name, etype in entity_extractor(content) or []:
                    name = str(name).strip()
                    if graph._valid_entity_name(name):
                        graph.mention(conn, new_id, name, etype=str(etype)[:30] or "thing")
                        result["entities_mentioned"] += 1
    return result


def run(
    db_path: Path,
    questions: list[dict] | None = None,
    cfg: dict | None = None,
    k: int = 5,
    dry_run: bool = False,
    distiller: Distiller | None = None,
    entity_extractor: Extractor | None = None,
) -> dict:
    db_path = Path(db_path)
    if cfg is None:
        cfg = config_mod.load()
    snapshot = db_path.with_name(db_path.name + ".autoconsolidate.bak")
    conn = db.connect(db_path)
    if not dry_run:
        # VACUUM INTO writes a consistent point-in-time copy even with
        # concurrent WAL writers (review finding: checkpoint+copy can tear).
        snapshot.unlink(missing_ok=True)
        conn.execute("VACUUM INTO ?", (str(snapshot),))
    baseline = evalharness.run_eval(conn, questions, k=k, cfg=cfg) if questions else None

    report = {
        "dry_run": dry_run,
        "snapshot": str(snapshot) if not dry_run else None,
        "hygiene": _hygiene(conn, cfg, dry_run),
        "distillation": _distil(conn, distiller, cfg, dry_run, entity_extractor),
        "reverted": False,
    }

    if questions and not dry_run:
        after = evalharness.run_eval(conn, questions, k=k, cfg=cfg)
        report["eval_before"] = {"hit_rate": baseline["hit_rate"], "mrr": baseline["mrr"]}
        report["eval_after"] = {"hit_rate": after["hit_rate"], "mrr": after["mrr"]}
        # Same predicate as tune's adoption gate: NO metric may degrade.
        if after["hit_rate"] < baseline["hit_rate"] or after["mrr"] < baseline["mrr"]:
            conn.close()
            # Remove WAL sidecars before restoring, or newer frames would be
            # replayed on top of the older restored main file.
            for suffix in ("-wal", "-shm"):
                Path(str(db_path) + suffix).unlink(missing_ok=True)
            shutil.copy2(snapshot, db_path)
            report["reverted"] = True
            return report
    conn.close()
    return report
