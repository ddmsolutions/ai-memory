import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import db, portability  # noqa: E402

SAMPLE = """# My CLAUDE.md

Some prose that should be ignored entirely.

- Always run the schema linter before committing migrations
- The staging database is Postgres 16 on port 5433
- Never push directly to the main branch of client repos
- short
* The team stand-up happens at 09:15 UK time
"""


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    yield c
    c.close()


def test_seed_types_and_counts(conn, tmp_path):
    f = tmp_path / "CLAUDE.md"
    f.write_text(SAMPLE, encoding="utf-8")
    report = portability.seed_from_markdown(conn, f)
    assert report == {"imported": 4, "skipped": 0}
    types = dict(conn.execute("SELECT content, type FROM memories").fetchall())
    assert types["Always run the schema linter before committing migrations"] == "procedural"
    assert types["Never push directly to the main branch of client repos"] == "procedural"
    assert types["The staging database is Postgres 16 on port 5433"] == "semantic"
    assert types["The team stand-up happens at 09:15 UK time"] == "semantic"


def test_seed_reruns_dedup(conn, tmp_path):
    f = tmp_path / "CLAUDE.md"
    f.write_text(SAMPLE, encoding="utf-8")
    portability.seed_from_markdown(conn, f)
    report = portability.seed_from_markdown(conn, f)
    assert report == {"imported": 0, "skipped": 4}


def test_seed_respects_scope(conn, tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("- The Hue MVP deadline moved to October", encoding="utf-8")
    portability.seed_from_markdown(conn, f, scope="hue")
    assert conn.execute("SELECT scope FROM memories").fetchone()["scope"] == "hue"


def test_seed_redacts_via_remember(conn, tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("- The deploy key is sk-live_Abc123Def456Ghi789Jkl apparently", encoding="utf-8")
    portability.seed_from_markdown(conn, f)
    stored = conn.execute("SELECT content FROM memories").fetchone()["content"]
    assert "sk-live_Abc123Def456Ghi789Jkl" not in stored
