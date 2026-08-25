import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

from ai_memory import db, redact, store  # noqa: E402
import capture  # noqa: E402


def _captured_bytes(monkeypatch, tmp_path, memo):
    """Run the real capture path with a memo, checkpoint WAL, return raw file bytes."""
    dbfile = tmp_path / "m.db"
    monkeypatch.setenv("AI_MEMORY_DB", str(dbfile))
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"message": {"role": "assistant", "content": [
        {"type": "text", "text": f"```memo\n{memo}\n```"}]}}), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "s", "transcript_path": str(t)})))
    assert capture.main() == 0
    conn = db.connect(dbfile)
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    conn.close()
    return dbfile.read_bytes()


SECRETS = {
    "api-key": "sk-live_Abc123Def456Ghi789Jkl",
    "aws-key": "AKIAIOSFODNN7EXAMPLE",
    "github-token": "ghp_" + "A1b2C3d4E5f6G7h8I9j0" * 2,
    "bearer-token": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abc",
}


def test_api_key_prefix_redacted(monkeypatch, tmp_path):
    raw = _captured_bytes(monkeypatch, tmp_path, f"key was {SECRETS['api-key']} oops")
    assert SECRETS["api-key"].encode() not in raw
    assert b"[REDACTED:api-key]" in raw


def test_aws_key_redacted(monkeypatch, tmp_path):
    raw = _captured_bytes(monkeypatch, tmp_path, f"creds {SECRETS['aws-key']}")
    assert SECRETS["aws-key"].encode() not in raw


def test_github_token_redacted(monkeypatch, tmp_path):
    raw = _captured_bytes(monkeypatch, tmp_path, SECRETS["github-token"])
    assert SECRETS["github-token"].encode() not in raw


def test_bearer_token_redacted(monkeypatch, tmp_path):
    raw = _captured_bytes(monkeypatch, tmp_path, f"header: {SECRETS['bearer-token']}")
    assert b"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abc" not in raw


def test_pem_block_redacted():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nlines\n-----END RSA PRIVATE KEY-----"
    clean, n = redact.redact(f"cert dump: {pem}")
    assert "MIIEow" not in clean and n == 1


def test_high_entropy_redacted_plain_text_untouched():
    token = "9fK2mQ7xVb4Rt8Lw3Zp6Ny1Jd5Hg0Sc9aE2uI7o"
    clean, n = redact.redact(f"token {token} found")
    assert token not in clean and n == 1
    sentence = "this is a perfectly normal sentence about memory stores"
    clean2, n2 = redact.redact(sentence)
    assert clean2 == sentence and n2 == 0


def test_fts_never_indexes_secret(monkeypatch, tmp_path):
    _captured_bytes(monkeypatch, tmp_path, f"key {SECRETS['api-key']}")
    conn = db.connect(tmp_path / "m.db")
    assert store.search(conn, "sk-live_Abc123Def456Ghi789Jkl") == []


def test_custom_pattern_from_config():
    clean, n = redact.redact(
        "internal id ZZ-9999-SECRET here",
        extra_patterns=[{"label": "internal-id", "regex": r"ZZ-\d{4}-SECRET"}])
    assert "ZZ-9999-SECRET" not in clean and "[REDACTED:internal-id]" in clean


def test_invalid_custom_pattern_skipped():
    clean, n = redact.redact("plain text", extra_patterns=[{"label": "bad", "regex": "("}])
    assert clean == "plain text" and n == 0


def test_clean_memo_unchanged(monkeypatch, tmp_path):
    raw = _captured_bytes(monkeypatch, tmp_path, "outcome: fixed the flaky test")
    assert b"outcome: fixed the flaky test" in raw
