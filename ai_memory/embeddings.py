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
        conn.commit()
    rows = conn.execute(
        "SELECT m.id, m.content FROM v_active_memories m"
        " LEFT JOIN memory_embeddings e ON e.memory_id = m.id AND e.model = ?"
        " WHERE e.memory_id IS NULL AND m.scope <> 'quarantine' LIMIT ?",
        (cfg["embed_model"], batch),
    ).fetchall()
    done = 0
    for row in rows:
        vector = embed_text(row["content"], cfg, kind="document")
        if vector is None:
            break
        conn.execute(
            "INSERT OR REPLACE INTO memory_embeddings (memory_id, model, vector)"
            " VALUES (?, ?, ?)",
            (row["id"], cfg["embed_model"], json.dumps(vector)),
        )
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
    """Top memory ids by cosine similarity to the query. Empty on any failure."""
    query_vec = embed_text(query, cfg, kind="query")
    if query_vec is None:
        return []
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
