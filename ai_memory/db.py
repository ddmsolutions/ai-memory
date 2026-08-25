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
import re
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
CREATE INDEX IF NOT EXISTS idx_memories_origin_session ON memories(origin_session);
CREATE INDEX IF NOT EXISTS idx_entities_name_nocase    ON entities(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_edges_src       ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst       ON edges(dst);

CREATE VIEW IF NOT EXISTS v_active_memories AS
  SELECT * FROM memories WHERE superseded_by IS NULL;

CREATE VIEW IF NOT EXISTS v_consolidation_backlog AS
  SELECT * FROM v_active_memories WHERE type = 'episodic' AND consolidated = 0;

CREATE VIEW IF NOT EXISTS v_edges_named AS
  SELECT e.id, s.name AS src_name, s.etype AS src_etype, e.rel,
         d.name AS dst_name, d.etype AS dst_etype, e.weight, e.memory_id, e.created_at
    FROM edges e
    JOIN entities s ON s.id = e.src
    JOIN entities d ON d.id = e.dst;
"""


SCHEMA_VERSION = 6

# Ordered migrations: {target_version: [sql, ...]}. The baseline schema is
# version 1; every DDL change from here ships as an entry here, never as an
# edit that only fresh stores receive.
MIGRATIONS: dict[int, list[str]] = {
    2: [
        # Session-level injection dedup: a row injected once (pack or turn)
        # is not injected again that session (FR-R5).
        """CREATE TABLE IF NOT EXISTS injection_log (
             session_id  TEXT NOT NULL,
             memory_id   INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
             injected_at TEXT NOT NULL DEFAULT (datetime('now')),
             PRIMARY KEY (session_id, memory_id)
           )""",
        "CREATE INDEX IF NOT EXISTS ix_injection_session ON injection_log(session_id)",
    ],
    3: [
        # FR-A1/A2: outcome valence on episodes, verify-by staleness on facts.
        # Views are recreated because SQLite expands SELECT * at view-creation
        # time; without this the new columns would be invisible to readers.
        "ALTER TABLE memories ADD COLUMN valence TEXT"
        " CHECK (valence IN ('success','failure','neutral'))",
        "ALTER TABLE memories ADD COLUMN verify_by TEXT",
        "DROP VIEW IF EXISTS v_consolidation_backlog",
        "DROP VIEW IF EXISTS v_active_memories",
        "CREATE VIEW v_active_memories AS"
        "  SELECT * FROM memories WHERE superseded_by IS NULL",
        "CREATE VIEW v_consolidation_backlog AS"
        "  SELECT * FROM v_active_memories WHERE type = 'episodic' AND consolidated = 0",
    ],
    4: [
        # FR-N1: mentions bridge between memories and the entity graph.
        """CREATE TABLE IF NOT EXISTS memory_entities (
             memory_id  INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
             entity_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
             created_at TEXT NOT NULL DEFAULT (datetime('now')),
             PRIMARY KEY (memory_id, entity_id)
           )""",
        "CREATE INDEX IF NOT EXISTS ix_mention_entity ON memory_entities(entity_id)",
        "CREATE VIEW IF NOT EXISTS v_entity_memories AS"
        "  SELECT e.name AS entity_name, e.etype, m.*"
        "  FROM memory_entities me"
        "  JOIN entities e ON e.id = me.entity_id"
        "  JOIN memories m ON m.id = me.memory_id",
    ],
    5: [
        # FR-P1: prospective memory. Its own table: intentions have a trigger
        # and a terminal lifecycle, which the memories type CHECK cannot hold.
        """CREATE TABLE IF NOT EXISTS intentions (
             id            INTEGER PRIMARY KEY,
             content       TEXT NOT NULL,
             trigger_kind  TEXT NOT NULL CHECK (trigger_kind IN ('time','context')),
             trigger_value TEXT NOT NULL,
             scope         TEXT NOT NULL DEFAULT 'global',
             status        TEXT NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending','fired','done','expired')),
             origin_session TEXT,
             created_at    TEXT NOT NULL DEFAULT (datetime('now')),
             resolved_at   TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS ix_intentions_pending ON intentions(status, trigger_kind)",
    ],
    6: [
        # FR-L1..L3: associative links between memories, curated + auto co_session,
        # Hebbian weights that reinforce on co-retrieval and decay unreinforced.
        """CREATE TABLE IF NOT EXISTS memory_links (
             src_memory      INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
             dst_memory      INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
             rel             TEXT NOT NULL CHECK (rel IN
                             ('derives_from','supports','contradicts','follows','co_session')),
             weight          REAL NOT NULL DEFAULT 0.3 CHECK (weight > 0 AND weight <= 1),
             reinforce_count INTEGER NOT NULL DEFAULT 1,
             last_reinforced TEXT NOT NULL DEFAULT (datetime('now')),
             created_at      TEXT NOT NULL DEFAULT (datetime('now')),
             PRIMARY KEY (src_memory, dst_memory, rel)
           )""",
        "CREATE INDEX IF NOT EXISTS ix_links_dst ON memory_links(dst_memory)",
    ],
}


class MigrationError(RuntimeError):
    """A migration failed; the store was left untouched at its old version."""


_ALTER_ADD_RE = re.compile(r"ALTER TABLE\s+(\w+)\s+ADD COLUMN\s+(\w+)", re.I)


def _already_applied(conn: sqlite3.Connection, statement: str) -> bool:
    """Idempotency convention: DDL uses IF NOT EXISTS, but ALTER ADD COLUMN has
    no such clause, so skip it when the column exists (version-stripped or
    concurrently migrated stores re-run migrations by design)."""
    m = _ALTER_ADD_RE.match(statement.strip())
    if not m:
        return False
    table, column = m.groups()
    return column in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        # Fresh store, or a pre-versioning v0.1 store: the baseline DDL is
        # idempotent either way.
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA user_version = 1")
        version = 1
    for target in sorted(MIGRATIONS):
        if target <= version:
            continue
        try:
            # Explicit BEGIN: python sqlite3's implicit transaction excludes
            # DDL, which would half-apply a failed migration.
            conn.execute("BEGIN IMMEDIATE")
            # Re-read under the write lock: a concurrent connection (Stop and
            # SubagentStop firing together) may have already migrated.
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            if current >= target:
                conn.rollback()
                version = current
                continue
            for statement in MIGRATIONS[target]:
                if not _already_applied(conn, statement):
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {int(target)}")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise MigrationError(
                f"migration to schema version {target} failed, store left at {version}: {exc}"
            ) from exc
        version = target


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
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    _migrate(conn)
    return conn
