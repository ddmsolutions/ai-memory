import json
import sys
from pathlib import Path

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


def test_recall_pack_respects_configured_limit(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    for i in range(6):
        store.remember(conn, f"pinned rule number {i}", mtype="episodic", pinned=True)
    cfg = dict(config.DEFAULTS)
    cfg["pack_limit"] = 3
    pack = store.recall_pack(conn, cfg=cfg)
    assert pack.count("pinned rule number") == 3
