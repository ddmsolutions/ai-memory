import io
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

from ai_memory import config, db, store  # noqa: E402
import capture  # noqa: E402

CFG = dict(config.DEFAULTS)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    yield c
    c.close()


def test_migration_adds_columns_to_existing_store(tmp_path):
    path = tmp_path / "m.db"
    conn = db.connect(path)
    store.remember(conn, "pre-existing row", mtype="semantic")
    conn.execute(f"PRAGMA user_version = 2")
    conn.close()
    conn = db.connect(path)  # migration 3 re-applies views over new columns
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    row = conn.execute("SELECT valence, verify_by FROM v_active_memories").fetchone()
    assert row["valence"] is None and row["verify_by"] is None


def test_valence_via_cli_param_and_validation(conn):
    mid = store.remember(conn, "the refactor attempt failed", mtype="episodic", valence="failure")
    assert conn.execute("SELECT valence FROM memories WHERE id=?", (mid,)).fetchone()[0] == "failure"
    with pytest.raises(ValueError):
        store.remember(conn, "bad", mtype="episodic", valence="meh")


def test_valence_from_memo_syntax(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"message": {"role": "assistant", "content": [
        {"type": "text", "text": "```memo\noutcome: tried X, broke prod\nvalence: failure\n```"}]}}),
        encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "s", "transcript_path": str(t)})))
    assert capture.main() == 0
    conn = db.connect(tmp_path / "m.db")
    assert conn.execute("SELECT valence FROM memories").fetchone()[0] == "failure"


def test_overdue_fact_flagged_in_recall(conn):
    past = (date.today() - timedelta(days=10)).isoformat()
    future = (date.today() + timedelta(days=90)).isoformat()
    store.remember(conn, "old claim about the API", mtype="semantic", verify_by=past)
    store.remember(conn, "fresh claim about the API", mtype="semantic", verify_by=future)
    pack = store.recall_pack(conn, cfg=CFG)
    assert f"(VERIFY: unconfirmed since {past})" in pack
    assert pack.count("VERIFY:") == 1
    turn = store.turn_recall(conn, "claim about the API", session_id="s1", cfg=CFG)
    assert "VERIFY:" in turn


def test_consolidate_listing_surfaces_valence(conn, capsys):
    from ai_memory.__main__ import main as cli_main

    store.remember(conn, "deploy went wrong", mtype="episodic", valence="failure")
    cli_main(["--db", str(conn.execute("PRAGMA database_list").fetchone()[2]), "consolidate"])
    assert "[failure]" in capsys.readouterr().out
