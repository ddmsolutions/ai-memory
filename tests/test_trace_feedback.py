import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import config, db, store  # noqa: E402

CFG = dict(config.DEFAULTS)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    yield c
    c.close()


def test_turn_recall_writes_trace_with_ids_not_content(conn):
    store.remember(conn, "sensitive widget deployment fact", mtype="semantic")
    store.turn_recall(conn, "widget deployment", session_id="s1", cfg=CFG)
    trace = conn.execute("SELECT * FROM recall_trace").fetchone()
    assert trace["surface"] == "turn" and trace["session_id"] == "s1"
    assert "sensitive" not in trace["candidates"] and "sensitive" not in trace["injected"]
    assert json.loads(trace["injected"])


def test_pack_writes_trace_only_with_session(conn):
    store.remember(conn, "pinned trace fact", mtype="procedural", pinned=True)
    store.recall_pack(conn, cfg=CFG)  # sessionless preview: no trace
    assert conn.execute("SELECT COUNT(*) FROM recall_trace").fetchone()[0] == 0
    store.recall_pack(conn, cfg=CFG, session_id="s1")
    trace = conn.execute("SELECT * FROM recall_trace").fetchone()
    assert trace["surface"] == "pack"


def test_useful_feedback_records_without_penalty(conn):
    mid = store.remember(conn, "useful fact about gadgets", mtype="semantic")
    store.turn_recall(conn, "gadgets fact", session_id="s1", cfg=CFG)
    before = conn.execute("SELECT confidence FROM memories WHERE id=?", (mid,)).fetchone()[0]
    tid = conn.execute("SELECT id FROM recall_trace").fetchone()[0]
    store.feedback(conn, tid, useful=True, cfg=CFG)
    assert conn.execute("SELECT was_useful FROM recall_trace").fetchone()[0] == 1
    after = conn.execute("SELECT confidence FROM memories WHERE id=?", (mid,)).fetchone()[0]
    assert after == before


def test_rejection_penalises_confidence_and_links(conn):
    a = store.remember(conn, "gizmo fact alpha", mtype="semantic")
    b = store.remember(conn, "gizmo fact beta", mtype="semantic")
    store.turn_recall(conn, "gizmo fact", session_id="s1", cfg=CFG)
    link_before = conn.execute(
        "SELECT weight FROM memory_links WHERE rel='co_session'").fetchone()[0]
    conf_before = conn.execute("SELECT confidence FROM memories WHERE id=?", (a,)).fetchone()[0]
    tid = conn.execute("SELECT id FROM recall_trace").fetchone()[0]
    report = store.feedback(conn, tid, useful=False, note="irrelevant", cfg=CFG)
    assert report["penalised_memories"] == 2
    conf_after = conn.execute("SELECT confidence FROM memories WHERE id=?", (a,)).fetchone()[0]
    assert conf_after == pytest.approx(conf_before - CFG["feedback_penalty"])
    link_after = conn.execute(
        "SELECT weight FROM memory_links WHERE rel='co_session'").fetchone()[0]
    assert link_after < link_before


def test_precision_view(conn):
    store.remember(conn, "fact one about sprockets", mtype="semantic")
    store.turn_recall(conn, "sprockets one", session_id="s1", cfg=CFG)
    store.turn_recall(conn, "sprockets again", session_id="s2", cfg=CFG)
    ids = [r[0] for r in conn.execute("SELECT id FROM recall_trace ORDER BY id")]
    store.feedback(conn, ids[0], useful=True, cfg=CFG)
    store.feedback(conn, ids[1], useful=False, cfg=CFG)
    row = conn.execute("SELECT * FROM v_recall_precision WHERE surface='turn'").fetchone()
    assert row["judged"] == 2 and row["precision"] == 0.5


def test_feedback_missing_trace(conn):
    with pytest.raises(ValueError):
        store.feedback(conn, 999, useful=True, cfg=CFG)


def test_traces_purged_at_retention(conn):
    store.remember(conn, "temp fact widgets", mtype="semantic")
    store.turn_recall(conn, "temp widgets", session_id="s1", cfg=CFG)
    conn.execute("UPDATE recall_trace SET created_at = datetime('now', '-60 days')")
    conn.commit()
    store.decay(conn, CFG)
    assert conn.execute("SELECT COUNT(*) FROM recall_trace").fetchone()[0] == 0
