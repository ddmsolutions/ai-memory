import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

from ai_memory import config, db, store  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    yield c
    c.close()


CFG = dict(config.DEFAULTS)


def test_relevant_memory_injected(conn):
    store.remember(conn, "staging database is postgres 16", mtype="semantic")
    out = store.turn_recall(conn, "what version is the staging database", session_id="s1", cfg=CFG)
    assert "postgres 16" in out and "- [20" in out


def test_no_match_injects_nothing(conn):
    store.remember(conn, "staging database is postgres 16", mtype="semantic")
    assert store.turn_recall(conn, "completely unrelated topic xyzzy", session_id="s1", cfg=CFG) == ""


def test_session_dedup(conn):
    store.remember(conn, "staging database is postgres 16", mtype="semantic")
    first = store.turn_recall(conn, "staging database", session_id="s1", cfg=CFG)
    second = store.turn_recall(conn, "staging database", session_id="s1", cfg=CFG)
    assert "postgres" in first and second == ""


def test_pack_rows_not_reinjected(conn):
    store.remember(conn, "always run the linter first", mtype="procedural")
    pack = store.recall_pack(conn, cfg=CFG, session_id="s1")
    assert "linter" in pack
    assert store.turn_recall(conn, "run the linter", session_id="s1", cfg=CFG) == ""


def test_superseded_never_injected(conn):
    old = store.remember(conn, "api endpoint is v1 legacy", mtype="semantic")
    store.remember(conn, "api endpoint is v2 current", mtype="semantic", supersedes=old)
    out = store.turn_recall(conn, "api endpoint version", session_id="s1", cfg=CFG)
    assert "v1 legacy" not in out and "v2 current" in out


def test_cap_respected(conn):
    for i in range(6):
        store.remember(conn, f"widget fact number {i}", mtype="semantic")
    cfg = dict(CFG)
    cfg["turn_recall_cap"] = 3
    out = store.turn_recall(conn, "widget fact", session_id="s1", cfg=cfg)
    assert out.count("widget fact") == 3


def test_counters_bump(conn):
    mid = store.remember(conn, "counters get bumped on injection", mtype="semantic")
    store.turn_recall(conn, "counters bumped injection", session_id="s1", cfg=CFG)
    row = conn.execute("SELECT recall_count, last_recalled_at FROM memories WHERE id=?", (mid,)).fetchone()
    assert row["recall_count"] == 1 and row["last_recalled_at"] is not None


def test_hook_fails_soft(monkeypatch, tmp_path):
    import user_prompt

    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "locked" / "nope" / "m.db"))
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    assert user_prompt.main() == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": "", "session_id": "s"})))
    assert user_prompt.main() == 0


def test_ranked_ordering_prefers_recent_and_used(conn):
    old = store.remember(conn, "stale widget guidance", mtype="procedural", confidence=0.8)
    fresh = store.remember(conn, "fresh widget guidance", mtype="procedural", confidence=0.8)
    conn.execute("UPDATE memories SET created_at = datetime('now', '-120 days') WHERE id=?", (old,))
    conn.execute("UPDATE memories SET recall_count = 5 WHERE id=?", (fresh,))
    conn.commit()
    pack = store.recall_pack(conn, cfg=CFG)
    assert pack.index("fresh widget guidance") < pack.index("stale widget guidance")
