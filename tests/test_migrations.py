import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import db, store  # noqa: E402
from ai_memory.__main__ import main as cli_main  # noqa: E402


def test_fresh_init_stamps_current_version(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_pre_versioning_store_is_adopted(tmp_path):
    path = tmp_path / "m.db"
    conn = db.connect(path)
    store.remember(conn, "old fact", mtype="semantic")
    # simulate a true pre-versioning v0.1 store: no migration artefacts, version 0
    conn.execute("DROP TABLE injection_log")
    conn.execute("PRAGMA user_version = 0")
    conn.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='injection_log'").fetchone()[0] == 1


def test_migrations_apply_in_order_exactly_once(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    db.connect(path).close()
    base = db.SCHEMA_VERSION
    monkeypatch.setattr(db, "MIGRATIONS", {
        base + 1: ["CREATE TABLE mig_a (x INTEGER)"],
        base + 2: ["INSERT INTO mig_a (x) VALUES (1)"],
    })
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == base + 2
    assert conn.execute("SELECT COUNT(*) FROM mig_a").fetchone()[0] == 1
    conn.close()
    conn = db.connect(path)  # re-connect must not re-run
    assert conn.execute("SELECT COUNT(*) FROM mig_a").fetchone()[0] == 1


def test_failed_migration_rolls_back_completely(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    conn = db.connect(path)
    store.remember(conn, "survives", mtype="semantic")
    conn.close()
    monkeypatch.setattr(db, "MIGRATIONS", {
        db.SCHEMA_VERSION + 1: ["CREATE TABLE mig_ok (x INTEGER)", "INSERT INTO does_not_exist VALUES (1)"],
    })
    with pytest.raises(db.MigrationError):
        db.connect(path)
    monkeypatch.setattr(db, "MIGRATIONS", dict(db.MIGRATIONS) if False else {})
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='mig_ok'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_cli_fails_loud_on_migration_error(tmp_path, monkeypatch, capsys):
    path = tmp_path / "m.db"
    db.connect(path).close()
    monkeypatch.setattr(db, "MIGRATIONS", {db.SCHEMA_VERSION + 1: ["INSERT INTO nope VALUES (1)"]})
    rc = cli_main(["--db", str(path), "status"])
    assert rc == 1
    assert "migration to schema version" in capsys.readouterr().err


def test_hooks_fail_soft_on_migration_error(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    db.connect(path).close()
    monkeypatch.setenv("AI_MEMORY_DB", str(path))
    monkeypatch.setattr(db, "MIGRATIONS", {db.SCHEMA_VERSION + 1: ["INSERT INTO nope VALUES (1)"]})
    sys.path.insert(0, str(ROOT / "hooks"))
    import session_start

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s"})))
    assert session_start.main() == 0


def test_migration_15_runs_against_a_populated_edges_table():
    """Regression: every migration test above starts from an empty store, so a
    constraint that only bites when rows exist shipped unnoticed. SQLite runs a
    full-table scan for an ADD COLUMN carrying a CHECK; a NOT NULL column added
    earlier in the same migration reads as NULL on existing rows during that
    scan, killing the upgrade for anyone with edges already in their graph."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT NOT NULL);
        CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE edges (
          id         INTEGER PRIMARY KEY,
          src        INTEGER NOT NULL REFERENCES entities(id),
          dst        INTEGER NOT NULL REFERENCES entities(id),
          rel        TEXT NOT NULL,
          weight     REAL NOT NULL DEFAULT 1.0,
          memory_id  INTEGER REFERENCES memories(id),
          t_valid    TEXT,
          t_invalid  TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO entities (id, name) VALUES (1, 'A'), (2, 'B');
        INSERT INTO edges (src, dst, rel) VALUES (1, 2, 'knows'), (2, 1, 'knows');
        """
    )

    for statement in db.MIGRATIONS[15]:
        conn.execute(statement)

    row = conn.execute(
        "SELECT source, status, confidence FROM edges ORDER BY id"
    ).fetchone()
    assert row == ("manual", "active", 0.9)
    assert conn.execute(
        "SELECT COUNT(*) FROM edges WHERE source IS NULL OR status IS NULL"
        " OR confidence IS NULL"
    ).fetchone()[0] == 0
