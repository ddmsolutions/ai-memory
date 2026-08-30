import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import config, db, store  # noqa: E402


def test_missing_config_uses_defaults(tmp_path):
    cfg = config.load(tmp_path / "absent.json")
    assert cfg == config.DEFAULTS


def test_malformed_json_uses_defaults(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json", encoding="utf-8")
    assert config.load(p) == config.DEFAULTS


def test_partial_config_merges(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"pack_limit": 5}), encoding="utf-8")
    cfg = config.load(p)
    assert cfg["pack_limit"] == 5
    assert cfg["decay_window_days"] == config.DEFAULTS["decay_window_days"]


def test_wrong_type_falls_back_per_key(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"pack_limit": "twelve", "turn_recall_cap": 7}), encoding="utf-8")
    cfg = config.load(p)
    assert cfg["pack_limit"] == config.DEFAULTS["pack_limit"]
    assert cfg["turn_recall_cap"] == 7


def test_env_override_path(tmp_path, monkeypatch):
    p = tmp_path / "elsewhere.json"
    p.write_text(json.dumps({"pack_limit": 4}), encoding="utf-8")
    monkeypatch.setenv("AI_MEMORY_CONFIG", str(p))
    assert config.load()["pack_limit"] == 4


@pytest.mark.parametrize(
    "raw,expected",
    [
        # the shapes Ollama itself accepts
        ("127.0.0.1:11435", "http://127.0.0.1:11435"),
        ("11435", "http://127.0.0.1:11435"),                  # bare port
        ("localhost:11435", "http://localhost:11435"),
        ("http://box.lan:11435", "http://box.lan:11435"),
        ("https://box.lan:443", "https://box.lan:443"),
        ("box.lan", "http://box.lan:11434"),                  # host, default port
        ("[::1]:11435", "http://[::1]:11435"),                # IPv6 keeps brackets
        ("  127.0.0.1:11435  ", "http://127.0.0.1:11435"),    # whitespace
        ("http://127.0.0.1:11435/", "http://127.0.0.1:11435"),  # trailing slash
        # bind addresses are not dial addresses
        ("0.0.0.0:11435", "http://127.0.0.1:11435"),
        # unusable input falls back rather than raising
        ("", "http://127.0.0.1:11434"),
        ("   ", "http://127.0.0.1:11434"),
        ("127.0.0.1:notaport", "http://127.0.0.1:11434"),
        ("ftp://box.lan:11435", "http://127.0.0.1:11434"),
        ("://", "http://127.0.0.1:11434"),
    ],
)
def test_default_embed_url_parses_ollama_host(raw, expected):
    assert config._default_embed_url(raw) == expected


def test_default_embed_url_reads_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11435")
    assert config._default_embed_url() == "http://127.0.0.1:11435"
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert config._default_embed_url() == "http://127.0.0.1:11434"


def test_defaults_pick_up_ollama_host_at_import(monkeypatch):
    """DEFAULTS is built at import, so the wiring needs a reload to observe."""
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11435")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.DEFAULTS["embed_url"] == "http://127.0.0.1:11435"
    finally:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        importlib.reload(config)  # leave the module as the rest of the suite expects


def test_explicit_embed_url_still_wins_over_env(tmp_path, monkeypatch):
    """Precedence: config.json > OLLAMA_HOST > hardcoded fallback."""
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11435")
    importlib.reload(config)
    try:
        p = tmp_path / "c.json"
        p.write_text(json.dumps({"embed_url": "http://elsewhere:9999"}), encoding="utf-8")
        assert config.load(p)["embed_url"] == "http://elsewhere:9999"
    finally:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        importlib.reload(config)


def test_recall_pack_respects_configured_limit(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    for i in range(6):
        store.remember(conn, f"pinned rule number {i}", mtype="episodic", pinned=True)
    cfg = dict(config.DEFAULTS)
    cfg["pack_limit"] = 3
    pack = store.recall_pack(conn, cfg=cfg)
    assert pack.count("pinned rule number") == 3
