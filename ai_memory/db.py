"""SQLite schema and connection handling.

One local database file holds all four stores:
  memories  - episodic / semantic / procedural rows (FTS5 indexed)
  entities  - typed nodes of the knowledge graph
  edges     - typed, weighted relationships between entities

The schema is third normal form with one documented exception:
recall_count / last_recalled_at are derived counters kept on the row
(a recall_events table was rejected: one insert per memory per session
start for no current query need). Provenance is atomic: origin_session
holds the capturing session id, promoted_from is a real self-referencing
foreign key recording consolidation lineage.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id               INTEGER PRIMARY KEY,
  type             TEXT NOT NULL CHECK (type IN ('episodic','semantic','procedural')),
  scope            TEXT NOT NULL DEFAULT 'global',
  content          TEXT NOT NULL,
  origin_session   TEXT,
  promoted_from    INTEGER REFERENCES memories(id) ON DELETE SET NULL,
  confidence       REAL NOT NULL DEFAULT 0.7,
  pinned           INTEGER NOT NULL DEFAULT 0,
  consolidated     INTEGER NOT NULL DEFAULT 0,
  superseded_by    INTEGER REFERENCES memories(id) ON DELETE SET NULL,
  recall_count     INTEGER NOT NULL DEFAULT 0,
  last_recalled_at TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  content, content='memories', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF content ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
  INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS entities (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  etype      TEXT NOT NULL DEFAULT 'thing',
  summary    TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (name, etype)
);

CREATE TABLE IF NOT EXISTS edges (
  id         INTEGER PRIMARY KEY,
  src        INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  dst        INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  rel        TEXT NOT NULL,
  weight     REAL NOT NULL DEFAULT 1.0,
  memory_id  INTEGER REFERENCES memories(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (src, dst, rel)
);

CREATE INDEX IF NOT EXISTS idx_memories_type   ON memories(type, consolidated);
CREATE INDEX IF NOT EXISTS idx_edges_src       ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst       ON edges(dst);
"""


def default_db_path() -> Path:
    env = os.environ.get("AI_MEMORY_DB")
    if env:
        return Path(env)
    return Path.home() / ".ai-memory" / "memory.db"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
