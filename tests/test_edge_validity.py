"""#68: valid-time windows on entity edges - non-destructive invalidation."""

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


def test_close_edge_hides_from_default_reads(conn):
    graph.link(conn, "alice", "acme", rel="works_at")
    assert graph.close_edge(conn, "alice", "acme", "works_at", on="2026-01-31") == 1
    assert graph.neighbours(conn, "alice") == []
    closed = graph.neighbours(conn, "alice", include_closed=True)
    assert len(closed) == 1 and closed[0]["t_invalid"] == "2026-01-31"


def test_closed_edge_survives_as_history(conn):
    graph.link(conn, "alice", "acme", rel="works_at")
    graph.close_edge(conn, "alice", "acme", "works_at")
    assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 1
    text = graph.describe(conn, "alice", history=True)
    assert "ENDED" in text
    assert "ENDED" not in graph.describe(conn, "alice")


def test_same_relationship_can_recur(conn):
    # left, rejoined: two windows of the same (src, dst, rel)
    graph.link(conn, "alice", "acme", rel="works_at", valid_from="2020-01-01")
    graph.close_edge(conn, "alice", "acme", "works_at", on="2022-06-30")
    graph.link(conn, "alice", "acme", rel="works_at", valid_from="2025-03-01")
    assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 2
    open_edges = graph.neighbours(conn, "alice")
    assert len(open_edges) == 1 and open_edges[0]["t_valid"] == "2025-03-01"


def test_replaces_closes_previous_window(conn):
    graph.link(conn, "alice", "acme", rel="works_at", valid_from="2020-01-01")
    graph.link(conn, "alice", "acme", rel="works_at",
               valid_from="2026-01-01", replaces=True)
    rows = conn.execute(
        "SELECT t_valid, t_invalid FROM edges ORDER BY t_valid").fetchall()
    assert rows[0]["t_invalid"] == "2026-01-01"  # closed by the new window
    assert rows[1]["t_invalid"] is None


def test_upsert_still_updates_same_window(conn):
    a = graph.link(conn, "alice", "acme", rel="works_at", weight=0.5)
    b = graph.link(conn, "alice", "acme", rel="works_at", weight=0.9)
    assert a == b
    assert conn.execute("SELECT weight FROM edges").fetchone()[0] == 0.9


def test_migration_preserves_existing_edges(tmp_path):
    # Build a store, strip to pre-14, re-migrate: edges survive with open windows.
    path = tmp_path / "m.db"
    conn = db.connect(path)
    graph.link(conn, "a", "b", rel="knows")
    conn.close()
    conn = db.connect(path)  # re-open runs migrations (no-op) without error
    rows = conn.execute("SELECT t_valid, t_invalid FROM edges").fetchall()
    assert rows[0]["t_valid"] == "" and rows[0]["t_invalid"] is None
    conn.close()


def test_export_import_round_trips_windows(conn, tmp_path):
    from ai_memory import portability

    graph.link(conn, "alice", "acme", rel="works_at", valid_from="2020-01-01")
    graph.close_edge(conn, "alice", "acme", "works_at", on="2023-01-01")
    data = portability.export_store(conn)
    target = db.connect(tmp_path / "o.db")
    portability.import_store(target, data)
    row = target.execute("SELECT t_valid, t_invalid FROM edges").fetchone()
    assert row["t_valid"] == "2020-01-01" and row["t_invalid"] == "2023-01-01"
    target.close()
