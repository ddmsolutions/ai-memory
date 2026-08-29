"""#80: typed entity mentions - stop the 'thing' default swamping the graph."""

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


def test_parse_typed_and_untyped_mix():
    pairs = graph.parse_entity_mentions(
        "outcome: x\nentities: Alice (person), Acme Ltd (company), some system")
    assert pairs == [("Alice", "person"), ("Acme Ltd", "company"),
                     ("some system", None)]


def test_parens_that_are_not_a_type_stay_in_the_name():
    # multi-word or long parenthetical is part of the name, not a type
    pairs = graph.parse_entity_mentions(
        "entities: graph.html (final version), report (v2 draft)")
    assert pairs == [("graph.html (final version)", None), ("report (v2 draft)", None)]


def test_parse_entity_names_backwards_compatible():
    assert graph.parse_entity_names(
        "entities: Alice (person), Acme") == ["Alice", "Acme"]


def test_capture_creates_typed_entities(conn):
    content = "outcome: met the client\nentities: Evander (company), Tom Henry (person)"
    mid = store.remember(conn, content)
    graph.mention_from_content(conn, mid, content)
    rows = dict(conn.execute("SELECT name, etype FROM entities").fetchall())
    assert rows == {"Evander": "company", "Tom Henry": "person"}


def test_typed_mention_upgrades_thing_in_place(conn):
    m1 = store.remember(conn, "first sighting\nentities: Sahil")
    graph.mention_from_content(conn, m1, "first sighting\nentities: Sahil")
    assert conn.execute("SELECT etype FROM entities").fetchone()[0] == "thing"
    m2 = store.remember(conn, "now we know\nentities: Sahil (person)")
    graph.mention_from_content(conn, m2, "now we know\nentities: Sahil (person)")
    assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 1
    assert conn.execute("SELECT etype FROM entities").fetchone()[0] == "person"


def test_established_type_never_churns(conn):
    graph.add_entity(conn, "Acme", etype="company")
    mid = store.remember(conn, "note\nentities: Acme (topic)")
    graph.mention_from_content(conn, mid, "note\nentities: Acme (topic)")
    assert conn.execute("SELECT etype FROM entities").fetchone()[0] == "company"


def test_upgrade_skips_on_typed_twin_collision(conn):
    graph.add_entity(conn, "Mercury", etype="thing")
    graph.add_entity(conn, "Mercury", etype="topic")
    mid = store.remember(conn, "note")
    # upgrading the thing row to topic would violate UNIQUE(name, etype): skip
    graph.mention(conn, mid, "Mercury", etype="topic")
    etypes = sorted(r[0] for r in conn.execute("SELECT etype FROM entities"))
    assert etypes == ["thing", "topic"]


def test_retype_repairs_existing_rows(conn):
    graph.add_entity(conn, "apply.py", etype="thing")
    report = graph.retype(conn, "apply.py", "file")
    assert report["changed"] and report["before"] == "thing"
    assert conn.execute("SELECT etype FROM entities").fetchone()[0] == "file"


def test_retype_collision_says_merge(conn):
    graph.add_entity(conn, "Mercury", etype="thing")
    graph.add_entity(conn, "Mercury", etype="topic")
    with pytest.raises(ValueError, match="entity merge"):
        graph.retype(conn, "Mercury", "topic")


def test_lint_nudges_once_over_threshold(conn):
    for i in range(6):
        graph.add_entity(conn, f"untyped-{i}", etype="thing")
    nudges = [f for f in store.lint(conn) if f["issue"] == "untyped_entities"]
    assert len(nudges) == 1
    assert "6 entities" in nudges[0]["detail"]


def test_lint_quiet_below_threshold(conn):
    graph.add_entity(conn, "one thing", etype="thing")
    assert not [f for f in store.lint(conn) if f["issue"] == "untyped_entities"]
