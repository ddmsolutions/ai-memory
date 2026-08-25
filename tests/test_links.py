import io
import json
import sys
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


def test_curated_link_and_candidate_set(conn):
    a = store.remember(conn, "NAV goes end of life Jan 2028", mtype="semantic")
    b = store.remember(conn, "client must select an ERP in 2026", mtype="semantic")
    c = store.remember(conn, "we proposed the selection engagement", mtype="episodic")
    store.link_memories(conn, b, a, rel="derives_from", weight=0.9)
    store.link_memories(conn, c, b, rel="follows", weight=0.85)
    candidates = store.related(conn, b, cfg=CFG)
    assert len(candidates) == 2
    assert candidates[0]["score"] >= candidates[1]["score"]
    assert candidates[1]["ambiguous_with_top"] is True  # 0.85 vs 0.9 within 15%


def test_link_validation(conn):
    a = store.remember(conn, "one", mtype="semantic")
    with pytest.raises(ValueError):
        store.link_memories(conn, a, a, rel="supports")
    with pytest.raises(ValueError):
        store.link_memories(conn, a, a + 1, rel="causes")


def test_hebbian_reinforce_asymptotic(conn):
    a = store.remember(conn, "alpha", mtype="semantic")
    b = store.remember(conn, "beta", mtype="semantic")
    for _ in range(50):
        store.reinforce_link(conn, a, b, "co_session", cfg=CFG)
    w = conn.execute("SELECT weight, reinforce_count FROM memory_links").fetchone()
    assert 0.99 < w["weight"] <= 1.0 and w["reinforce_count"] == 50


def test_co_session_links_from_capture(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    lines = [json.dumps({"message": {"role": "assistant", "content": [
        {"type": "text", "text": f"```memo\nmemo number {i}\n```"}]}}) for i in range(3)]
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "s", "transcript_path": str(t)})))
    assert capture.main() == 0
    conn = db.connect(tmp_path / "m.db")
    n = conn.execute("SELECT COUNT(*) FROM memory_links WHERE rel='co_session'").fetchone()[0]
    assert n == 3  # 3 memos pairwise: (1,2) (1,3) (2,3)


def test_co_retrieval_reinforces(conn):
    store.remember(conn, "widget fact alpha", mtype="semantic")
    store.remember(conn, "widget fact beta", mtype="semantic")
    store.turn_recall(conn, "widget fact", session_id="s1", cfg=CFG)
    row = conn.execute("SELECT * FROM memory_links WHERE rel='co_session'").fetchone()
    assert row is not None


def test_decay_prunes_faded_links(conn):
    a = store.remember(conn, "old one", mtype="semantic")
    b = store.remember(conn, "old two", mtype="semantic")
    store.reinforce_link(conn, a, b, "co_session", cfg=CFG)
    conn.execute("UPDATE memory_links SET last_reinforced = datetime('now', '-900 days'),"
                 " weight = 0.3")
    conn.commit()
    store.decay(conn, CFG)
    assert conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0] == 0


def test_strong_links_survive_decay(conn):
    a = store.remember(conn, "keep one", mtype="semantic")
    b = store.remember(conn, "keep two", mtype="semantic")
    store.link_memories(conn, a, b, rel="supports", weight=0.9)
    store.decay(conn, CFG)
    assert conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0] == 1
