import sys
from datetime import date, timedelta
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


def test_time_intention_surfaces_when_due_and_fires_once(conn):
    past = (date.today() - timedelta(days=1)).isoformat()
    store.intend(conn, "rotate the OAuth token", "time", past)
    pack = store.recall_pack(conn, cfg=CFG, session_id="s1")
    assert "Pending intentions" in pack and "rotate the OAuth token" in pack
    assert "Pending intentions" not in store.recall_pack(conn, cfg=CFG, session_id="s2")  # fired once


def test_future_intention_stays_silent(conn):
    future = (date.today() + timedelta(days=30)).isoformat()
    store.intend(conn, "renew the certificate", "time", future)
    assert "renew the certificate" not in store.recall_pack(conn, cfg=CFG)


def test_context_intention_fires_on_matching_prompt(conn):
    store.intend(conn, "remember to bump user_version", "context", "schema migration")
    out = store.turn_recall(conn, "planning the schema change for tomorrow", session_id="s", cfg=CFG)
    assert "[INTENTION] remember to bump user_version" in out
    again = store.turn_recall(conn, "more schema work", session_id="s", cfg=CFG)
    assert "[INTENTION]" not in again  # fired once


def test_done_and_expired_leave_all_packs(conn):
    past = (date.today() - timedelta(days=1)).isoformat()
    iid = store.intend(conn, "check the backup", "time", past)
    store.resolve_intention(conn, iid, "done")
    assert "check the backup" not in store.recall_pack(conn, cfg=CFG)


def test_rearm_returns_to_pending(conn):
    past = (date.today() - timedelta(days=1)).isoformat()
    iid = store.intend(conn, "chase the invoice", "time", past)
    store.recall_pack(conn, cfg=CFG, session_id="s1")  # fires it
    store.resolve_intention(conn, iid, "pending")
    assert "chase the invoice" in store.recall_pack(conn, cfg=CFG, session_id="s2")


def test_validation(conn):
    with pytest.raises(ValueError):
        store.intend(conn, "x", "time", "not-a-date")
    with pytest.raises(ValueError):
        store.intend(conn, "x", "context", "   ")
    with pytest.raises(ValueError):
        store.intend(conn, "x", "weekly", "monday")


def test_scoped_intention_respects_scope(conn):
    past = (date.today() - timedelta(days=1)).isoformat()
    store.intend(conn, "projA-only reminder", "time", past, scope="proja")
    assert "projA-only" not in store.recall_pack(conn, scope="projb", cfg=CFG, session_id="s1")
    assert "projA-only" in store.recall_pack(conn, scope="proja", cfg=CFG, session_id="s2")
