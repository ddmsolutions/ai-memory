"""Self-maintenance observer (FR-SL5): watches the system's health and raises
the work it needs. Observing and drafting only; implementing raised issues
stays human-triggered (epic #40 decision). Posting mode: draft-for-review by
default; direct posting via `gh` only when config observer_post = "direct".
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import config, store

THRESHOLDS = {
    "unjudged_traces": 20,
    "consolidation_backlog": 50,
    "quarantine": 5,
    "turn_precision_floor": 0.6,
    "min_judged_for_precision": 10,
}


def _draft(title: str, body: str) -> dict:
    return {"title": title, "body": body}


def observe(conn: sqlite3.Connection, cfg: dict | None = None) -> list[dict]:
    """Read the health surfaces; return issue drafts for real findings only."""
    if cfg is None:
        cfg = config.load()
    card = store.scorecard(conn)
    lint = store.lint(conn)
    drafts: list[dict] = []

    if any(f["issue"] == "no_capture" for f in lint):
        detail = next(f["detail"] for f in lint if f["issue"] == "no_capture")
        drafts.append(_draft(
            "fix: capture pipeline appears silently dead",
            f"**Observed:** {detail}.\n**Requirements:** NFR-1 hides breakage; FR-O8 surfaced it.\n"
            "**Acceptance:** identify why no memos are landing (hook wiring, memo habit, "
            "interpreter) and restore capture; scorecard days_since_last_capture under 2.",
        ))
    unjudged = card["traces"] - card["traces_judged"]
    if unjudged > THRESHOLDS["unjudged_traces"]:
        drafts.append(_draft(
            "chore: judge the recall trace backlog",
            f"**Observed:** {unjudged} unjudged traces this window.\n"
            "**Why it matters:** ranking only learns from judged traces (FR-M4); "
            "the precision metric is starving.\n**Acceptance:** backlog under 10 via "
            "`trace list` + `feedback`.",
        ))
    turn = card["precision_by_surface"].get("turn")
    if turn and turn["judged"] >= THRESHOLDS["min_judged_for_precision"] \
            and turn["precision"] < THRESHOLDS["turn_precision_floor"]:
        drafts.append(_draft(
            "fix: turn-recall precision below floor",
            f"**Observed:** turn precision {turn['precision']} over {turn['judged']} judged "
            f"(floor {THRESHOLDS['turn_precision_floor']}).\n**Acceptance:** run `tune` against "
            "the grown eval set; adopt a non-degrading config or re-cut thresholds (FR-SL1, NFR-11).",
        ))
    if card["quarantined"] > THRESHOLDS["quarantine"]:
        drafts.append(_draft(
            "chore: review the quarantine backlog",
            f"**Observed:** {card['quarantined']} quarantined rows.\n**Acceptance:** each "
            "released (`policy release`) or confirmed (`policy hostile`); labels feed FR-SL4.",
        ))
    if card["consolidation_backlog"] > THRESHOLDS["consolidation_backlog"]:
        drafts.append(_draft(
            "chore: consolidation backlog over threshold",
            f"**Observed:** {card['consolidation_backlog']} unconsolidated episodics.\n"
            "**Acceptance:** run `autoconsolidate --questions <eval set>` (gated) or a manual "
            "/memory consolidate pass; backlog under 25.",
        ))
    return drafts


def emit(drafts: list[dict], cfg: dict | None = None, drafts_dir: Path | None = None,
         post_direct: bool = False) -> dict:
    if cfg is None:
        cfg = config.load()
    mode = "direct" if (post_direct and cfg.get("observer_post") == "direct") else "draft"
    written, posted = [], []
    if mode == "direct":
        for d in drafts:
            proc = subprocess.run(
                ["gh", "issue", "create", "-R", "ddmsolutions/ai-memory",
                 "-t", d["title"], "-b", d["body"], "-l", "learning"],
                capture_output=True, text=True,
            )
            (posted if proc.returncode == 0 else written).append(d["title"])
        if not written:
            return {"mode": mode, "posted": posted, "drafted": []}
    target = drafts_dir or (Path.home() / ".ai-memory" / "drafts")
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    for d in drafts:
        slug = re.sub(r"[^a-z0-9]+", "-", d["title"].lower())[:50].strip("-")
        path = target / f"{stamp}-{slug}.md"
        path.write_text(f"# {d['title']}\n\n{d['body']}\n", encoding="utf-8")
        written.append(str(path))
    return {"mode": mode, "posted": posted, "drafted": written}
