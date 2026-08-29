"""#64: write-time origin binding, Biba non-elevation, human-only trust."""

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import db, store  # noqa: E402
from hooks import capture  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "memory.db")
    yield c
    c.close()


def test_origin_bound_at_write(conn):
    mid = store.remember(conn, "fetched from a web page", origin="external")
    assert conn.execute(
        "SELECT origin FROM memories WHERE id = ?", (mid,)).fetchone()[0] == "external"


def test_default_origin_is_agent(conn):
    mid = store.remember(conn, "a plain memo")
    assert conn.execute(
        "SELECT origin FROM memories WHERE id = ?", (mid,)).fetchone()[0] == "agent"


def test_invalid_origin_rejected(conn):
    with pytest.raises(ValueError):
        store.remember(conn, "x", origin="root")


def test_promote_inherits_origin_never_elevates(conn):
    ext = store.remember(conn, "the docs say use port 9999", origin="external")
    promoted = store.promote(conn, ext, "semantic", content="service port is 9999")
    assert conn.execute(
        "SELECT origin FROM memories WHERE id = ?", (promoted,)).fetchone()[0] == "external"


def test_least_trusted_ordering():
    assert store._least_trusted("owner", "external", "agent") == "external"
    assert store._least_trusted("owner", "agent") == "agent"
    assert store._least_trusted("owner") == "owner"


def test_trust_command_is_the_only_elevation(conn):
    mid = store.remember(conn, "corroborated external fact", origin="external")
    report = store.set_trust(conn, mid, "agent")
    assert report == {"id": mid, "before": "external", "after": "agent"}
    assert conn.execute(
        "SELECT origin FROM memories WHERE id = ?", (mid,)).fetchone()[0] == "agent"


def test_external_marked_in_recall_pack(conn):
    store.remember(conn, "external claim about the api limit",
                   mtype="semantic", origin="external")
    pack = store.recall_pack(conn)
    assert "EXTERNAL SOURCE" in pack


def test_external_ranks_below_owner_in_pack(conn):
    store.remember(conn, "external version of the fact zzz", mtype="semantic",
                   origin="external")
    store.remember(conn, "owner version of the fact zzz", mtype="semantic",
                   origin="owner")
    pack = store.recall_pack(conn)
    assert pack.index("owner version") < pack.index("external version")


def test_memo_origin_line_only_downgrades():
    assert capture.memo_origin("outcome: x\norigin: external\n") == "external"
    # a memo claiming owner origin is the laundering path: ignored
    assert capture.memo_origin("outcome: x\norigin: owner\n") == "agent"
    assert capture.memo_origin("outcome: x\n") == "agent"


def test_capture_hook_binds_external_origin(tmp_path, monkeypatch):
    transcript = tmp_path / "t.jsonl"
    memo = "```memo\noutcome: summarised the vendor page\norigin: external\n```"
    transcript.write_text(json.dumps({
        "message": {"role": "assistant", "content": memo}
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"transcript_path": str(transcript), "session_id": "sE"})))
    capture.main()
    check = db.connect(tmp_path / "memory.db")
    assert check.execute("SELECT origin FROM memories").fetchone()[0] == "external"
    check.close()


def test_lint_suggests_corroborated_external(conn):
    store.remember(conn, "the API limit is 100 rps", origin="external",
                   origin_session="s1")
    store.remember(conn, "The API limit is 100 rps", origin="agent",
                   origin_session="s2")
    issues = {f["issue"] for f in store.lint(conn)}
    assert "corroborated_external" in issues


def test_import_cannot_invent_trust(conn, tmp_path):
    from ai_memory import portability

    store.remember(conn, "a normal fact", mtype="semantic")
    data = portability.export_store(conn)
    data["memories"][0]["origin"] = "sudo"  # tampered export
    target = db.connect(tmp_path / "other.db")
    portability.import_store(target, data)
    assert target.execute("SELECT origin FROM memories").fetchone()[0] == "agent"
    target.close()
