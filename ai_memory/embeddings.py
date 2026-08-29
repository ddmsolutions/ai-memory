"""Optional embedding layer (FR-V1): local model, fail-soft, behind search.

Disabled by default (config embed_enabled). When enabled, vectors come from
an Ollama-compatible /api/embeddings endpoint; any failure (server down,
model missing, timeout) degrades silently to FTS-only search. Absence
changes nothing, by requirement.
"""

from __future__ import annotations

import json
import math
import sqlite3
import urllib.request


def _vec_conn(conn: sqlite3.Connection, dim: int | None = None) -> bool:
    """#74: load the optional sqlite-vec extension and ensure the ANN table.
    True when the vec0 index is usable on this connection; any failure
    (package absent, extension loading disabled) means the JSON scan path.
    """
    try:
        import sqlite_vec  # type: ignore[import-not-found]

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        if dim is not None:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory USING"
                f" vec0(memory_id INTEGER PRIMARY KEY, embedding float[{int(dim)}])"
            )
        else:
            # Query path: usable only if the table already exists.
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'vec_memory'"
            ).fetchone() is None:
                return False
        return True
    except Exception:
        return False


def _vec_upsert(conn: sqlite3.Connection, memory_id: int, vector: list[float]) -> None:
    try:
        import sqlite_vec  # type: ignore[import-not-found]

        conn.execute("DELETE FROM vec_memory WHERE memory_id = ?", (memory_id,))
        conn.execute(
            "INSERT INTO vec_memory (memory_id, embedding) VALUES (?, ?)",
            (memory_id, sqlite_vec.serialize_float32(vector)),
        )
    except Exception:
        pass


def embed_text(text: str, cfg: dict, kind: str = "document") -> list[float] | None:
    """kind selects the model task prefix (query vs document): embedding models
    trained with prefixes measurably underperform on raw text (#59)."""
    prefix = cfg.get("embed_query_prefix" if kind == "query" else "embed_doc_prefix") or ""
    try:
        req = urllib.request.Request(
            f"{cfg['embed_url']}/api/embeddings",
            data=json.dumps({"model": cfg["embed_model"], "prompt": prefix + text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            vector = json.loads(resp.read()).get("embedding")
        return vector if isinstance(vector, list) and vector else None
    except Exception:
        return None


def index_memories(conn: sqlite3.Connection, cfg: dict, batch: int = 500, force: bool = False) -> int:
    """Embed active rows that lack a vector for the configured model.
    Stops at the first embedding failure (server gone) rather than looping.
    force drops the model's existing vectors first (prefix/model changes)."""
    if force:
        conn.execute("DELETE FROM memory_embeddings WHERE model = ?", (cfg["embed_model"],))
        try:
            if _vec_conn(conn):
                conn.execute("DELETE FROM vec_memory")
        except Exception:
            pass
        conn.commit()
    rows = conn.execute(
        "SELECT m.id, m.content FROM v_active_memories m"
        " LEFT JOIN memory_embeddings e ON e.memory_id = m.id AND e.model = ?"
        " WHERE e.memory_id IS NULL AND m.scope <> 'quarantine' LIMIT ?",
        (cfg["embed_model"], batch),
    ).fetchall()
    done = 0
    vec_ready: bool | None = None
    for row in rows:
        vector = embed_text(row["content"], cfg, kind="document")
        if vector is None:
            break
        conn.execute(
            "INSERT OR REPLACE INTO memory_embeddings (memory_id, model, vector)"
            " VALUES (?, ?, ?)",
            (row["id"], cfg["embed_model"], json.dumps(vector)),
        )
        # #74: mirror into the ANN index when sqlite-vec is present (fail-soft).
        if vec_ready is None:
            vec_ready = _vec_conn(conn, dim=len(vector))
        if vec_ready:
            _vec_upsert(conn, row["id"], vector)
        done += 1
    if done:
        conn.commit()
    return done


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def semantic_candidates(
    conn: sqlite3.Connection, query: str, cfg: dict, limit: int
) -> list[tuple[int, float]]:
    """Top memory ids by cosine similarity to the query. Empty on any failure.

    Prefers the sqlite-vec ANN index when present (#74); the JSON scan is the
    fallback so absence of the extension changes nothing but speed.
    """
    query_vec = embed_text(query, cfg, kind="query")
    if query_vec is None:
        return []
    if _vec_conn(conn):
        try:
            import sqlite_vec  # type: ignore[import-not-found]

            rows = conn.execute(
                "SELECT memory_id, distance FROM vec_memory"
                " WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (sqlite_vec.serialize_float32(query_vec), limit),
            ).fetchall()
            if rows:
                # vec0 distance is L2; monotonic with cosine for normalised
                # embeddings, so rank order (all RRF uses) is preserved.
                return [(r["memory_id"], 1.0 / (1.0 + r["distance"])) for r in rows]
        except Exception:
            pass
    scored = []
    for row in conn.execute(
        "SELECT memory_id, vector FROM memory_embeddings WHERE model = ?",
        (cfg["embed_model"],),
    ):
        try:
            sim = _cosine(query_vec, json.loads(row["vector"]))
        except Exception:
            continue
        scored.append((row["memory_id"], sim))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:limit]
