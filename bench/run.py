"""A/B benchmark: does Claude Code behave better WITH this memory than without?

Runs a probe battery twice under identical hooks: once against a seeded store,
once against an empty one, so memory content is the only treatment. Each probe
invocation gets a FRESH COPY of its arm's store (handoffs and intentions are
consume-once). The user's real store is never touched: the DB and config paths
are overridden per invocation.

Usage:
  python bench/run.py [--runs 2] [--model sonnet] [--out bench/report.json]

Scoring: a probe passes when `expect` appears in the response (case-insensitive)
and `forbid`, if set, does not. Control probes validate the harness (both arms
should pass); they are reported separately and excluded from the memory value.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import db, portability  # noqa: E402


def claude_runner(prompt: str, env: dict, model: str) -> tuple[str, int | None]:
    """Real engine: headless claude with JSON output for text + token usage."""
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--output-format", "json"],
        capture_output=True, text=True, env={**os.environ, **env}, timeout=300,
    )
    try:
        data = json.loads(proc.stdout)
        text = data.get("result", "")
        usage = data.get("usage") or {}
        tokens = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0) or None
    except Exception:
        text, tokens = proc.stdout, None
    return text, tokens


def score(text: str, probe: dict) -> bool:
    lowered = text.lower()
    if probe["expect"].lower() not in lowered:
        return False
    forbid = probe.get("forbid")
    return not (forbid and forbid.lower() in lowered)


def run_bench(
    probes: list[dict],
    seed: dict,
    workdir: Path,
    runs: int = 1,
    model: str = "sonnet",
    runner=claude_runner,
) -> dict:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    bench_config = workdir / "bench-config.json"
    bench_config.write_text("{}", encoding="utf-8")

    seeded_base = workdir / "seeded-base.db"
    conn = db.connect(seeded_base)
    portability.import_store(conn, seed)
    conn.close()
    empty_base = workdir / "empty-base.db"
    db.connect(empty_base).close()

    results: list[dict] = []
    counter = 0
    for arm, base in (("with", seeded_base), ("without", empty_base)):
        for probe in probes:
            for run_index in range(runs):
                counter += 1
                run_db = workdir / f"run-{counter}.db"
                shutil.copy(base, run_db)  # fresh copy: consume-once isolation
                env = {
                    "AI_MEMORY_DB": str(run_db),
                    "AI_MEMORY_CONFIG": str(bench_config),
                }
                text, tokens = runner(probe["prompt"], env, model)
                results.append({
                    "probe": probe["id"], "behaviour": probe["behaviour"],
                    "arm": arm, "run": run_index, "passed": score(text, probe),
                    "tokens": tokens, "db": str(run_db),
                })

    def acc(rows: list[dict]) -> float | None:
        return round(sum(r["passed"] for r in rows) / len(rows), 3) if rows else None

    behaviours: dict[str, dict] = {}
    for behaviour in sorted({p["behaviour"] for p in probes}):
        arm_rows = {
            arm: [r for r in results if r["behaviour"] == behaviour and r["arm"] == arm]
            for arm in ("with", "without")
        }
        behaviours[behaviour] = {
            "with": acc(arm_rows["with"]),
            "without": acc(arm_rows["without"]),
            "delta": round((acc(arm_rows["with"]) or 0) - (acc(arm_rows["without"]) or 0), 3),
        }
    memory_rows = [r for r in results if r["behaviour"] != "control"]
    token_mean = {
        arm: (lambda t: round(sum(t) / len(t)) if t else None)(
            [r["tokens"] for r in results if r["arm"] == arm and r["tokens"]])
        for arm in ("with", "without")
    }
    return {
        "runs_per_probe": runs,
        "model": model,
        "behaviours": behaviours,
        "memory_value": {
            "with": acc([r for r in memory_rows if r["arm"] == "with"]),
            "without": acc([r for r in memory_rows if r["arm"] == "without"]),
        },
        "harness_valid": behaviours.get("control", {}).get("with") == 1.0
        and behaviours.get("control", {}).get("without") == 1.0,
        "mean_tokens": token_mean,
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--probes", type=Path, default=ROOT / "bench" / "probes.json")
    ap.add_argument("--seed", type=Path, default=ROOT / "bench" / "seed.json")
    ap.add_argument("--out", type=Path, default=ROOT / "bench" / "report.json")
    args = ap.parse_args()
    if shutil.which("claude") is None:
        print("error: claude CLI not found on PATH", file=sys.stderr)
        return 1
    probes = json.loads(args.probes.read_text(encoding="utf-8"))
    seed = json.loads(args.seed.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="ai-memory-bench-") as tmp:
        report = run_bench(probes, seed, Path(tmp), runs=args.runs, model=args.model)
    for r in report["results"]:
        r.pop("db", None)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{'behaviour':<12} {'with':>6} {'without':>8} {'delta':>7}")
    for b, v in report["behaviours"].items():
        print(f"{b:<12} {v['with']:>6} {v['without']:>8} {v['delta']:>7}")
    print(f"\nmemory value (non-control): with {report['memory_value']['with']}"
          f" vs without {report['memory_value']['without']}")
    print(f"harness valid (controls pass both arms): {report['harness_valid']}")
    print(f"mean tokens: {report['mean_tokens']}")
    print(f"full report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
