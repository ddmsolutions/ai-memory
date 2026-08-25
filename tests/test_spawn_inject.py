import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

from ai_memory import db, store  # noqa: E402
import spawn_inject  # noqa: E402


def _run(monkeypatch, payload, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    rc = spawn_inject.main()
    return rc, capsys.readouterr().out


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    conn = db.connect(tmp_path / "m.db")
    store.remember(conn, "the staging deploy needs the maintenance flag", mtype="procedural", pinned=True)
    conn.close()


def test_task_prompt_gains_pack(monkeypatch, tmp_path, capsys):
    _seed(tmp_path, monkeypatch)
    rc, out = _run(monkeypatch, {
        "tool_name": "Task",
        "tool_input": {"prompt": "review the staging deploy scripts", "subagent_type": "reviewer"},
    }, capsys)
    assert rc == 0
    body = json.loads(out)["hookSpecificOutput"]
    assert body["hookEventName"] == "PreToolUse"
    updated = body["updatedInput"]
    assert updated["prompt"].startswith("review the staging deploy scripts")
    assert "maintenance flag" in updated["prompt"]
    assert updated["subagent_type"] == "reviewer"  # untouched fields survive


def test_non_task_tools_untouched(monkeypatch, tmp_path, capsys):
    _seed(tmp_path, monkeypatch)
    rc, out = _run(monkeypatch, {"tool_name": "Bash", "tool_input": {"command": "ls"}}, capsys)
    assert rc == 0 and out == ""


def test_zero_limit_disables(monkeypatch, tmp_path, capsys):
    _seed(tmp_path, monkeypatch)
    cfgfile = tmp_path / "cfg.json"
    cfgfile.write_text(json.dumps({"spawn_pack_limit": 0}), encoding="utf-8")
    monkeypatch.setenv("AI_MEMORY_CONFIG", str(cfgfile))
    rc, out = _run(monkeypatch, {
        "tool_name": "Task", "tool_input": {"prompt": "anything at all"},
    }, capsys)
    assert rc == 0 and out == ""


def test_fails_soft(monkeypatch, capsys):
    monkeypatch.setenv("AI_MEMORY_DB", "Z:/nonexistent/path/m.db")
    rc, out = _run(monkeypatch, {"tool_name": "Task", "tool_input": {"prompt": "x"}}, capsys)
    assert rc == 0 and out == ""
    monkeypatch.setattr(sys, "stdin", io.StringIO("{broken"))
    assert spawn_inject.main() == 0
