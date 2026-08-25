import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from ai_memory import config, db, portability, store  # noqa: E402
import run as bench  # noqa: E402

CFG = dict(config.DEFAULTS)
SEED = json.loads((ROOT / "bench" / "seed.json").read_text(encoding="utf-8"))
PROBES = json.loads((ROOT / "bench" / "probes.json").read_text(encoding="utf-8"))


# --- export handoffs fix (#39) ---

def test_export_round_trips_open_handoff(tmp_path):
    a = db.connect(tmp_path / "a.db")
    store.handoff_write(a, "carry this state across")
    b = db.connect(tmp_path / "b.db")
    report = portability.import_store(b, portability.export_store(a))
    assert report["handoffs"] == 1
    assert b.execute("SELECT consumed_at FROM handoffs").fetchone()[0] is None


def test_consumed_handoff_round_trips_consumed(tmp_path):
    a = db.connect(tmp_path / "a.db")
    store.handoff_write(a, "already read state")
    store.recall_pack(a, cfg=CFG, session_id="reader")
    b = db.connect(tmp_path / "b.db")
    portability.import_store(b, portability.export_store(a))
    assert b.execute("SELECT consumed_by FROM handoffs").fetchone()[0] == "reader"
    assert "already read state" not in store.recall_pack(b, cfg=CFG, session_id="x")


def test_handoff_reimport_dedups(tmp_path):
    a = db.connect(tmp_path / "a.db")
    store.handoff_write(a, "dedup me")
    data = portability.export_store(a)
    report = portability.import_store(a, data)
    assert report["handoffs"] == 0
    assert a.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 1


# --- scorecard (#38) ---

def test_scorecard_counts_and_read_only(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    store.remember(conn, "fresh fact about widgets", mtype="semantic")
    store.turn_recall(conn, "widgets fact", session_id="s1", cfg=CFG)
    tid = conn.execute("SELECT id FROM recall_trace").fetchone()[0]
    store.feedback(conn, tid, useful=True, cfg=CFG)
    store.handoff_write(conn, "open note")
    store.intend(conn, "overdue thing", "time", "2020-01-01")
    before = conn.execute("SELECT SUM(recall_count), SUM(confidence) FROM memories").fetchone()
    card = store.scorecard(conn, days=7)
    assert card["injections"] == 1 and card["traces"] == 1 and card["traces_judged"] == 1
    assert card["precision_by_surface"]["turn"]["precision"] == 1.0
    assert card["new_memories"] == 1 and card["open_handoffs"] == 1 and card["due_intentions"] == 1
    after = conn.execute("SELECT SUM(recall_count), SUM(confidence) FROM memories").fetchone()
    assert tuple(before) == tuple(after)


def test_scorecard_window_filter(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    store.remember(conn, "old fact", mtype="semantic")
    conn.execute("UPDATE memories SET created_at = datetime('now', '-30 days')")
    conn.commit()
    assert store.scorecard(conn, days=7)["new_memories"] == 0


# --- bench harness (#37) ---

def test_scoring_expect_and_forbid():
    probe = {"expect": "30k", "forbid": "budget is 50k"}
    assert bench.score("The budget is 30k now.", probe)
    assert not bench.score("The budget is 50k.", probe)
    assert not bench.score("budget is 50k, though some say 30k", probe)


def _fake_runner_factory(seen):
    def fake(prompt, env, model):
        seen.append(dict(env))
        conn = db.connect(Path(env["AI_MEMORY_DB"]))
        n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()
        if "France" in prompt:
            return "Paris.", 10
        # a crude memory-aware fake: seeded stores answer, empty ones do not
        if n > 0:
            return "5433 hubspot maintenance feat colour 30k oauth tokenizer", 50
        return "I do not know.", 20
    return fake


def test_arms_isolated_and_aggregated(tmp_path):
    seen: list[dict] = []
    report = bench.run_bench(PROBES, SEED, tmp_path, runs=1,
                             runner=_fake_runner_factory(seen))
    dbs = {e["AI_MEMORY_DB"] for e in seen}
    assert len(dbs) == len(seen)  # fresh copy per invocation
    assert all("bench-config.json" in e["AI_MEMORY_CONFIG"] for e in seen)
    assert report["memory_value"]["with"] == 1.0
    assert report["memory_value"]["without"] == 0.0
    assert report["harness_valid"] is True
    assert report["behaviours"]["fact"]["delta"] == 1.0
    assert report["mean_tokens"]["with"] > 0


def test_control_separated_from_memory_value(tmp_path):
    seen: list[dict] = []
    report = bench.run_bench(PROBES, SEED, tmp_path, runs=1,
                             runner=_fake_runner_factory(seen))
    assert report["behaviours"]["control"]["delta"] == 0.0
    # controls pass both arms but do not inflate the without-arm memory score
    assert report["memory_value"]["without"] == 0.0


def test_seed_import_is_clean(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    report = portability.import_store(conn, SEED)
    assert report["imported"] == 7 and report["intentions"] == 1 and report["handoffs"] == 1
    assert report["quarantined"] == 0
    active = [r["content"] for r in conn.execute("SELECT content FROM v_active_memories")]
    assert not any("50k" == c[-3:] for c in active)  # superseded budget inactive
