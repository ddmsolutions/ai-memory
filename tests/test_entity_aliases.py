"""#69: entity aliases, ambiguity as a candidate set, merge tombstones."""

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


def test_normalise_alias():
    assert graph.normalise_alias("D.D.M.  Solutions!") == "d d m solutions"
    assert graph.normalise_alias("  DDM \t Solutions ") == "ddm solutions"


def test_alias_resolves_to_canonical(conn):
    graph.add_entity(conn, "DDM Solutions", etype="company")
    graph.add_alias(conn, "ddm", "DDM Solutions")
    ent = graph.find_entity(conn, "DDM")  # normalised lookup
    assert ent is not None and ent["name"] == "DDM Solutions"


def test_exact_canonical_beats_alias(conn):
    graph.add_entity(conn, "AV", etype="thing")
    graph.add_entity(conn, "Assured Velocity", etype="company")
    graph.add_alias(conn, "AV", "Assured Velocity")
    # exact canonical match wins over the alias
    assert graph.find_entity(conn, "AV")["name"] == "AV"


def test_ambiguous_alias_raises_with_candidates(conn):
    graph.add_entity(conn, "Assured Velocity", etype="company")
    graph.add_entity(conn, "Anti Virus", etype="thing")
    graph.add_alias(conn, "avx", "Assured Velocity")
    graph.add_alias(conn, "avx", "Anti Virus")
    with pytest.raises(graph.AmbiguousEntity) as exc:
        graph.find_entity(conn, "avx")
    names = {c["name"] for c in exc.value.candidates}
    assert names == {"Assured Velocity", "Anti Virus"}


def test_headless_capture_links_nothing_on_ambiguity(conn):
    graph.add_entity(conn, "Assured Velocity", etype="company")
    graph.add_entity(conn, "Anti Virus", etype="thing")
    graph.add_alias(conn, "avz", "Assured Velocity")
    graph.add_alias(conn, "avz", "Anti Virus")
    mid = store.remember(conn, "outcome: did the thing\nentities: avz, NewCo")
    added = graph.mention_from_content(
        conn, mid, "outcome: did the thing\nentities: avz, NewCo")
    # avz skipped (ambiguous), NewCo auto-created and linked
    assert added == 1
    linked = conn.execute(
        "SELECT e.name FROM memory_entities me JOIN entities e ON e.id = me.entity_id"
        " WHERE me.memory_id = ?", (mid,)).fetchall()
    assert [r["name"] for r in linked] == ["NewCo"]


def test_lint_reports_ambiguous_alias(conn):
    graph.add_entity(conn, "A Corp", etype="company")
    graph.add_entity(conn, "B Corp", etype="company")
    graph.add_alias(conn, "abc", "A Corp")
    graph.add_alias(conn, "abc", "B Corp")
    issues = {f["issue"] for f in store.lint(conn)}
    assert "ambiguous_alias" in issues


def test_suffix_stripping_only_suggests(conn):
    graph.add_entity(conn, "DDM Solutions", etype="company")
    result = graph.resolve(conn, "DDM Solutions Limited")
    assert result["entity"] is None  # never auto-linked
    assert [s["name"] for s in result["suggestions"]] == ["DDM Solutions"]


def test_merge_repoints_everything_and_leaves_redirect(conn):
    m = store.remember(conn, "a fact about the split entity")
    graph.add_entity(conn, "DDM", etype="company")
    graph.add_entity(conn, "DDM Solutions", etype="company")
    graph.mention(conn, m, "DDM")
    graph.link(conn, "DDM", "Richard", rel="owned_by")
    report = graph.merge_entities(conn, "DDM", "DDM Solutions")
    assert report["mentions_moved"] == 1
    # everything about the winner now includes the loser's history
    about = graph.memories_about(conn, "DDM Solutions")
    assert [r["id"] for r in about] == [m]
    # the loser's name resolves to the winner (alias + redirect)
    assert graph.find_entity(conn, "DDM")["name"] == "DDM Solutions"
    loser = conn.execute(
        "SELECT status, merged_into FROM entities WHERE name = 'DDM'").fetchone()
    assert loser["status"] == "merged" and loser["merged_into"] is not None
    # edges repointed
    assert any(n["other"] == "Richard" for n in graph.neighbours(conn, "DDM Solutions"))


def test_merge_same_entity_rejected(conn):
    graph.add_entity(conn, "X", etype="thing")
    with pytest.raises(ValueError):
        graph.merge_entities(conn, "X", "X")


def test_purge_reaches_entity_via_alias(conn):
    m = store.remember(conn, "sensitive fact\nentities: MegaCorp")
    graph.mention_from_content(conn, m, "sensitive fact\nentities: MegaCorp")
    graph.add_alias(conn, "MC", "MegaCorp")
    report = graph.purge_subject(conn, entity_name="MC", dry_run=False)
    assert report["memories"] == 1 and report["entities"] == 1
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_aliases_round_trip_export_import(conn, tmp_path):
    from ai_memory import portability

    graph.add_entity(conn, "DDM Solutions", etype="company")
    graph.add_alias(conn, "ddm", "DDM Solutions")
    data = portability.export_store(conn)
    target = db.connect(tmp_path / "o.db")
    portability.import_store(target, data)
    assert graph.find_entity(target, "ddm")["name"] == "DDM Solutions"
    target.close()
