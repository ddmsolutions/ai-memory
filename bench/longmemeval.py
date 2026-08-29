"""#66: LongMemEval adapter - an externally comparable retrieval number,
measured through the REAL pipeline, not a bespoke harness.

For each benchmark instance the adapter builds a FRESH store, ingests every
haystack session turn through store.remember (the production insert funnel:
redaction, screening, the lot), then runs the production hybrid search with
the benchmark question as the cue and scores whether the evidence turns (the
dataset's has_answer marks) were retrieved.

This is RETRIEVAL-level evaluation (evidence recall@k + MRR), not end-to-end
answer accuracy: no model in the loop, fully offline, reproducible. The
numbers are comparable to the retrieval components of published systems, with
the standard caveat that these benchmarks are easy to overfit and all scores
are directional.

The dataset is NOT bundled (licence + size). Download LongMemEval-S from the
benchmark's own repository (https://github.com/xiaowu0162/LongMemEval) and:

  python bench/longmemeval.py --data longmemeval_s.json --limit 50 --k 8
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import config, db, store  # noqa: E402


def _turn_text(turn: dict) -> str:
    role = turn.get("role", "user")
    return f"[{role}] {turn.get('content', '')}".strip()


def ingest_instance(conn, instance: dict) -> tuple[int, set[int]]:
    """Ingest every haystack turn through the production funnel. Returns
    (rows_ingested, evidence_memory_ids)."""
    evidence_ids: set[int] = set()
    ingested = 0
    session_ids = instance.get("haystack_session_ids") or []
    for s_index, session in enumerate(instance.get("haystack_sessions") or []):
        session_id = str(session_ids[s_index]) if s_index < len(session_ids) else f"s{s_index}"
        for turn in session:
            text = _turn_text(turn)
            if not text or len(text) < 8:
                continue
            mid = store.remember(
                conn, text, mtype="episodic", origin_session=session_id
            )
            ingested += 1
            if turn.get("has_answer"):
                evidence_ids.add(mid)
    return ingested, evidence_ids


def evaluate_instance(instance: dict, k: int, cfg: dict) -> dict | None:
    evidence_turns = sum(
        1 for session in (instance.get("haystack_sessions") or [])
        for turn in session if turn.get("has_answer")
    )
    if evidence_turns == 0:
        return None  # abstention instances have no retrievable evidence
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.connect(Path(tmp) / "bench.db")
        ingested, evidence_ids = ingest_instance(conn, instance)
        if not evidence_ids:
            conn.close()
            return None
        started = time.perf_counter()
        rows = store.search(conn, instance.get("question", ""), limit=k, cfg=cfg)
        latency_ms = (time.perf_counter() - started) * 1000
        conn.close()
    hit_rank = next(
        (rank for rank, row in enumerate(rows, start=1) if row["id"] in evidence_ids),
        None,
    )
    tokens = sum(len(r["content"]) for r in rows) // 4
    return {
        "question_id": instance.get("question_id"),
        "question_type": instance.get("question_type", "unknown"),
        "ingested": ingested,
        "hit": hit_rank is not None,
        "reciprocal_rank": (1.0 / hit_rank) if hit_rank else 0.0,
        "retrieved_tokens": tokens,
        "latency_ms": round(latency_ms, 1),
    }


def run(data_path: Path, limit: int, k: int) -> dict:
    instances = json.loads(Path(data_path).read_text(encoding="utf-8"))
    if not isinstance(instances, list):
        raise SystemExit("error: expected a JSON list of LongMemEval instances")
    cfg = config.load()
    results = []
    skipped = 0
    for instance in instances[: limit or len(instances)]:
        outcome = evaluate_instance(instance, k, cfg)
        if outcome is None:
            skipped += 1
            continue
        results.append(outcome)
        print(f"  {outcome['question_id']}: "
              f"{'HIT' if outcome['hit'] else 'miss'}"
              f" (rr {outcome['reciprocal_rank']:.2f},"
              f" {outcome['ingested']} rows ingested)", file=sys.stderr)
    if not results:
        raise SystemExit("error: no scorable instances (abstention-only slice?)")
    by_type: dict[str, list] = {}
    for r in results:
        by_type.setdefault(r["question_type"], []).append(r)
    return {
        "benchmark": "LongMemEval (retrieval-level adapter, #66)",
        "caveat": "evidence recall@k + MRR through the production pipeline;"
                  " NOT end-to-end answer accuracy; benchmark scores are"
                  " directional and easy to overfit",
        "k": k,
        "questions": len(results),
        "skipped_no_evidence": skipped,
        "evidence_recall_at_k": round(sum(r["hit"] for r in results) / len(results), 4),
        "mrr": round(sum(r["reciprocal_rank"] for r in results) / len(results), 4),
        "avg_retrieved_tokens": round(sum(r["retrieved_tokens"] for r in results) / len(results)),
        "avg_search_latency_ms": round(sum(r["latency_ms"] for r in results) / len(results), 1),
        "by_type": {
            t: {
                "questions": len(rs),
                "evidence_recall_at_k": round(sum(r["hit"] for r in rs) / len(rs), 4),
                "mrr": round(sum(r["reciprocal_rank"] for r in rs) / len(rs), 4),
            }
            for t, rs in sorted(by_type.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, required=True,
                        help="LongMemEval instances JSON (download separately)")
    parser.add_argument("--limit", type=int, default=50,
                        help="instances to run (0 = all; default 50)")
    parser.add_argument("--k", type=int, default=8, help="retrieval depth")
    parser.add_argument("--out", type=Path, help="write the JSON report here")
    args = parser.parse_args()
    report = run(args.data, args.limit, args.k)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
