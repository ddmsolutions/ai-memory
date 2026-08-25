"""Eval harness (FR-M2, NFR-11): recall quality measured, not vibes.

Runs a labelled question set against the store's retrieval path and reports
hit rate and mean reciprocal rank. Evaluation reads only: it never bumps
recall counters or reinforces confidence, so measuring cannot distort the
thing being measured.

Question file format (JSON list):
  [{"id": "q1", "query": "staging database version",
    "expect": "postgres 16", "scope": "optional-scope"}]
`expect` is a case-insensitive substring the top-k results must contain.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import store


def run_eval(conn: sqlite3.Connection, questions: list[dict], k: int = 5) -> dict:
    results = []
    for q in questions:
        rows = store.search(conn, q["query"], scope=q.get("scope"), limit=k)
        expect = q["expect"].lower()
        rank = next(
            (i for i, r in enumerate(rows, start=1) if expect in r["content"].lower()),
            None,
        )
        results.append({
            "id": q.get("id", q["query"][:40]),
            "query": q["query"],
            "hit": rank is not None,
            "rank": rank,
            "returned": len(rows),
        })
    n = len(results)
    hits = sum(1 for r in results if r["hit"])
    mrr = sum(1.0 / r["rank"] for r in results if r["rank"]) / n if n else 0.0
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "k": k,
        "questions": n,
        "hits": hits,
        "hit_rate": round(hits / n, 4) if n else 0.0,
        "mrr": round(mrr, 4),
        "misses": [r["id"] for r in results if not r["hit"]],
        "results": results,
    }


def run_eval_file(
    conn: sqlite3.Connection,
    questions_path: Path,
    k: int = 5,
    out_path: Path | None = None,
) -> dict:
    questions = json.loads(Path(questions_path).read_text(encoding="utf-8"))
    if not isinstance(questions, list):
        raise ValueError("questions file must be a JSON list")
    report = run_eval(conn, questions, k=k)
    if out_path:
        Path(out_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
