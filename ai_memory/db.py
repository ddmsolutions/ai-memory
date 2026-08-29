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


SCHEMA_VERSION = 19

# Ordered migrations: {target_version: [sql, ...]}. The baseline schema is
# version 1; every DDL change from here ships as an entry here, never as an
# edit that only fresh stores receive.
# Entries are SQL strings, or callables taking the connection for changes
# needing their own idempotence guard (table rebuilds).
MIGRATIONS: dict[int, list] = {
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
    7: [
        # FR-V1: optional embedding layer. Vectors stored as JSON text: readable,
        # stdlib-only, adequate at local-store scale.
        """CREATE TABLE IF NOT EXISTS memory_embeddings (
             memory_id  INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
             model      TEXT NOT NULL,
             vector     TEXT NOT NULL,
             created_at TEXT NOT NULL DEFAULT (datetime('now'))
           )""",
    ],
    8: [
        # Review fix: quarantine is enforced at the READ layer. One predicate,
        # one place; every recall surface (search, pack, related, graph lines)
        # reads through these views. Review paths (lint, raw table) still see
        # quarantined rows deliberately.
        "DROP VIEW IF EXISTS v_consolidation_backlog",
        "DROP VIEW IF EXISTS v_entity_memories",
        "DROP VIEW IF EXISTS v_active_memories",
        "CREATE VIEW v_active_memories AS"
        "  SELECT * FROM memories WHERE superseded_by IS NULL AND scope <> 'quarantine'",
        "CREATE VIEW v_consolidation_backlog AS"
        "  SELECT * FROM v_active_memories WHERE type = 'episodic' AND consolidated = 0",
        "CREATE VIEW v_entity_memories AS"
        "  SELECT e.name AS entity_name, e.etype, m.*"
        "  FROM memory_entities me"
        "  JOIN entities e ON e.id = me.entity_id"
        "  JOIN v_active_memories m ON m.id = me.memory_id",
    ],
    9: [
        # FR-M4: recall utility feedback. Traces hold ids and scores only,
        # never content: no duplicate leak surface.
        """CREATE TABLE IF NOT EXISTS recall_trace (
             id          INTEGER PRIMARY KEY,
             session_id  TEXT NOT NULL,
             surface     TEXT NOT NULL CHECK (surface IN ('pack','turn')),
             cue         TEXT,
             candidates  TEXT NOT NULL,
             injected    TEXT NOT NULL,
             was_useful  INTEGER,
             feedback_note TEXT,
             created_at  TEXT NOT NULL DEFAULT (datetime('now'))
           )""",
        "CREATE INDEX IF NOT EXISTS ix_trace_session ON recall_trace(session_id)",
        "CREATE VIEW IF NOT EXISTS v_recall_precision AS"
        "  SELECT surface, COUNT(*) AS judged, AVG(was_useful) AS precision"
        "  FROM recall_trace WHERE was_useful IS NOT NULL GROUP BY surface",
    ],
    10: [
        # UC-35 handoff memory: one writer session, one reader session, then
        # discarded. Its own table so it can never be consolidated.
        """CREATE TABLE IF NOT EXISTS handoffs (
             id             INTEGER PRIMARY KEY,
             content        TEXT NOT NULL,
             scope          TEXT NOT NULL DEFAULT 'global',
             origin_session TEXT,
             created_at     TEXT NOT NULL DEFAULT (datetime('now')),
             consumed_at    TEXT,
             consumed_by    TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS ix_handoffs_open ON handoffs(scope)"
        "  WHERE consumed_at IS NULL",
    ],
    11: [
        # FR-SL4: labelled outcomes for quarantine decisions feed policy learning.
        """CREATE TABLE IF NOT EXISTS policy_labels (
             id         INTEGER PRIMARY KEY,
             memory_id  INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
             label      TEXT NOT NULL CHECK (label IN ('false_positive','confirmed_hostile')),
             created_at TEXT NOT NULL DEFAULT (datetime('now'))
           )""",
    ],
    12: [
        # #74: content-hash capture idempotence. NULL rows opt out (ordinary
        # remember calls may legitimately repeat), so the unique index is
        # partial; capture and import set the hash and become re-runnable.
        "ALTER TABLE memories ADD COLUMN line_hash TEXT",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_memories_line_hash"
        " ON memories(line_hash) WHERE line_hash IS NOT NULL",
    ],
    13: [
        # #64: write-time origin binding. Trust is set at capture and is
        # immutable to every machine path; only the human `trust` command may
        # raise it (Biba: promotion and consolidation never elevate).
        # Existing rows default to 'agent' - the honest label for memos the
        # model wrote.
        "ALTER TABLE memories ADD COLUMN origin TEXT NOT NULL DEFAULT 'agent'"
        " CHECK (origin IN ('owner','agent','external'))",
    ],
    # #68: valid-time windows on edges. A callable migration: the table-level
    # UNIQUE(src,dst,rel) must widen to include t_valid (recurring
    # relationships: left, rejoined), which SQLite only allows via rebuild,
    # and a rebuild needs an idempotence guard plain SQL cannot express.
    14: [lambda conn: _rebuild_edges_with_validity(conn)],
    15: [
        # #71: edge provenance - how an edge knows what it claims. source is
        # the channel (manual outranks machine paths per the #64 trust model),
        # confidence defaults per channel, status carries quarantine
        # suspension, and edge_sources is the evidence set (which memories
        # back this edge), replacing the single memory_id as the join of
        # record (memory_id kept for compatibility as 'first evidence').
        "ALTER TABLE edges ADD COLUMN confidence REAL NOT NULL DEFAULT 0.9",
        "ALTER TABLE edges ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'"
        " CHECK (source IN ('manual','consolidate','extract'))",
        "ALTER TABLE edges ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
        " CHECK (status IN ('active','suspended'))",
        """CREATE TABLE IF NOT EXISTS edge_sources (
             edge_id    INTEGER NOT NULL REFERENCES edges(id) ON DELETE CASCADE,
             memory_id  INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
             created_at TEXT NOT NULL DEFAULT (datetime('now')),
             PRIMARY KEY (edge_id, memory_id)
           )""",
        "INSERT OR IGNORE INTO edge_sources (edge_id, memory_id)"
        " SELECT id, memory_id FROM edges WHERE memory_id IS NOT NULL",
    ],
    16: [
        # #69: aliases resolve what an entity is CALLED; merge tombstones keep
        # a losing entity as a redirect (status merged + merged_into), so old
        # references never dangle. alias_norm is the lookup key (lowercase,
        # punctuation to space, whitespace collapsed - the workspace-proven
        # convention); alias_raw preserves what was actually written.
        "ALTER TABLE entities ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
        " CHECK (status IN ('active','merged'))",
        "ALTER TABLE entities ADD COLUMN merged_into INTEGER REFERENCES entities(id)",
        """CREATE TABLE IF NOT EXISTS entity_aliases (
             entity_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
             alias_norm TEXT NOT NULL,
             alias_raw  TEXT NOT NULL,
             source     TEXT NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual','consolidate','backfill','merge')),
             created_at TEXT NOT NULL DEFAULT (datetime('now')),
             PRIMARY KEY (entity_id, alias_norm)
           )""",
        "CREATE INDEX IF NOT EXISTS ix_alias_norm ON entity_aliases(alias_norm)",
    ],
    17: [
        # #73: external identity refs - what an entity IS (company number,
        # domain, email, CRM id), where aliases are what it is CALLED. A ref
        # is authoritative and unique across the store: two entities claiming
        # the same ref is definitionally a split entity (merge, not insert).
        """CREATE TABLE IF NOT EXISTS entity_refs (
             entity_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
             kind       TEXT NOT NULL,
             value      TEXT NOT NULL,
             created_at TEXT NOT NULL DEFAULT (datetime('now')),
             PRIMARY KEY (entity_id, kind, value),
             UNIQUE (kind, value)
           )""",
        "CREATE INDEX IF NOT EXISTS ix_refs_kind_value ON entity_refs(kind, value)",
    ],
    18: [
        # #70: governed ontology for entity and edge types. Free-text etype/rel
        # drifts ('person' vs 'people' vs 'contact'); the registry powers lint
        # validation, is_a expansion, and viewer legends. retired status lets
        # vocabulary evolve without breaking old rows; abstract marks
        # supertypes that group but are not assignable.
        """CREATE TABLE IF NOT EXISTS graph_types (
             kind        TEXT NOT NULL CHECK (kind IN ('entity','edge')),
             name        TEXT NOT NULL,
             is_a        TEXT,
             abstract    INTEGER NOT NULL DEFAULT 0,
             symmetric   INTEGER NOT NULL DEFAULT 0,
             src_types   TEXT,
             dst_types   TEXT,
             description TEXT,
             status      TEXT NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','retired')),
             created_at  TEXT NOT NULL DEFAULT (datetime('now')),
             PRIMARY KEY (kind, name)
           )""",
        # Core seed: small, generic, user-extensible. INSERT OR IGNORE keeps
        # re-runs and user overrides safe.
        "INSERT OR IGNORE INTO graph_types (kind, name, is_a, abstract, symmetric,"
        " src_types, dst_types, description) VALUES"
        " ('entity','thing',NULL,0,0,NULL,NULL,'default catch-all'),"
        " ('entity','person',NULL,0,0,NULL,NULL,'a human'),"
        " ('entity','organisation',NULL,0,0,NULL,NULL,'org of any form'),"
        " ('entity','company','organisation',0,0,NULL,NULL,'trading organisation'),"
        " ('entity','team','organisation',0,0,NULL,NULL,'group within an org'),"
        " ('entity','project',NULL,0,0,NULL,NULL,'a piece of work'),"
        " ('entity','system',NULL,0,0,NULL,NULL,'software/service/tool'),"
        " ('entity','file','system',0,0,NULL,NULL,'file or codebase path'),"
        " ('entity','role',NULL,0,0,NULL,NULL,'reified role node (#57)'),"
        " ('entity','place',NULL,0,0,NULL,NULL,'location'),"
        " ('entity','event',NULL,0,0,NULL,NULL,'dated occurrence'),"
        " ('entity','topic',NULL,0,0,NULL,NULL,'subject matter'),"
        " ('edge','works_at',NULL,0,0,'person','organisation',NULL),"
        " ('edge','member_of',NULL,0,0,'person','organisation,team,project',NULL),"
        " ('edge','maintains',NULL,0,0,'person,team','system,file,project',NULL),"
        " ('edge','owns',NULL,0,0,NULL,NULL,NULL),"
        " ('edge','uses',NULL,0,0,NULL,'system,file',NULL),"
        " ('edge','part_of',NULL,0,0,NULL,NULL,NULL),"
        " ('edge','knows',NULL,0,1,'person','person','symmetric acquaintance'),"
        " ('edge','holds',NULL,0,0,'person','role','role-node pattern'),"
        " ('edge','at',NULL,0,0,'role','organisation','role-node pattern'),"
        " ('edge','has_role',NULL,0,0,NULL,'role','reified edge pattern'),"
        " ('edge','with',NULL,0,0,'role',NULL,'reified edge pattern'),"
        " ('edge','related_to',NULL,0,1,NULL,NULL,'weak symmetric link')",
    ],
    19: [
        # #72: mention roles. 'subject' (the memory is ABOUT this entity)
        # outranks 'mentioned' (appears in passing) on every about-X surface.
        # Existing mentions stay 'mentioned' - the honest backfill default.
        "ALTER TABLE memory_entities ADD COLUMN role TEXT NOT NULL DEFAULT 'mentioned'"
        " CHECK (role IN ('subject','mentioned'))",
        "ALTER TABLE memory_entities ADD COLUMN confidence REAL NOT NULL DEFAULT 0.7",
        "DROP VIEW IF EXISTS v_entity_memories",
        "CREATE VIEW v_entity_memories AS"
        "  SELECT e.name AS entity_name, e.etype, me.entity_id, me.role AS mention_role, m.*"
        "  FROM memory_entities me"
        "  JOIN entities e ON e.id = me.entity_id"
        "  JOIN v_active_memories m ON m.id = me.memory_id",
    ],
}


def _rebuild_edges_with_validity(conn: sqlite3.Connection) -> None:
    """#68: rebuild edges with t_valid/t_invalid and UNIQUE(src,dst,rel,t_valid).

    t_valid '' means 'window opened at an unknown time' (the workspace-proven
    convention), keeping NOT NULL so the uniqueness key stays total. Closing a
    window sets t_invalid; closed edges are excluded from default reads but
    never deleted (non-destructive invalidation).
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(edges)")}
    if "t_valid" in cols:
        return  # version-stripped store re-running migrations: already rebuilt
    conn.execute(
        """CREATE TABLE edges_new (
             id         INTEGER PRIMARY KEY,
             src        INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
             dst        INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
             rel        TEXT NOT NULL,
             weight     REAL NOT NULL DEFAULT 1.0,
             memory_id  INTEGER REFERENCES memories(id) ON DELETE SET NULL,
             t_valid    TEXT NOT NULL DEFAULT '',
             t_invalid  TEXT,
             created_at TEXT NOT NULL DEFAULT (datetime('now')),
             UNIQUE (src, dst, rel, t_valid)
           )"""
    )
    conn.execute(
        "INSERT INTO edges_new (id, src, dst, rel, weight, memory_id, created_at)"
        " SELECT id, src, dst, rel, weight, memory_id, created_at FROM edges"
    )
    # The view must go before the rename: ALTER re-parses dependent views.
    conn.execute("DROP VIEW IF EXISTS v_edges_named")
    conn.execute("DROP TABLE edges")
    conn.execute("ALTER TABLE edges_new RENAME TO edges")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst)")
    conn.execute(
        "CREATE VIEW v_edges_named AS"
        "  SELECT e.id, s.name AS src_name, s.etype AS src_etype, e.rel,"
        "         d.name AS dst_name, d.etype AS dst_etype, e.weight, e.memory_id,"
        "         e.t_valid, e.t_invalid, e.created_at"
        "    FROM edges e"
        "    JOIN entities s ON s.id = e.src"
        "    JOIN entities d ON d.id = e.dst"
    )


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


def _snapshot_before_migration(conn: sqlite3.Connection, version: int) -> None:
    """Premortem hardening: an existing store is copied aside before any
    migration touches it. One .bak per source version; fresh inits skip."""
    try:
        db_file = conn.execute("PRAGMA database_list").fetchone()[2]
        if not db_file:
            return
        source = Path(db_file)
        if not source.exists():
            return
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        import shutil

        shutil.copy2(source, source.with_name(source.name + f".v{version}.bak"))
    except Exception:
        pass  # a failed snapshot must not block the session (hooks fail soft)


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    pending = any(target > version for target in MIGRATIONS) if version else False
    if version >= 1 and pending:
        _snapshot_before_migration(conn, version)
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
                if callable(statement):
                    statement(conn)
                elif not _already_applied(conn, statement):
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
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    _migrate(conn)
    return conn
