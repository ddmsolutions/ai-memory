"""Premortem hardening regressions (#48, #50, #51)."""
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

from ai_memory import config, db, portability, store  # noqa: E402

CFG = dict(config.DEFAULTS)


# --- #51 exclude_paths boundary ---

def test_is_excluded_prefix_and_nesting(tmp_path):
    cfg = dict(CFG)
    cfg["exclude_paths"] = [str(tmp_path / "workspace")]
    (tmp_path / "workspace" / "deep").mkdir(parents=True)
    assert config.is_excluded(str(tmp_path / "workspace"), cfg)
    assert config.is_excluded(str(tmp_path / "workspace" / "deep"), cfg)
    assert not config.is_excluded(str(tmp_path), cfg)
    assert not config.is_excluded(None, cfg)


@pytest.mark.parametrize("hook_module,payload_extra", [
    ("session_start", {}),
    ("user_prompt", {"prompt": "anything relevant here"}),
    ("capture", {"transcript_path": "SET_BELOW"}),
    ("spawn_inject", {"tool_name": "Task", "tool_input": {"prompt": "review things"}}),
])
def test_hooks_silent_under_excluded_cwd(monkeypatch, tmp_path, capsys, hook_module, payload_extra):
    excluded = tmp_path / "workspace"
    excluded.mkdir()
    cfgfile = tmp_path / "cfg.json"
    cfgfile.write_text(json.dumps({"exclude_paths": [str(excluded)]}), encoding="utf-8")
    monkeypatch.setenv("AI_MEMORY_CONFIG", str(cfgfile))
    dbfile = tmp_path / "m.db"
    monkeypatch.setenv("AI_MEMORY_DB", str(dbfile))
    conn = db.connect(dbfile)
    store.remember(conn, "should never surface here", mtype="procedural", pinned=True)
    conn.close()
    if payload_extra.get("transcript_path") == "SET_BELOW":
        t = tmp_path / "t.jsonl"
        t.write_text(json.dumps({"message": {"role": "assistant", "content": [
            {"type": "text", "text": "```memo\nexcluded memo\n```"}]}}), encoding="utf-8")
        payload_extra["transcript_path"] = str(t)
    mod = __import__(hook_module)
    payload = {"session_id": "s", "cwd": str(excluded), **payload_extra}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert mod.main() == 0
    assert capsys.readouterr().out == ""
    conn = db.connect(dbfile)
    assert conn.execute(
        "SELECT COUNT(*) FROM memories WHERE content = 'excluded memo'").fetchone()[0] == 0


# --- #50 migration snapshot + backup ---

def test_migration_snapshots_existing_store(tmp_path, monkeypatch):
    path = tmp_path / "m.db"
    conn = db.connect(path)
    store.remember(conn, "precious pre-migration fact", mtype="semantic")
    conn.close()
    monkeypatch.setattr(db, "MIGRATIONS", {db.SCHEMA_VERSION + 1: ["CREATE TABLE mig_x (x INTEGER)"]})
    db.connect(path).close()
    bak = path.with_name(path.name + f".v{db.SCHEMA_VERSION}.bak")
    assert bak.exists()
    import sqlite3 as sq  # raw open: connect() would migrate the backup
    old = sq.connect(bak)
    assert old.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    assert old.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_fresh_init_snapshots_nothing(tmp_path):
    path = tmp_path / "fresh.db"
    db.connect(path).close()
    assert not list(tmp_path.glob("*.bak"))


def test_backup_round_trips(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    store.remember(conn, "backed up fact", mtype="semantic")
    from ai_memory.__main__ import main as cli_main
    rc = cli_main(["--db", str(tmp_path / "m.db"), "backup", "--out", str(tmp_path / "bk")])
    assert rc == 0
    files = list((tmp_path / "bk").glob("memory-*.json"))
    assert len(files) == 1
    fresh = db.connect(tmp_path / "b.db")
    report = portability.import_from_file(fresh, files[0])
    assert report["imported"] == 1


# --- #48 telemetry ---

def test_scorecard_telemetry_fields(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    store.remember(conn, "captured via hook", mtype="episodic", origin_session="s1")
    store.remember(conn, "searchable widget fact", mtype="semantic")
    store.turn_recall(conn, "widget fact", session_id="s1", cfg=CFG)
    card = store.scorecard(conn, days=7)
    assert card["days_since_last_capture"] is not None and card["days_since_last_capture"] < 1
    assert card["injected_tokens_estimate"] > 0
    assert card["recall_latency_ms"] >= 0


def test_scorecard_latency_probe_is_read_only(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    store.remember(conn, "pinned thing", mtype="procedural", pinned=True)
    before = conn.execute("SELECT recall_count, confidence FROM memories").fetchone()
    store.scorecard(conn)
    after = conn.execute("SELECT recall_count, confidence FROM memories").fetchone()
    assert tuple(before) == tuple(after)


def test_lint_no_capture_finding(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    issues = {f["issue"] for f in store.lint(conn)}
    assert "no_capture" in issues  # never captured
    store.remember(conn, "fresh capture", mtype="episodic", origin_session="s")
    issues = {f["issue"] for f in store.lint(conn)}
    assert "no_capture" not in issues
    conn.execute("UPDATE memories SET created_at = datetime('now', '-8 days')")
    conn.commit()
    issues = {f["issue"] for f in store.lint(conn)}
    assert "no_capture" in issues


# --- #54 stdin content (shell-quoting immunity) ---

def test_remember_content_via_stdin(tmp_path, monkeypatch):
    import io
    from ai_memory.__main__ import main as cli_main

    tricky = "it's a \"quoted\" fact - with -leading hyphen risk"
    monkeypatch.setattr(sys, "stdin", io.StringIO(tricky))
    rc = cli_main(["--db", str(tmp_path / "m.db"), "remember", "--type", "semantic"])
    assert rc == 0
    conn = db.connect(tmp_path / "m.db")
    assert conn.execute("SELECT content FROM memories").fetchone()["content"] == tricky


def test_remember_positional_still_works(tmp_path):
    from ai_memory.__main__ import main as cli_main

    rc = cli_main(["--db", str(tmp_path / "m.db"), "remember", "plain positional", "--type", "semantic"])
    assert rc == 0


def test_remember_empty_stdin_fails_loud(tmp_path, monkeypatch):
    import io
    import pytest as _pytest
    from ai_memory.__main__ import main as cli_main

    monkeypatch.setattr(sys, "stdin", io.StringIO("   "))
    with _pytest.raises(SystemExit):
        cli_main(["--db", str(tmp_path / "m.db"), "remember", "--type", "semantic"])
