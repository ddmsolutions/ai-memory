import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import config, db, embeddings, store  # noqa: E402

# Deterministic fake embedder: fixed vectors express that "postgres" and the
# database row are semantically close while the recipe row is not.
VECTORS = {
    "postgres": [1.0, 0.0, 0.0],
    "the relational engine stores rows in pages": [0.95, 0.1, 0.0],
    "grandma's shortcrust pastry recipe": [0.0, 0.0, 1.0],
}


@pytest.fixture
def fake_embed(monkeypatch):
    monkeypatch.setattr(embeddings, "embed_text", lambda text, cfg: VECTORS.get(text))


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    store.remember(c, "the relational engine stores rows in pages", mtype="semantic")
    store.remember(c, "grandma's shortcrust pastry recipe", mtype="episodic")
    yield c
    c.close()


def _cfg(enabled=True):
    cfg = dict(config.DEFAULTS)
    cfg["embed_enabled"] = enabled
    return cfg


def test_index_and_semantic_search(conn, fake_embed):
    assert embeddings.index_memories(conn, _cfg()) == 2
    rows = store.search(conn, "postgres", cfg=_cfg())
    contents = [r["content"] for r in rows]
    assert "the relational engine stores rows in pages" in contents  # no keyword overlap: semantic hit
    # ranked by cosine, the recipe should not lead
    assert contents[0] != "grandma's shortcrust pastry recipe"


def test_disabled_changes_nothing(conn, fake_embed):
    embeddings.index_memories(conn, _cfg())
    assert store.search(conn, "postgres", cfg=_cfg(enabled=False)) == []
    assert store.search(conn, "postgres") == []  # no cfg at all: pure FTS


def test_server_down_fails_soft(conn, monkeypatch):
    monkeypatch.setattr(embeddings, "embed_text", lambda text, cfg: None)
    assert embeddings.index_memories(conn, _cfg()) == 0
    assert store.search(conn, "postgres", cfg=_cfg()) == []


def test_fts_results_lead_hybrid(conn, fake_embed):
    embeddings.index_memories(conn, _cfg())
    rows = store.search(conn, "pastry recipe", cfg=_cfg())
    assert rows[0]["content"] == "grandma's shortcrust pastry recipe"  # FTS hit first


def test_quarantine_never_embedded(conn, fake_embed, tmp_path):
    store.remember(conn, "poisoned content", mtype="episodic", scope="quarantine")
    embeddings.index_memories(conn, _cfg())
    n = conn.execute(
        "SELECT COUNT(*) FROM memory_embeddings me JOIN memories m ON m.id = me.memory_id"
        " WHERE m.scope = 'quarantine'").fetchone()[0]
    assert n == 0


def test_cosine_edges():
    assert embeddings._cosine([1, 0], [0, 1]) == 0.0
    assert embeddings._cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert embeddings._cosine([0, 0], [1, 0]) == 0.0
    assert embeddings._cosine([1, 0], [1, 0, 0]) == 0.0
