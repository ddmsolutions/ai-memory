"""#65: safety-triggered forgetting - quarantine cascade over lineage."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import db, graph, policy, store  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "memory.db")
    yield c
    c.close()


def test_cascade_follows_promotion_chain(conn):
    root = store.remember(conn, "poisoned episodic memo")
    child = store.promote(conn, root, "semantic", content="poisoned fact")
    report = store.quarantine_cascade(conn, root)
    assert set(report["memories"]) == {root, child}
    scopes = {r[0] for r in conn.execute("SELECT scope FROM memories")}
    assert scopes == {"quarantine"}


def test_cascade_follows_derives_from_links(conn):
    origin = store.remember(conn, "bad premise")
    conclusion = store.remember(conn, "conclusion built on it", mtype="semantic")
    store.link_memories(conn, conclusion, origin, "derives_from")
    report = store.quarantine_cascade(conn, origin)
    assert set(report["memories"]) == {origin, conclusion}


def test_cascade_is_transitive(conn):
    a = store.remember(conn, "root memo aaa")
    b = store.promote(conn, a, "semantic", content="level one bbb")
    # derives_from off the promoted row: c derived from b
    c = store.remember(conn, "level two ccc", mtype="procedural")
    store.link_memories(conn, c, b, "derives_from")
    report = store.quarantine_cascade(conn, a)
    assert set(report["memories"]) == {a, b, c}


def test_cascade_leaves_unrelated_rows_alone(conn):
    bad = store.remember(conn, "bad root")
    good = store.remember(conn, "innocent bystander", mtype="semantic")
    store.quarantine_cascade(conn, bad)
    assert conn.execute(
        "SELECT scope FROM memories WHERE id = ?", (good,)).fetchone()[0] == "global"


def test_cascade_suspends_contaminated_machine_edges(conn):
    m = store.remember(conn, "evidence memo")
    graph.link(conn, "a", "b", rel="knows", source="extract", memory_id=m)
    report = store.quarantine_cascade(conn, m)
    assert report["edges_suspended"] == 1
    assert graph.neighbours(conn, "a") == []


def test_dry_run_changes_nothing(conn):
    root = store.remember(conn, "suspect memo")
    child = store.promote(conn, root, "semantic")
    report = store.quarantine_cascade(conn, root, dry_run=True)
    assert set(report["memories"]) == {root, child}
    assert conn.execute(
        "SELECT COUNT(*) FROM memories WHERE scope = 'quarantine'").fetchone()[0] == 0


def test_quarantined_rows_leave_recall_surfaces(conn):
    root = store.remember(conn, "hostile distinctive zebra fact", mtype="semantic")
    assert store.search(conn, "zebra")
    store.quarantine_cascade(conn, root)
    assert not store.search(conn, "zebra")


def test_release_path_still_works_after_cascade(conn):
    root = store.remember(conn, "false positive memo")
    store.quarantine_cascade(conn, root)
    policy.release(conn, root, scope="global")
    assert conn.execute(
        "SELECT scope FROM memories WHERE id = ?", (root,)).fetchone()[0] == "global"


def test_policy_sweep_cascades_pattern_hits(conn):
    bad = store.remember(conn, "the magic phrase xyzzy plugh")
    child = store.promote(conn, bad, "semantic", content="derived from xyzzy source")
    good = store.remember(conn, "clean unrelated row", mtype="semantic")
    report = policy.sweep(conn, r"xyzzy")
    assert bad in report["quarantined"] and child in report["quarantined"]
    assert good not in report["quarantined"]


def test_policy_sweep_rejects_bad_regex(conn):
    with pytest.raises(ValueError):
        policy.sweep(conn, "(unclosed")
