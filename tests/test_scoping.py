import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import config, db, store  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    yield c
    c.close()


def _cfg(tmp_path):
    cfg = dict(config.DEFAULTS)
    cfg["scope_map"] = {str(tmp_path / "proja"): "proja"}
    return cfg


def test_mapped_cwd_scopes_pack(conn, tmp_path):
    (tmp_path / "proja").mkdir()
    cfg = _cfg(tmp_path)
    store.remember(conn, "projA deploy needs the flag", mtype="semantic", scope="proja")
    store.remember(conn, "projB uses a different stack", mtype="semantic", scope="projb")
    scope = config.resolve_scope(str(tmp_path / "proja"), cfg)
    assert scope == "proja"
    pack = store.recall_pack(conn, scope=scope, cfg=cfg)
    assert "projA deploy" in pack and "projB uses" not in pack


def test_unmapped_cwd_is_global_regression(conn, tmp_path):
    cfg = _cfg(tmp_path)
    assert config.resolve_scope(str(tmp_path / "elsewhere"), cfg) == "global"
    assert config.resolve_scope(None, cfg) == "global"


def test_nested_dir_resolves_to_ancestor(tmp_path):
    (tmp_path / "proja" / "src" / "deep").mkdir(parents=True)
    cfg = _cfg(tmp_path)
    assert config.resolve_scope(str(tmp_path / "proja" / "src" / "deep"), cfg) == "proja"


def test_longest_prefix_wins(tmp_path):
    (tmp_path / "proja" / "sub").mkdir(parents=True)
    cfg = dict(config.DEFAULTS)
    cfg["scope_map"] = {
        str(tmp_path / "proja"): "proja",
        str(tmp_path / "proja" / "sub"): "subproj",
    }
    assert config.resolve_scope(str(tmp_path / "proja" / "sub"), cfg) == "subproj"


def test_union_includes_global(conn, tmp_path):
    cfg = _cfg(tmp_path)
    store.remember(conn, "global rule applies everywhere", mtype="procedural", pinned=True)
    store.remember(conn, "projA-only fact", mtype="semantic", scope="proja")
    pack = store.recall_pack(conn, scope="proja", cfg=cfg)
    assert "global rule applies everywhere" in pack and "projA-only fact" in pack


def test_explicit_scope_beats_mapping(conn, tmp_path):
    cfg = _cfg(tmp_path)
    store.remember(conn, "projB-only fact", mtype="semantic", scope="projb")
    pack = store.recall_pack(conn, scope="projb", cfg=cfg)
    assert "projB-only fact" in pack
