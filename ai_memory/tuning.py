"""Self-tuning parameters (FR-SL1): grid-search the knobs, adopt only what
the eval harness proves non-degrading, keep the previous config for revert.

The loop's metric is the labelled question set, which tune cannot modify:
FR-SL6 by construction.
"""

from __future__ import annotations

import itertools
import json
import sqlite3
from pathlib import Path

from . import config, evalharness

DEFAULT_GRID: dict[str, list] = {
    # Only knobs an eval surface actually exercises (pack ordering + search).
    # turn_recall_min_score is deliberately absent: no surface measures
    # turn_recall yet, and adopting an unmeasured knob breaks FR-SL6.
    "recency_half_life_days": [15.0, 30.0, 60.0],
    "usage_saturation": [2.0, 3.0, 5.0],
    "pack_limit": [8, 12, 16],
    "hybrid_semantic_weight": [0.0, 0.3, 0.5, 0.7],
}


def _cells(grid: dict[str, list]) -> list[dict]:
    keys = sorted(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def _score(conn: sqlite3.Connection, questions: list[dict], cfg: dict, k: int) -> dict:
    report = evalharness.run_eval(conn, questions, k=k, cfg=cfg)
    return {"hit_rate": report["hit_rate"], "mrr": report["mrr"]}


def tune(
    conn: sqlite3.Connection,
    questions: list[dict],
    base_cfg: dict | None = None,
    grid: dict[str, list] | None = None,
    k: int = 5,
) -> dict:
    if base_cfg is None:
        base_cfg = config.load()
    if grid is None:
        grid = DEFAULT_GRID
    baseline = _score(conn, questions, base_cfg, k)
    cells = []
    for overrides in _cells(grid):
        candidate_cfg = {**base_cfg, **overrides}
        cells.append({"overrides": overrides, **_score(conn, questions, candidate_cfg, k)})
    best = max(cells, key=lambda c: (c["hit_rate"], c["mrr"]))
    # Adoption rule (FR-SL1/NFR-11): no metric degrades, at least one improves.
    adoptable = (
        best["hit_rate"] >= baseline["hit_rate"]
        and best["mrr"] >= baseline["mrr"]
        and (best["hit_rate"] > baseline["hit_rate"] or best["mrr"] > baseline["mrr"])
    )
    return {
        "questions": len(questions),
        "k": k,
        "baseline": baseline,
        "cells": cells,
        "best": best,
        "adoptable": adoptable,
    }


def adopt(overrides: dict, path: Path | None = None) -> Path:
    """Merge overrides into the config file; the previous file is kept whole
    at <config>.prev for one-command revert."""
    target = path or config.config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = target.read_text(encoding="utf-8") if target.exists() else "{}"
    target.with_suffix(target.suffix + ".prev").write_text(previous, encoding="utf-8")
    merged = {**json.loads(previous or "{}"), **overrides}
    target.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return target


def revert(path: Path | None = None) -> bool:
    target = path or config.config_path()
    prev = target.with_suffix(target.suffix + ".prev")
    if not prev.exists():
        return False
    target.write_text(prev.read_text(encoding="utf-8"), encoding="utf-8")
    return True
