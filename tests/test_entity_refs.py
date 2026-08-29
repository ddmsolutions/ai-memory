"""#73: external identity refs - hard identifiers, unique store-wide."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import db, graph  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "memory.db")
    yield c
    c.close()


def test_ref_add_and_resolve(conn):
    graph.add_entity(conn, "DDM Solutions", etype="company")
    graph.add_ref(conn, "DDM Solutions", "domain", "ddmsolutions.co.uk")
    ent = graph.resolve_ref(conn, "domain", "ddmsolutions.co.uk")
    assert ent is not None and ent["name"] == "DDM Solutions"


def test_conflicting_ref_says_merge(conn):
    graph.add_entity(conn, "DDM", etype="company")
    graph.add_entity(conn, "DDM Solutions", etype="company")
    graph.add_ref(conn, "DDM", "company_no", "12345678")
    with pytest.raises(ValueError, match="entity merge"):
        graph.add_ref(conn, "DDM Solutions", "company_no", "12345678")


def test_same_entity_can_hold_multiple_refs(conn):
    graph.add_entity(conn, "DDM Solutions", etype="company")
    graph.add_ref(conn, "DDM Solutions", "domain", "ddmsolutions.co.uk")
    graph.add_ref(conn, "DDM Solutions", "company_no", "12345678")
    graph.add_ref(conn, "DDM Solutions", "domain", "ddm.example")  # second domain fine
    ent = graph.find_entity(conn, "DDM Solutions")
    assert len(graph.entity_refs(conn, ent["id"])) == 3


def test_merge_moves_refs_and_ref_follows_redirect(conn):
    graph.add_entity(conn, "DDM", etype="company")
    graph.add_entity(conn, "DDM Solutions", etype="company")
    graph.add_ref(conn, "DDM", "domain", "ddmsolutions.co.uk")
    graph.merge_entities(conn, "DDM", "DDM Solutions")
    ent = graph.resolve_ref(conn, "domain", "ddmsolutions.co.uk")
    assert ent["name"] == "DDM Solutions"


def test_describe_shows_refs(conn):
    graph.add_entity(conn, "DDM Solutions", etype="company")
    graph.add_ref(conn, "DDM Solutions", "domain", "ddmsolutions.co.uk")
    assert "domain=ddmsolutions.co.uk" in graph.describe(conn, "DDM Solutions")


def test_refs_round_trip_export_import(conn, tmp_path):
    from ai_memory import portability

    graph.add_entity(conn, "DDM Solutions", etype="company")
    graph.add_ref(conn, "DDM Solutions", "company_no", "12345678")
    data = portability.export_store(conn)
    target = db.connect(tmp_path / "o.db")
    portability.import_store(target, data)
    assert graph.resolve_ref(target, "company_no", "12345678")["name"] == "DDM Solutions"
    target.close()
