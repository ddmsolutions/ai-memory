"""#72: mention roles - subject vs mentioned."""

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


def test_first_entity_on_entities_line_is_subject(conn):
    content = "outcome: shipped\nentities: Evander, Tom Henry, Dynamics NAV"
    mid = store.remember(conn, content)
    graph.mention_from_content(conn, mid, content)
    rows = conn.execute(
        "SELECT e.name, me.role FROM memory_entities me"
        " JOIN entities e ON e.id = me.entity_id WHERE me.memory_id = ?"
        " ORDER BY e.id", (mid,)).fetchall()
    assert [(r["name"], r["role"]) for r in rows] == [
        ("Evander", "subject"), ("Tom Henry", "mentioned"),
        ("Dynamics NAV", "mentioned")]


def test_subject_rows_rank_first_in_about(conn):
    m1 = store.remember(conn, "passing mention of Acme in another story")
    m2 = store.remember(conn, "a full account of Acme's situation")
    graph.mention(conn, m1, "Acme", role="mentioned")
    graph.mention(conn, m2, "Acme", role="subject")
    about = graph.memories_about(conn, "Acme")
    assert about[0]["id"] == m2 and about[0]["mention_role"] == "subject"


def test_role_upgrades_but_never_downgrades(conn):
    mid = store.remember(conn, "a memory")
    graph.mention(conn, mid, "Acme", role="mentioned")
    graph.mention(conn, mid, "Acme", role="subject")
    assert conn.execute("SELECT role FROM memory_entities").fetchone()[0] == "subject"
    graph.mention(conn, mid, "Acme", role="mentioned")  # no silent downgrade
    assert conn.execute("SELECT role FROM memory_entities").fetchone()[0] == "subject"


def test_invalid_role_rejected(conn):
    mid = store.remember(conn, "a memory")
    with pytest.raises(ValueError):
        graph.mention(conn, mid, "Acme", role="star")


def test_promote_inherits_roles(conn):
    content = "outcome: learned\nentities: Acme"
    mid = store.remember(conn, content)
    graph.mention_from_content(conn, mid, content)
    new_id = store.promote(conn, mid, "semantic", content="Acme fact distilled")
    role = conn.execute(
        "SELECT role FROM memory_entities WHERE memory_id = ?", (new_id,)).fetchone()
    assert role[0] == "subject"


def test_roles_round_trip_export_import(conn, tmp_path):
    from ai_memory import portability

    mid = store.remember(conn, "about acme")
    graph.mention(conn, mid, "Acme", role="subject")
    data = portability.export_store(conn)
    target = db.connect(tmp_path / "o.db")
    portability.import_store(target, data)
    assert target.execute("SELECT role FROM memory_entities").fetchone()[0] == "subject"
    target.close()
