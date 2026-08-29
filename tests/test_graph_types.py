"""#70: graph type registry - governed ontology, is_a, strict mode, lint."""

import json
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


@pytest.fixture
def strict(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"graph_strict": True}), encoding="utf-8")
    monkeypatch.setenv("AI_MEMORY_CONFIG", str(cfg))


def test_core_ontology_seeded(conn):
    assert graph.get_type(conn, "entity", "person") is not None
    assert graph.get_type(conn, "edge", "works_at") is not None
    company = graph.get_type(conn, "entity", "company")
    assert company["is_a"] == "organisation"


def test_type_family_expands_is_a(conn):
    fam = graph.type_family(conn, "entity", "organisation")
    assert {"organisation", "company", "team"} <= fam


def test_add_type_requires_registered_parent(conn):
    with pytest.raises(ValueError):
        graph.add_type(conn, "entity", "starship", is_a="vehicle")
    graph.add_type(conn, "entity", "vehicle")
    graph.add_type(conn, "entity", "starship", is_a="vehicle")
    assert "starship" in graph.type_family(conn, "entity", "vehicle")


def test_permissive_by_default(conn):
    # unknown types warn via lint, never block writes
    graph.add_entity(conn, "X", etype="made_up_type")
    issues = [f for f in store.lint(conn) if f["issue"] == "unregistered_type"]
    assert any("made_up_type" in f["detail"] for f in issues)


def test_strict_mode_refuses_unknown_type(conn, strict):
    with pytest.raises(ValueError, match="unregistered"):
        graph.add_entity(conn, "X", etype="made_up_type")
    with pytest.raises(ValueError, match="unregistered"):
        graph.link(conn, "a", "b", rel="made_up_rel")


def test_strict_mode_refuses_retired_and_abstract(conn, strict):
    graph.retire_type(conn, "entity", "place")
    with pytest.raises(ValueError, match="retired"):
        graph.add_entity(conn, "Hinckley", etype="place")
    graph.add_type(conn, "entity", "actor", abstract=True)
    with pytest.raises(ValueError, match="abstract"):
        graph.add_entity(conn, "X", etype="actor")


def test_strict_mode_accepts_registered(conn, strict):
    graph.add_entity(conn, "Alice", etype="person")
    graph.add_entity(conn, "Acme", etype="company")
    graph.link(conn, "Alice", "Acme", rel="works_at")


def test_lint_endpoint_violation_is_a_aware(conn):
    graph.add_entity(conn, "Alice", etype="person")
    graph.add_entity(conn, "Acme", etype="company")
    graph.add_entity(conn, "Widget", etype="thing")
    # company is_a organisation: allowed for works_at (dst organisation)
    graph.link(conn, "Alice", "Acme", rel="works_at")
    # thing is not an organisation: violation
    graph.link(conn, "Alice", "Widget", rel="works_at")
    violations = [f for f in store.lint(conn) if f["issue"] == "edge_endpoint_violation"]
    assert len(violations) == 1
    assert "Widget" in violations[0]["detail"]


def test_graph_types_round_trip(conn, tmp_path):
    from ai_memory import portability

    graph.add_type(conn, "entity", "custom_kind", description="mine")
    data = portability.export_store(conn)
    target = db.connect(tmp_path / "o.db")
    portability.import_store(target, data)
    assert graph.get_type(target, "entity", "custom_kind") is not None
    target.close()
