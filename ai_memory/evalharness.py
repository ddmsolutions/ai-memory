"""Eval harness (FR-M2, NFR-11): recall quality measured, not vibes.

Runs a labelled question set against the store's retrieval path and reports
hit rate and mean reciprocal rank. Evaluation reads only: it never bumps
recall counters or reinforces confidence, so measuring cannot distort the
thing being measured.

Question file format (JSON list):
  [{"id": "q1", "query": "staging database version",
    "expect": "postgres 16", "scope": "optional-scope",
    "surface": "search"}]
`expect` is a case-insensitive substring the results must contain; `avoid`
(instead of expect) passes when the substring does NOT surface, the negative
question shape generated from not-useful feedback. `surface` is "search"
(default, FTS/hybrid top-k) or "pack" (a sessionless recall-pack compile,
which exercises the ranking tunables).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import store


def _judge(conn: sqlite3.Connection, q: dict, k: int, cfg: dict | None) -> tuple[bool, int | None, int]:
    surface = q.get("surface", "search")
    if surface == "pack":
        pack = store.recall_pack(
            conn, task=q["query"], scope=q.get("scope", "global"), cfg=cfg
        ).lower()
        if "avoid" in q:
            return q["avoid"].lower() not in pack, None, pack.count("\n- ")
        return q["expect"].lower() in pack, (1 if q["expect"].lower() in pack else None), pack.count("\n- ")
    rows = store.search(conn, q["query"], scope=q.get("scope"), limit=k, cfg=cfg)
    if "avoid" in q:
        found = any(q["avoid"].lower() in r["content"].lower() for r in rows)
        return not found, None, len(rows)
    expect = q["expect"].lower()
    rank = next(
        (i for i, r in enumerate(rows, start=1) if expect in r["content"].lower()), None
    )
    return rank is not None, rank, len(rows)


def run_eval(
    conn: sqlite3.Connection, questions: list[dict], k: int = 5, cfg: dict | None = None
) -> dict:
    results = []
    for q in questions:
        hit, rank, returned = _judge(conn, q, k, cfg)
        results.append({
            "id": q.get("id", q["query"][:40]),
            "query": q["query"],
            "hit": hit,
            "rank": rank,
            "returned": returned,
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


def grow_questions(conn: sqlite3.Connection, out_path: Path, days: int = 30) -> dict:
    """FR-SL2: the eval set grows from real failures. Not-useful traces become
    avoid-questions; re-explanations become expect-questions for the memory
    that should have surfaced. Generated questions are deduped and reviewable
    before joining the canonical set."""
    out_path = Path(out_path)
    existing: list[dict] = []
    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
    seen = {q["id"] for q in existing}
    added = []
    for trace in conn.execute(
        "SELECT * FROM recall_trace WHERE was_useful = 0"
        " AND created_at >= datetime('now', ?)", (f"-{int(days)} days",)
    ):
        qid = f"trace-{trace['id']}"
        if qid in seen or not trace["cue"]:
            continue
        injected = json.loads(trace["injected"])
        if not injected:
            continue
        row = conn.execute(
            "SELECT content FROM memories WHERE id = ?", (injected[0],)
        ).fetchone()
        if row is None:
            continue
        added.append({
            "id": qid, "query": trace["cue"], "avoid": row["content"][:40],
            "surface": "search", "source": "not-useful feedback",
        })
        seen.add(qid)
    for pair in store.detect_reexplanations(conn, days=days):
        qid = f"reexp-{pair['old_id']}-{pair['new_id']}"
        if qid in seen:
            continue
        terms = sorted(store._significant_terms(pair["new_content"]))[:8]
        added.append({
            "id": qid, "query": " ".join(terms), "expect": pair["old_content"][:40],
            "surface": "search", "source": "re-explanation",
        })
        seen.add(qid)
    if added:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(existing + added, indent=1), encoding="utf-8")
    return {"added": len(added), "total": len(existing) + len(added), "path": str(out_path)}


def run_eval_file(
    conn: sqlite3.Connection,
    questions_path: Path,
    k: int = 5,
    out_path: Path | None = None,
    cfg: dict | None = None,
) -> dict:
    questions = json.loads(Path(questions_path).read_text(encoding="utf-8"))
    if not isinstance(questions, list):
        raise ValueError("questions file must be a JSON list")
    report = run_eval(conn, questions, k=k, cfg=cfg)
    if out_path:
        Path(out_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
