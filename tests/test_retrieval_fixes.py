"""Regression tests for #59 (hybrid rank) and #60 (scope relevance)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import config, db, embeddings, store, tuning  # noqa: E402

CFG = dict(config.DEFAULTS)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    yield c
    c.close()


def _hybrid_cfg():
    cfg = dict(CFG)
    cfg["embed_enabled"] = True
    cfg["hybrid_semantic_weight"] = 0.5
    return cfg


def _fake(monkeypatch, vectors):
    monkeypatch.setattr(embeddings, "embed_text",
                        lambda text, cfg, kind="document": vectors.get(text))


def test_hybrid_rank_word_form_gap(conn, monkeypatch):
    # "live" vs "lives": FTS finds nothing, the vectors bridge it (#59 evidence 2)
    doc = "Donna lives at 451 Coventry Road"
    _fake(monkeypatch, {doc: [1.0, 0.1], "where does Donna live": [0.98, 0.15],
                        "unrelated gardening note": [0.0, 1.0]})
    store.remember(conn, doc, mtype="semantic")
    store.remember(conn, "unrelated gardening note", mtype="episodic")
    cfg = _hybrid_cfg()
    embeddings.index_memories(conn, cfg)
    rows = store.search(conn, "where does Donna live", cfg=cfg)
    assert rows and rows[0]["content"] == doc


def test_hybrid_rank_semantic_gap(conn, monkeypatch):
    # "birthday" vs "born": zero lexical overlap (#59 evidence 1)
    doc = "Harley was born 1 September 2019"
    _fake(monkeypatch, {doc: [0.9, 0.2], "when is the birthday": [0.88, 0.25],
                        "meeting notes about budgets": [0.0, 1.0]})
    store.remember(conn, doc, mtype="semantic")
    store.remember(conn, "meeting notes about budgets", mtype="episodic")
    cfg = _hybrid_cfg()
    embeddings.index_memories(conn, cfg)
    rows = store.search(conn, "when is the birthday", cfg=cfg)
    assert rows and rows[0]["content"] == doc


def test_hybrid_failsoft_backend_down(conn, monkeypatch):
    store.remember(conn, "keyword findable fact", mtype="semantic")
    _fake(monkeypatch, {})  # embedder returns None for everything
    rows = store.search(conn, "keyword findable", cfg=_hybrid_cfg())
    assert rows and rows[0]["content"] == "keyword findable fact"  # pure bm25, no error


def test_hybrid_weight_zero_is_pure_bm25(conn, monkeypatch):
    doc = "Donna lives at 451 Coventry Road"
    query = "current home address"  # zero lexical overlap with the doc
    _fake(monkeypatch, {doc: [1.0, 0.0], query: [1.0, 0.0]})
    store.remember(conn, doc, mtype="semantic")
    cfg = _hybrid_cfg()
    embeddings.index_memories(conn, cfg)
    assert store.search(conn, query, cfg=cfg)  # blend finds it
    cfg["hybrid_semantic_weight"] = 0.0
    assert store.search(conn, query, cfg=cfg) == []  # weight 0: pure bm25, gap returns


def test_tune_can_adjust_blend_weight():
    assert "hybrid_semantic_weight" in tuning.DEFAULT_GRID
    assert 0.0 in tuning.DEFAULT_GRID["hybrid_semantic_weight"]


def test_query_and_doc_prefixes_sent(monkeypatch):
    sent = []

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"embedding": [0.1, 0.2]}).encode()

    def fake_urlopen(req, timeout=10):
        sent.append(json.loads(req.data)["prompt"])
        return FakeResp()

    monkeypatch.setattr(embeddings.urllib.request, "urlopen", fake_urlopen)
    cfg = dict(CFG)
    embeddings.embed_text("hello", cfg, kind="query")
    embeddings.embed_text("hello", cfg, kind="document")
    assert sent[0] == "search_query: hello"
    assert sent[1] == "search_document: hello"


def test_force_reindex_drops_and_rebuilds(conn, monkeypatch):
    _fake(monkeypatch, {"a fact about sprockets": [1.0]})
    store.remember(conn, "a fact about sprockets", mtype="semantic")
    cfg = _hybrid_cfg()
    assert embeddings.index_memories(conn, cfg) == 1
    assert embeddings.index_memories(conn, cfg) == 0          # incremental: nothing new
    assert embeddings.index_memories(conn, cfg, force=True) == 1  # force re-embeds


def test_search_downweights_foreign_scope(conn):
    store.remember(conn, "Donna address detail here", mtype="semantic", scope="personal")
    store.remember(conn, "Donna address detail here noted again", mtype="episodic", scope="moltbook")
    cfg = dict(CFG)
    cfg["foreign_scope_penalty"] = 0.3
    rows = store.search(conn, "Donna address detail", cfg=cfg, preferred_scope="personal")
    assert rows[0]["scope"] == "personal"
    scopes = [r["scope"] for r in rows]
    assert "moltbook" in scopes  # down-weighted, not filtered


def test_search_explicit_scope_unchanged(conn):
    store.remember(conn, "scoped widget fact", mtype="semantic", scope="proja")
    store.remember(conn, "another widget fact", mtype="semantic", scope="projb")
    rows = store.search(conn, "widget fact", scope="proja", cfg=dict(CFG),
                        preferred_scope="projb")
    assert [r["scope"] for r in rows] == ["proja"]  # hard filter wins; preferred ignored


def test_global_rows_never_penalised(conn):
    store.remember(conn, "global widget rule", mtype="procedural")
    store.remember(conn, "foreign widget note", mtype="episodic", scope="moltbook")
    cfg = dict(CFG)
    cfg["foreign_scope_penalty"] = 0.1
    rows = store.search(conn, "widget", cfg=cfg, preferred_scope="personal")
    assert rows[0]["scope"] == "global"
