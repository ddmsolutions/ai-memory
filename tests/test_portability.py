import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import db, graph, portability, store  # noqa: E402


def _build_source(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    old = store.remember(conn, "port is 5432", mtype="semantic")
    store.remember(conn, "port is 5433", mtype="semantic", supersedes=old)
    epi = store.remember(conn, "deploy broke", mtype="episodic",
                         origin_session="s1", valence="failure")
    rule = store.promote(conn, epi, "procedural", content="check the flag before deploy")
    graph.mention(conn, rule, "deploy-system", etype="system")
    graph.link(conn, "deploy-system", "prod-cluster", rel="targets")
    return conn


def test_round_trip_lossless(tmp_path):
    a = _build_source(tmp_path)
    data = portability.export_store(a)
    b = db.connect(tmp_path / "b.db")
    report = portability.import_store(b, data)
    assert report["imported"] == 4 and report["deduplicated"] == 0
    # supersession preserved: only the corrected fact is active
    contents = [r["content"] for r in b.execute("SELECT content FROM v_active_memories WHERE type='semantic'")]
    assert contents == ["port is 5433"]
    # promotion lineage preserved by content
    lineage = b.execute(
        "SELECT p.content FROM memories m JOIN memories p ON p.id = m.promoted_from"
        " WHERE m.content = 'check the flag before deploy'").fetchone()
    assert lineage["content"] == "deploy broke"
    # graph + mentions preserved
    assert graph.memories_about(b, "deploy-system")[0]["content"] == "check the flag before deploy"
    assert graph.neighbours(b, "deploy-system")[0]["other"] == "prod-cluster"
    # exports match apart from row ids
    a2, b2 = portability.export_store(a), portability.export_store(b)
    strip = lambda rows: sorted(
        tuple(sorted((k, v) for k, v in r.items()
                     if k not in ("id", "promoted_from", "superseded_by", "src", "dst",
                                  "memory_id", "entity_id")))
        for r in rows)
    for table in ("memories", "entities", "edges"):
        assert strip(a2[table]) == strip(b2[table])


def test_reimport_is_noop(tmp_path):
    a = _build_source(tmp_path)
    data = portability.export_store(a)
    report = portability.import_store(a, data)
    assert report["imported"] == 0 and report["deduplicated"] == 4
    assert a.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 4


def test_import_rejects_foreign_json(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    with pytest.raises(ValueError):
        portability.import_store(conn, {"format": "something-else"})


def test_file_round_trip(tmp_path):
    a = _build_source(tmp_path)
    out = tmp_path / "dump.json"
    portability.export_to_file(a, out)
    b = db.connect(tmp_path / "d.db")
    report = portability.import_from_file(b, out)
    assert report["imported"] == 4
