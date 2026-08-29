"""#71: edge provenance - source channel, confidence, evidence sets."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import db, graph, store  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "memory.db")
    yield c
    c.close()


def test_source_channel_sets_default_confidence(conn):
    graph.link(conn, "alice", "payments", rel="maintains", source="extract")
    row = conn.execute("SELECT source, confidence FROM edges").fetchone()
    assert row["source"] == "extract" and row["confidence"] == pytest.approx(0.6)


def test_invalid_source_rejected(conn):
    with pytest.raises(ValueError):
        graph.link(conn, "a", "b", rel="x", source="hearsay")


def test_evidence_accumulates_and_corroboration_reinforces(conn):
    m1 = store.remember(conn, "alice fixed payments today")
    m2 = store.remember(conn, "alice on payments rota again")
    eid = graph.link(conn, "alice", "payments", rel="maintains",
                     source="consolidate", memory_id=m1)
    base = conn.execute("SELECT confidence FROM edges").fetchone()[0]
    assert base == pytest.approx(0.7)  # first evidence priced into the default
    graph.add_edge_evidence(conn, eid, m2)
    after = conn.execute("SELECT confidence FROM edges").fetchone()[0]
    assert after > base
    assert conn.execute("SELECT COUNT(*) FROM edge_sources").fetchone()[0] == 2


def test_edge_why_shows_provenance(conn):
    m1 = store.remember(conn, "observed alice maintaining payments")
    graph.link(conn, "alice", "payments", rel="maintains",
               source="consolidate", memory_id=m1)
    text = graph.edge_why(conn, "alice", "maintains", "payments")
    assert "source consolidate" in text
    assert "evidence #" in text


def test_suspend_only_when_all_evidence_quarantined(conn):
    m1 = store.remember(conn, "evidence one")
    m2 = store.remember(conn, "evidence two")
    e1 = graph.link(conn, "a", "b", rel="knows", source="extract", memory_id=m1)
    graph.add_edge_evidence(conn, e1, m2)
    # only one of two evidence memories quarantined: edge stands
    assert graph.suspend_edges_for_memories(conn, [m1]) == 0
    # both gone: edge suspended
    assert graph.suspend_edges_for_memories(conn, [m1, m2]) == 1
    assert graph.neighbours(conn, "a") == []


def test_manual_edges_never_suspended(conn):
    m1 = store.remember(conn, "the only evidence")
    graph.link(conn, "a", "b", rel="knows", source="manual", memory_id=m1)
    assert graph.suspend_edges_for_memories(conn, [m1]) == 0
    assert len(graph.neighbours(conn, "a")) == 1


def test_lint_flags_machine_edge_with_no_evidence(conn):
    graph.link(conn, "a", "b", rel="knows", source="extract")
    issues = {f["issue"] for f in store.lint(conn)}
    assert "edge_evidence_gone" in issues
    # a manual edge with no evidence is fine: it stands on the human's word
    graph.link(conn, "c", "d", rel="knows", source="manual")
    gone = [f for f in store.lint(conn) if f["issue"] == "edge_evidence_gone"]
    assert len(gone) == 1


def test_export_import_round_trips_provenance(conn, tmp_path):
    from ai_memory import portability

    m1 = store.remember(conn, "evidence for the edge")
    graph.link(conn, "alice", "payments", rel="maintains",
               source="consolidate", memory_id=m1)
    data = portability.export_store(conn)
    target = db.connect(tmp_path / "o.db")
    portability.import_store(target, data)
    row = target.execute("SELECT source, confidence FROM edges").fetchone()
    assert row["source"] == "consolidate"
    assert target.execute("SELECT COUNT(*) FROM edge_sources").fetchone()[0] == 1
    target.close()
