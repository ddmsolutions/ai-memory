import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import db, graph, store  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    yield c
    c.close()


def test_mention_links_and_autocreates_entity(conn):
    mid = store.remember(conn, "Evander needs an ERP decision by Q2", mtype="semantic")
    graph.mention(conn, mid, "Evander", etype="org")
    rows = graph.memories_about(conn, "evander")
    assert len(rows) == 1 and rows[0]["id"] == mid
    assert graph.find_entity(conn, "Evander")["etype"] == "org"


def test_mention_rejects_missing_memory(conn):
    with pytest.raises(ValueError):
        graph.mention(conn, 999, "Nobody")


def test_memories_about_excludes_superseded(conn):
    old = store.remember(conn, "Acme budget is 50k", mtype="semantic")
    graph.mention(conn, old, "Acme")
    new = store.remember(conn, "Acme budget is 30k", mtype="semantic", supersedes=old)
    graph.mention(conn, new, "Acme")
    contents = [r["content"] for r in graph.memories_about(conn, "Acme")]
    assert contents == ["Acme budget is 30k"]


def test_purge_entity_erases_everything(conn, tmp_path):
    mid = store.remember(conn, "secret client Zorgcorp pays in gold", mtype="semantic")
    graph.mention(conn, mid, "Zorgcorp", etype="org")
    graph.link(conn, "Zorgcorp", "gold-ledger", rel="uses")
    keep = store.remember(conn, "unrelated fact survives", mtype="semantic")
    report = graph.purge_subject(conn, entity_name="Zorgcorp")
    assert report["memories"] == 1 and report["entities"] == 1 and report["edges"] == 1
    assert store.search(conn, "Zorgcorp") == []
    assert graph.find_entity(conn, "Zorgcorp") is None
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    raw = (tmp_path / "m.db").read_bytes()
    assert b"Zorgcorp" not in raw and b"pays in gold" not in raw


def test_purge_session(conn):
    store.remember(conn, "session memo one", mtype="episodic", origin_session="doomed")
    store.remember(conn, "session memo two", mtype="episodic", origin_session="doomed")
    store.remember(conn, "other session memo", mtype="episodic", origin_session="fine")
    report = graph.purge_subject(conn, session_id="doomed")
    assert report["memories"] == 2
    left = [r["content"] for r in conn.execute("SELECT content FROM memories")]
    assert left == ["other session memo"]


def test_purge_dry_run_deletes_nothing(conn):
    mid = store.remember(conn, "still here", mtype="semantic")
    graph.mention(conn, mid, "Keeper")
    report = graph.purge_subject(conn, entity_name="Keeper", dry_run=True)
    assert report["dry_run"] and report["memories"] == 1
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    assert graph.find_entity(conn, "Keeper") is not None


def test_purge_requires_a_target(conn):
    with pytest.raises(ValueError):
        graph.purge_subject(conn)
