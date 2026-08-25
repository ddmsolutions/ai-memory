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
    conn.execute("PRAGMA user_version = 0")
    conn.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_migrations_apply_in_order_exactly_once(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    db.connect(path).close()
    monkeypatch.setattr(db, "MIGRATIONS", {
        2: ["CREATE TABLE mig_a (x INTEGER)"],
        3: ["INSERT INTO mig_a (x) VALUES (1)"],
    })
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
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
        2: ["CREATE TABLE mig_ok (x INTEGER)", "INSERT INTO does_not_exist VALUES (1)"],
    })
    with pytest.raises(db.MigrationError):
        db.connect(path)
    monkeypatch.setattr(db, "MIGRATIONS", {})
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='mig_ok'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_cli_fails_loud_on_migration_error(tmp_path, monkeypatch, capsys):
    path = tmp_path / "m.db"
    db.connect(path).close()
    monkeypatch.setattr(db, "MIGRATIONS", {2: ["INSERT INTO nope VALUES (1)"]})
    rc = cli_main(["--db", str(path), "status"])
    assert rc == 1
    assert "migration to schema version 2" in capsys.readouterr().err


def test_hooks_fail_soft_on_migration_error(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    db.connect(path).close()
    monkeypatch.setenv("AI_MEMORY_DB", str(path))
    monkeypatch.setattr(db, "MIGRATIONS", {2: ["INSERT INTO nope VALUES (1)"]})
    sys.path.insert(0, str(ROOT / "hooks"))
    import session_start

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s"})))
    assert session_start.main() == 0
