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


def _age(conn, mid, days=90):
    conn.execute("UPDATE memories SET created_at = datetime('now', ?) WHERE id=?",
                 (f"-{days} days", mid))
    conn.commit()


def test_old_unpromoted_unrecalled_deleted(conn):
    mid = store.remember(conn, "forgettable episode", mtype="episodic")
    _age(conn, mid)
    gone = store.decay(conn, CFG)
    assert [r["id"] for r in gone] == [mid]
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_each_disqualifier_protects(conn):
    pinned = store.remember(conn, "pinned episode", mtype="episodic", pinned=True)
    promoted = store.remember(conn, "promoted episode", mtype="episodic")
    recalled = store.remember(conn, "recalled episode", mtype="episodic")
    young = store.remember(conn, "young episode", mtype="episodic")
    for mid in (pinned, promoted, recalled):
        _age(conn, mid)
    store.promote(conn, promoted, "semantic")
    conn.execute("UPDATE memories SET recall_count = 2 WHERE id=?", (recalled,))
    conn.commit()
    store.decay(conn, CFG)
    survivors = {r["id"] for r in conn.execute("SELECT id FROM memories WHERE type='episodic'")}
    assert {pinned, promoted, recalled, young} <= survivors


def test_dry_run_deletes_nothing(conn):
    mid = store.remember(conn, "would decay", mtype="episodic")
    _age(conn, mid)
    listed = store.decay(conn, CFG, dry_run=True)
    assert [r["id"] for r in listed] == [mid]
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_durable_types_never_decay(conn):
    s = store.remember(conn, "ancient fact", mtype="semantic")
    p = store.remember(conn, "ancient rule", mtype="procedural")
    for mid in (s, p):
        _age(conn, mid, days=999)
    assert store.decay(conn, CFG) == []
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 2


def test_reinforcement_caps_at_one(conn):
    store.remember(conn, "reinforced fact about widgets", mtype="semantic", confidence=0.95)
    cfg = dict(CFG)
    cfg["reinforce_step"] = 0.04
    for i in range(4):
        store.turn_recall(conn, "widgets fact reinforced", session_id=f"s{i}", cfg=cfg)
    conf = conn.execute("SELECT confidence FROM memories").fetchone()[0]
    assert conf == 1.0


def test_fts_cleaned_on_decay(conn):
    mid = store.remember(conn, "vanishing zorble episode", mtype="episodic")
    _age(conn, mid)
    store.decay(conn, CFG)
    assert store.search(conn, "zorble") == []
