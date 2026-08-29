"""#67: dedupe-first consolidation - verbatim beats rewrite, no summary chains."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import autoconsolidate, db, store  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "memory.db")
    yield c
    c.close()


def test_promote_refuses_non_episodic_source(conn):
    epi = store.remember(conn, "an episode")
    fact = store.promote(conn, epi, "semantic", content="a distilled fact")
    with pytest.raises(ValueError, match="episodic"):
        store.promote(conn, fact, "procedural", content="summary of a summary")


def test_lint_flags_legacy_summary_chains(conn):
    epi = store.remember(conn, "an episode")
    fact = store.promote(conn, epi, "semantic")
    # simulate a legacy chain created before the guard
    grandchild = store.remember(conn, "chained summary", mtype="procedural",
                                promoted_from=fact)
    issues = {f["issue"] for f in store.lint(conn)}
    assert "summary_of_summary" in issues


def test_summarise_links_all_originals(conn):
    a = store.remember(conn, "deploy failed on friday")
    b = store.remember(conn, "deploy failed again saturday")
    c = store.remember(conn, "deploy fine after freeze lifted")
    new_id = store.summarise(conn, [a, b, c], "procedural",
                             "avoid deploying during the freeze window")
    rels = conn.execute(
        "SELECT dst_memory FROM memory_links WHERE src_memory = ?"
        " AND rel = 'derives_from' ORDER BY dst_memory", (new_id,)).fetchall()
    assert [r[0] for r in rels] == sorted([a, b, c])
    consolidated = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE consolidated = 1 AND type = 'episodic'"
    ).fetchone()[0]
    assert consolidated == 3


def test_summarise_carries_least_trusted_origin(conn):
    a = store.remember(conn, "one source", origin="agent")
    b = store.remember(conn, "web-derived source", origin="external")
    new_id = store.summarise(conn, [a, b], "semantic", "combined claim")
    assert conn.execute(
        "SELECT origin FROM memories WHERE id = ?", (new_id,)).fetchone()[0] == "external"


def test_summarise_refuses_single_source_and_non_episodic(conn):
    a = store.remember(conn, "one episode")
    with pytest.raises(ValueError, match="2\\+"):
        store.summarise(conn, [a], "semantic", "x")
    fact = store.promote(conn, a, "semantic")
    b = store.remember(conn, "another episode")
    with pytest.raises(ValueError, match="originals"):
        store.summarise(conn, [fact, b], "semantic", "x")


def test_autoconsolidate_dedupes_instead_of_forking(tmp_path):
    path = tmp_path / "m.db"
    conn = db.connect(path)
    store.remember(conn, "The staging DB is Postgres 16", mtype="semantic")
    epi = store.remember(conn, "confirmed again: staging is postgres 16")
    conn.close()

    def distiller(content):
        return ("semantic", "the staging db is postgres 16", True)

    report = autoconsolidate.run(path, distiller=distiller)
    assert report["distillation"]["deduped"] == 1
    assert report["distillation"]["promoted"] == 0
    check = db.connect(path)
    # no duplicate durable row was created
    n = check.execute(
        "SELECT COUNT(*) FROM v_active_memories WHERE type = 'semantic'").fetchone()[0]
    assert n == 1
    # the episode became evidence of the existing fact
    assert check.execute(
        "SELECT COUNT(*) FROM memory_links WHERE rel = 'derives_from'").fetchone()[0] == 1
    check.close()
