import json
import subprocess
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


def test_remember_and_search(conn):
    mid = store.remember(conn, "Staging DB is Postgres 16 on port 5433", mtype="semantic")
    hits = store.search(conn, "postgres staging")
    assert [h["id"] for h in hits] == [mid]


def test_supersession_hides_old_truth(conn):
    old = store.remember(conn, "Staging DB is Postgres 15", mtype="semantic")
    store.remember(conn, "Staging DB is Postgres 16", mtype="semantic", supersedes=old)
    hits = store.search(conn, "staging postgres")
    assert len(hits) == 1
    assert "16" in hits[0]["content"]


def test_recall_pack_priorities_and_counting(conn):
    store.remember(conn, "Never push the instance repo", mtype="procedural", pinned=True)
    store.remember(conn, "Run linters before commit", mtype="procedural")
    store.remember(conn, "Owner email is x@example.com", mtype="semantic")
    store.remember(conn, "Fixed the billing bug today", mtype="episodic")
    pack = store.recall_pack(conn, task="billing", session_id="s1")
    assert "Pinned" in pack and "Never push" in pack
    assert "- [20" in pack  # every line carries its recorded date (staleness discount)
    assert "procedural" in pack and "semantic" in pack.lower()
    assert "billing" in pack
    counted = conn.execute("SELECT COUNT(*) FROM memories WHERE recall_count > 0").fetchone()[0]
    assert counted >= 3


def test_promote_marks_consolidated_and_records_lineage(conn):
    eid = store.remember(conn, "User said always use British spelling", mtype="episodic")
    new_id = store.promote(conn, eid, "procedural", content="Use British spelling in all output")
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (eid,)).fetchone()
    assert row["consolidated"] == 1
    new = conn.execute("SELECT * FROM memories WHERE id = ?", (new_id,)).fetchone()
    assert new["type"] == "procedural"
    assert new["promoted_from"] == eid
    assert store.unconsolidated(conn) == []


def test_promoted_from_is_enforced_foreign_key(conn):
    import sqlite3 as sq
    with pytest.raises(sq.IntegrityError):
        conn.execute(
            "INSERT INTO memories (type, content, promoted_from) VALUES ('semantic', 'orphan', 9999)"
        )


def test_capture_dedups_by_origin_session(conn):
    store.remember(conn, "outcome: shipped", mtype="episodic", origin_session="s1")
    rows = conn.execute(
        "SELECT content FROM memories WHERE origin_session = ?", ("s1",)
    ).fetchall()
    assert [r["content"] for r in rows] == ["outcome: shipped"]


def test_scope_filtering(conn):
    store.remember(conn, "global fact about widgets", mtype="semantic")
    store.remember(conn, "reviewer-only fact about widgets", mtype="semantic", scope="reviewer")
    hits = store.search(conn, "widgets", scope="builder")
    assert len(hits) == 1
    hits = store.search(conn, "widgets", scope="reviewer")
    assert len(hits) == 2


def test_entity_graph_roundtrip(conn):
    graph.add_entity(conn, "Alice", etype="person", summary="Payments lead")
    graph.link(conn, "Alice", "payments-service", rel="maintains")
    ns = graph.neighbours(conn, "alice")
    assert ns and ns[0]["other"] == "payments-service"
    desc = graph.describe(conn, "Alice")
    assert "maintains" in desc and "Payments lead" in desc


def test_entity_upsert_no_duplicates(conn):
    a = graph.add_entity(conn, "Bob", etype="person")
    b = graph.add_entity(conn, "Bob", etype="person", summary="Ops")
    assert a == b
    assert graph.find_entity(conn, "bob")["summary"] == "Ops"


def test_cli_end_to_end(tmp_path):
    env_db = str(tmp_path / "cli.db")

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "ai_memory", "--db", env_db, *args],
            capture_output=True, text=True, cwd=ROOT, check=True,
        ).stdout

    run("init")
    run("remember", "CLI smoke fact", "--type", "semantic")
    assert "CLI smoke fact" in run("search", "smoke")
    counts = json.loads(run("status"))
    assert counts["semantic"] == 1


def test_active_view_excludes_superseded(conn):
    old = store.remember(conn, "fact v1", mtype="semantic")
    store.remember(conn, "fact v2", mtype="semantic", supersedes=old)
    contents = [r["content"] for r in conn.execute("SELECT content FROM v_active_memories")]
    assert "fact v2" in contents and "fact v1" not in contents


def test_backlog_view_tracks_unconsolidated_only(conn):
    done = store.remember(conn, "raw episode", mtype="episodic")
    store.promote(conn, done, "semantic")
    store.remember(conn, "another episode", mtype="episodic")
    backlog = [r["content"] for r in conn.execute("SELECT content FROM v_consolidation_backlog")]
    assert backlog == ["another episode"]


def test_edges_named_view(conn):
    graph.link(conn, "Alice", "payments-service", rel="maintains")
    row = conn.execute("SELECT * FROM v_edges_named").fetchone()
    assert (row["src_name"], row["rel"], row["dst_name"]) == ("Alice", "maintains", "payments-service")


def test_hot_queries_use_their_indexes(conn):
    dedup_plan = " ".join(
        r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT content FROM memories WHERE origin_session = ?", ("s",)
        )
    )
    assert "idx_memories_origin_session" in dedup_plan
    entity_plan = " ".join(
        r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM entities WHERE name = ? COLLATE NOCASE", ("x",)
        )
    )
    assert "idx_entities_name_nocase" in entity_plan


def test_capture_hook_extracts_memos(tmp_path):
    transcript = tmp_path / "t.jsonl"
    lines = [
        json.dumps({"message": {"role": "user", "content": "hi"}}),
        json.dumps({"message": {"role": "assistant", "content": [
            {"type": "text", "text": "Done.\n```memo\noutcome: shipped the thing\n```\n"}
        ]}}),
    ]
    transcript.write_text("\n".join(lines), encoding="utf-8")
    sys.path.insert(0, str(ROOT / "hooks"))
    import capture

    memos = capture.extract_memos(str(transcript))
    assert memos == ["outcome: shipped the thing"]
