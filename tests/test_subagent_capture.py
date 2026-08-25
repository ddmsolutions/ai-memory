import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

from ai_memory import db  # noqa: E402
import capture  # noqa: E402


def _transcript(tmp_path, name, memos):
    lines = [json.dumps({"message": {"role": "assistant", "content": [
        {"type": "text", "text": f"Done.\n```memo\n{m}\n```\n"}]}}) for m in memos]
    p = tmp_path / name
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _run(monkeypatch, tmp_path, transcript, session="sess-1"):
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": session, "transcript_path": str(transcript)})))
    return capture.main()


def _contents(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    return [r["content"] for r in conn.execute(
        "SELECT content FROM memories ORDER BY id").fetchall()]


def test_subagent_memo_captured(monkeypatch, tmp_path):
    t = _transcript(tmp_path, "sub.jsonl", ["outcome: subagent found the bug"])
    assert _run(monkeypatch, tmp_path, t) == 0
    assert _contents(tmp_path) == ["outcome: subagent found the bug"]


def test_subagent_dedup_against_parent(monkeypatch, tmp_path):
    main_t = _transcript(tmp_path, "main.jsonl", ["outcome: shared memo"])
    sub_t = _transcript(tmp_path, "sub.jsonl", ["outcome: shared memo"])
    _run(monkeypatch, tmp_path, main_t)
    _run(monkeypatch, tmp_path, sub_t)  # same session id, same content
    assert _contents(tmp_path) == ["outcome: shared memo"]


def test_subagent_multiple_memos(monkeypatch, tmp_path):
    t = _transcript(tmp_path, "sub.jsonl", ["memo one", "memo two", "memo three"])
    _run(monkeypatch, tmp_path, t)
    assert len(_contents(tmp_path)) == 3


def test_subagent_hook_fails_soft(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "m.db"))
    missing = tmp_path / "gone.jsonl"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "s", "transcript_path": str(missing)})))
    assert capture.main() == 0
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json at all", encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "s", "transcript_path": str(bad)})))
    assert capture.main() == 0


def test_no_memo_no_write(monkeypatch, tmp_path):
    lines = json.dumps({"message": {"role": "assistant", "content": [
        {"type": "text", "text": "no memo here"}]}})
    p = tmp_path / "plain.jsonl"
    p.write_text(lines, encoding="utf-8")
    _run(monkeypatch, tmp_path, p)
    assert _contents(tmp_path) == []
