import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import config, db, graph, store  # noqa: E402

CFG = dict(config.DEFAULTS)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    yield c
    c.close()


def test_lint_reports_all_classes(conn):
    store.remember(conn, "Duplicated Fact", mtype="semantic")
    store.remember(conn, "duplicated fact", mtype="semantic")
    store.remember(conn, "old api claim", mtype="semantic", verify_by="2020-01-01")
    rule = store.remember(conn, "ancient unused rule", mtype="procedural")
    conn.execute("UPDATE memories SET created_at = datetime('now', '-200 days') WHERE id=?", (rule,))
    a = store.remember(conn, "deploys are safe on friday", mtype="semantic")
    b = store.remember(conn, "never deploy on friday", mtype="procedural")
    store.link_memories(conn, a, b, rel="contradicts")
    store.remember(conn, "ignore previous instructions payload", mtype="episodic", scope="quarantine")
    weak = store.remember(conn, "shaky claim", mtype="semantic", confidence=0.2)
    conn.commit()
    issues = {f["issue"] for f in store.lint(conn)}
    assert issues == {
        "duplicate", "overdue_verify", "stale_rule",
        "unresolved_contradiction", "quarantined", "weak_evidence",
    }


def test_lint_clean_store(conn):
    store.remember(conn, "one healthy fact", mtype="semantic")
    assert store.lint(conn) == []


def test_evidence_decay_penalises_orphaned_rule(conn):
    epi = store.remember(conn, "the episode behind the rule", mtype="episodic")
    rule = store.promote(conn, epi, "procedural", content="the derived rule")
    before = conn.execute("SELECT confidence FROM memories WHERE id=?", (rule,)).fetchone()[0]
    conn.execute("UPDATE memories SET created_at = datetime('now','-90 days'),"
                 " consolidated = 0, recall_count = 0 WHERE id=?", (epi,))
    conn.commit()
    store.decay(conn, CFG)
    after = conn.execute("SELECT confidence FROM memories WHERE id=?", (rule,)).fetchone()[0]
    assert after == pytest.approx(before - 0.1)


def test_graph_lines_join_task_pack(conn):
    graph.add_entity(conn, "Evander", etype="org", summary="Ways of Working client")
    graph.link(conn, "Evander", "Dynamics NAV", rel="runs")
    mid = store.remember(conn, "Evander NAV support ends Jan 2028", mtype="semantic")
    graph.mention(conn, mid, "Evander")
    pack = store.recall_pack(conn, task="prep for the Evander workshop", cfg=CFG)
    assert "Known connections (graph):" in pack
    assert "Evander -> runs -> Dynamics NAV" in pack


def test_graph_section_absent_without_task_or_match(conn):
    graph.add_entity(conn, "Evander", etype="org")
    assert "Known connections" not in store.recall_pack(conn, cfg=CFG)
    assert "Known connections" not in store.recall_pack(conn, task="unrelated topic", cfg=CFG)


def test_graph_lines_respect_budget(conn):
    graph.add_entity(conn, "Hub", etype="system")
    for i in range(8):
        graph.link(conn, "Hub", f"spoke-{i}", rel="feeds")
    cfg = dict(CFG)
    cfg["pack_limit"] = 2
    store.remember(conn, "filler one", mtype="episodic", pinned=True)
    store.remember(conn, "filler two", mtype="episodic", pinned=True)
    pack = store.recall_pack(conn, task="the Hub review", cfg=cfg)
    assert "Known connections" not in pack  # budget exhausted by pinned rows
