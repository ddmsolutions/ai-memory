import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import db, evalharness, store  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    store.remember(c, "staging database is postgres 16 on port 5433", mtype="semantic")
    store.remember(c, "run the schema linter before committing migrations", mtype="procedural")
    store.remember(c, "unrelated note about the office plants", mtype="episodic")
    yield c
    c.close()


QUESTIONS = [
    {"id": "db", "query": "staging database version", "expect": "postgres 16"},
    {"id": "lint", "query": "schema linter commit", "expect": "schema linter"},
    {"id": "miss", "query": "kubernetes ingress timeout", "expect": "nginx"},
]


def test_metrics_computed_correctly(conn):
    report = evalharness.run_eval(conn, QUESTIONS, k=5)
    assert report["questions"] == 3 and report["hits"] == 2
    assert report["hit_rate"] == round(2 / 3, 4)
    by_id = {r["id"]: r for r in report["results"]}
    assert by_id["db"]["rank"] == 1 and by_id["miss"]["rank"] is None
    assert report["misses"] == ["miss"]
    assert report["mrr"] == round((1.0 + 1.0 + 0.0) / 3, 4)


def test_eval_is_read_only(conn):
    evalharness.run_eval(conn, QUESTIONS, k=5)
    assert conn.execute(
        "SELECT COUNT(*) FROM memories WHERE recall_count > 0").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM memories WHERE confidence <> 0.7").fetchone()[0] == 0


def test_file_roundtrip_and_report(conn, tmp_path):
    qfile = tmp_path / "q.json"
    qfile.write_text(json.dumps(QUESTIONS), encoding="utf-8")
    out = tmp_path / "report.json"
    report = evalharness.run_eval_file(conn, qfile, k=3, out_path=out)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["hit_rate"] == report["hit_rate"]
    assert saved["k"] == 3 and "run_at" in saved


def test_invalid_questions_file_fails_loud(conn, tmp_path):
    qfile = tmp_path / "q.json"
    qfile.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(ValueError):
        evalharness.run_eval_file(conn, qfile)
