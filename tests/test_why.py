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


def test_why_full_story(conn):
    epi = store.remember(
        conn, "tried the flag, deploy failed", mtype="episodic",
        origin_session="sess-42", valence="failure",
    )
    rule = store.promote(conn, epi, "procedural", content="never deploy with the flag on")
    out = store.why(conn, rule)
    assert f"distilled from #{epi}" in out
    assert "tried the flag" in out
    assert "recalled 0 time(s), never" in out
    out_epi = store.why(conn, epi)
    assert "captured from session sess-42" in out_epi
    assert "valence failure" in out_epi
    assert f"promoted into #{rule}" in out_epi


def test_why_correction_chain_both_directions(conn):
    old = store.remember(conn, "port is 5432", mtype="semantic")
    new = store.remember(conn, "port is 5433", mtype="semantic", supersedes=old)
    assert f"SUPERSEDED by #{new}" in store.why(conn, old)
    assert f"corrects #{old}" in store.why(conn, new)


def test_why_shows_mentions_and_missing_id(conn):
    mid = store.remember(conn, "Evander decision pending", mtype="semantic")
    graph.mention(conn, mid, "Evander", etype="org")
    assert "mentions: Evander (org)" in store.why(conn, mid)
    assert "No memory with id 999" in store.why(conn, 999)


def test_why_after_ancestor_forgotten(conn):
    # forget() hard-deletes; ON DELETE SET NULL erases the lineage pointer,
    # so the story cleanly shows no promotion trail (and never crashes).
    epi = store.remember(conn, "raw episode", mtype="episodic")
    rule = store.promote(conn, epi, "semantic")
    store.forget(conn, epi)
    out = store.why(conn, rule)
    assert "distilled from" not in out and out.startswith(f"**#{rule}**")
