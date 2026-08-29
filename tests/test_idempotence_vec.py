"""#74: line_hash capture idempotence + optional sqlite-vec ANN index."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import db, embeddings, portability, store  # noqa: E402
from hooks import capture  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "memory.db")
    yield c
    c.close()


def _hash(session: str, memo: str) -> str:
    return hashlib.sha256(f"{session}|{memo}".encode("utf-8")).hexdigest()


def test_same_hash_returns_existing_row(conn):
    h = _hash("s1", "learned the thing")
    a = store.remember(conn, "learned the thing", origin_session="s1", line_hash=h)
    b = store.remember(conn, "learned the thing", origin_session="s1", line_hash=h)
    assert a == b
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_no_hash_still_allows_duplicates(conn):
    a = store.remember(conn, "same content twice")
    b = store.remember(conn, "same content twice")
    assert a != b


def test_same_memo_different_session_is_corroboration_not_duplicate(conn):
    a = store.remember(conn, "the deploy needs VPN", origin_session="s1",
                       line_hash=_hash("s1", "the deploy needs VPN"))
    b = store.remember(conn, "the deploy needs VPN", origin_session="s2",
                       line_hash=_hash("s2", "the deploy needs VPN"))
    assert a != b


def test_capture_hook_replay_is_noop(tmp_path, conn, monkeypatch):
    transcript = tmp_path / "t.jsonl"
    memo = "```memo\noutcome: shipped the widget\n```"
    transcript.write_text(json.dumps({
        "message": {"role": "assistant", "content": memo}
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("AI_MEMORY_DB", str(tmp_path / "memory.db"))
    payload = json.dumps({"transcript_path": str(transcript), "session_id": "sX"})
    for _ in range(2):
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
        capture.main()
    check = db.connect(tmp_path / "memory.db")
    assert check.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    assert check.execute(
        "SELECT line_hash FROM memories").fetchone()[0] is not None
    check.close()


def test_import_dedups_on_line_hash(conn, tmp_path):
    h = _hash("s1", "portable fact")
    store.remember(conn, "portable fact", mtype="semantic", origin_session="s1", line_hash=h)
    data = portability.export_store(conn)
    target = db.connect(tmp_path / "other.db")
    r1 = portability.import_store(target, data)
    r2 = portability.import_store(target, data)
    assert r1["imported"] == 1
    assert r2["imported"] == 0 and r2["deduplicated"] == 1
    assert target.execute(
        "SELECT line_hash FROM memories").fetchone()[0] == h
    target.close()


def test_semantic_candidates_falls_back_without_sqlite_vec(conn, monkeypatch):
    # Force the JSON scan path regardless of whether sqlite-vec is installed.
    monkeypatch.setattr(embeddings, "_vec_conn", lambda *a, **k: False)
    monkeypatch.setattr(embeddings, "embed_text",
                        lambda text, cfg, kind="document": [1.0, 0.0])
    mid = store.remember(conn, "vector fact", mtype="semantic")
    conn.execute(
        "INSERT INTO memory_embeddings (memory_id, model, vector) VALUES (?,?,?)",
        (mid, "m", json.dumps([1.0, 0.0])),
    )
    out = embeddings.semantic_candidates(conn, "vector fact", {"embed_model": "m"}, 5)
    assert out and out[0][0] == mid


def test_vec_index_roundtrip_when_available(conn, monkeypatch):
    pytest.importorskip("sqlite_vec")
    monkeypatch.setattr(embeddings, "embed_text",
                        lambda text, cfg, kind="document": [0.5, 0.5, 0.0])
    cfg = {"embed_model": "m", "embed_enabled": True,
           "embed_query_prefix": "", "embed_doc_prefix": ""}
    store.remember(conn, "ann indexed fact", mtype="semantic")
    done = embeddings.index_memories(conn, cfg)
    assert done == 1
    out = embeddings.semantic_candidates(conn, "ann indexed fact", cfg, 5)
    assert out
