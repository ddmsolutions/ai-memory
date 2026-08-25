"""Regression tests for the v0.2 cold-review findings."""
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

from ai_memory import config, db, redact, store  # noqa: E402
import capture  # noqa: E402

CFG = dict(config.DEFAULTS)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    yield c
    c.close()


def test_decay_never_resurrects_corrected_facts(conn):
    old = store.remember(conn, "budget is 50k", mtype="episodic")
    conn.execute("UPDATE memories SET recall_count = 1 WHERE id=?", (old,))
    fix = store.remember(conn, "correction: budget is 30k", mtype="episodic", supersedes=old)
    conn.execute("UPDATE memories SET created_at = datetime('now','-90 days')")
    conn.commit()
    store.decay(conn, CFG)
    row = conn.execute("SELECT superseded_by FROM memories WHERE id=?", (old,)).fetchone()
    assert row is not None and row["superseded_by"] == fix
    assert "50k" not in " ".join(
        r["content"] for r in conn.execute("SELECT content FROM v_active_memories"))


def test_cli_remember_path_redacts(conn):
    store.remember(conn, "the key is sk-live_Abc123Def456Ghi789Jkl", mtype="semantic")
    stored = conn.execute("SELECT content FROM memories").fetchone()["content"]
    assert "sk-live_Abc123Def456Ghi789Jkl" not in stored
    assert "[REDACTED:api-key]" in stored


def test_capture_fails_soft_on_unreadable_transcript(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    directory = tmp_path / "iamadir"
    directory.mkdir()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "s", "transcript_path": str(directory)})))
    assert capture.main() == 0


def test_capture_dedups_within_one_transcript(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    line = json.dumps({"message": {"role": "assistant", "content": [
        {"type": "text", "text": "```memo\nsame memo\n```"}]}})
    t = tmp_path / "t.jsonl"
    t.write_text(line + "\n" + line, encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "s", "transcript_path": str(t)})))
    assert capture.main() == 0
    conn = db.connect(tmp_path / "m.db")
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_capture_resolves_scope_from_cwd(monkeypatch, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cfgfile = tmp_path / "cfg.json"
    cfgfile.write_text(json.dumps({"scope_map": {str(proj): "proj"}}), encoding="utf-8")
    monkeypatch.setenv("AI_MEMORY_CONFIG", str(cfgfile))
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"message": {"role": "assistant", "content": [
        {"type": "text", "text": "```memo\nscoped memo\n```"}]}}), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "s", "transcript_path": str(t), "cwd": str(proj)})))
    assert capture.main() == 0
    conn = db.connect(tmp_path / "m.db")
    assert conn.execute("SELECT scope FROM memories").fetchone()["scope"] == "proj"


def test_stopword_prompt_injects_nothing(conn):
    store.remember(conn, "the client meeting moved to Tuesday", mtype="semantic")
    assert store.turn_recall(conn, "what is the best way to do this", session_id="s", cfg=CFG) == ""


def test_min_score_threshold_filters(conn):
    store.remember(conn, "postgres migration checklist for staging", mtype="semantic")
    strict = dict(CFG)
    strict["turn_recall_min_score"] = 99.0
    assert store.turn_recall(conn, "postgres staging checklist", session_id="s", cfg=strict) == ""
    assert "postgres" in store.turn_recall(conn, "postgres staging checklist", session_id="s2", cfg=CFG)


def test_quote_in_query_does_not_crash(conn):
    store.remember(conn, "quoting works fine", mtype="semantic")
    assert store.search(conn, 'don"t "quoting"') is not None


def test_pack_limit_is_total_budget(conn):
    for i in range(10):
        store.remember(conn, f"pinned episode {i}", mtype="episodic", pinned=True)
    for i in range(10):
        store.remember(conn, f"procedural rule {i}", mtype="procedural")
    cfg = dict(CFG)
    cfg["pack_limit"] = 6
    pack = store.recall_pack(conn, cfg=cfg)
    assert sum(1 for line in pack.splitlines() if line.startswith("- [")) == 6


def test_pinned_survive_session_resume(conn):
    store.remember(conn, "pinned rule stays", mtype="procedural", pinned=True)
    first = store.recall_pack(conn, cfg=CFG, session_id="s1")
    resumed = store.recall_pack(conn, cfg=CFG, session_id="s1")
    assert "pinned rule stays" in first and "pinned rule stays" in resumed
    assert conn.execute("SELECT recall_count FROM memories").fetchone()[0] == 1


def test_version_stripped_store_recovers(tmp_path):
    path = tmp_path / "m.db"
    db.connect(path).close()
    conn = db.connect(path)
    conn.execute("PRAGMA user_version = 0")  # tables (incl. injection_log) survive
    conn.close()
    conn = db.connect(path)  # must not raise: migration 2 is idempotent
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_hex_secret_redacted_git_sha_survives():
    hex64 = "a3f8c2d94e7b165098fedc4ba21375e6d09c8b4a7f2e63d15c88a90bf4e721dc"
    clean, n = redact.redact(f"signing secret {hex64}")
    assert hex64 not in clean and n == 1
    sha = "a" * 20 + "1b2c3d4e5f60718293a4"  # 40-char hex, git SHA shape
    clean2, _ = redact.redact(f"see commit {sha}")
    assert sha in clean2


def test_nonfinite_and_negative_config_rejected(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"reinforce_step": -0.5, "recency_half_life_days": Infinity}', encoding="utf-8")
    cfg = config.load(p)
    assert cfg["reinforce_step"] == config.DEFAULTS["reinforce_step"]
    assert cfg["recency_half_life_days"] == config.DEFAULTS["recency_half_life_days"]
