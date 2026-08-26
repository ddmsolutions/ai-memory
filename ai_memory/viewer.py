"""Visual graph viewer (FR-G5..G8): a single self-contained offline HTML over
both layers (entities + edges, memories + links) joined by mentions.

The viewer is a read surface: it reads through v_active_memories semantics
(quarantine and superseded excluded by default); --include-quarantine renders
those nodes visually branded untrusted. The emitted file embeds the vendored
force-graph library and the data: it never touches the network.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import config as config_mod, db, store

ASSETS = Path(__file__).resolve().parent / "assets"


def effective_at(weight_raw: float, age_days: float, half_life: float, offset_days: float = 0) -> float:
    """Mirror of store._link_effective, exposed so the client-side decay
    scrubber and the engine provably share one formula (parity-tested)."""
    return weight_raw * (1.0 / (1.0 + (age_days + offset_days) / half_life))


def export_graph_json(
    conn: sqlite3.Connection,
    cfg: dict | None = None,
    include_quarantine: bool = False,
    include_superseded: bool = False,
) -> dict:
    if cfg is None:
        cfg = config_mod.load()
    half = float(cfg["link_half_life_days"])
    today = date.today().isoformat()
    nodes: list[dict] = []
    links: list[dict] = []
    memory_ids: set[int] = set()

    where = []
    if not include_superseded:
        where.append("superseded_by IS NULL")
    if not include_quarantine:
        where.append("scope <> 'quarantine'")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    for m in conn.execute(f"SELECT * FROM memories{clause}"):
        memory_ids.add(m["id"])
        nodes.append({
            "id": f"m{m['id']}",
            "kind": "memory",
            "type": m["type"],
            "scope": m["scope"],
            "label": m["content"][:70],
            "content": m["content"],
            "created": m["created_at"][:10],
            "recall_count": m["recall_count"],
            "confidence": m["confidence"],
            "pinned": bool(m["pinned"]),
            "valence": m["valence"],
            "verify_overdue": bool(m["verify_by"] and m["verify_by"][:10] <= today),
            "quarantined": m["scope"] == "quarantine",
            "superseded": m["superseded_by"] is not None,
            "promoted_from": m["promoted_from"],
        })
        if include_superseded and m["superseded_by"] is not None:
            links.append({
                "source": f"m{m['id']}", "target": f"m{m['superseded_by']}",
                "kind": "supersedes", "rel": "superseded_by", "weight": 0.6,
            })
    for e in conn.execute("SELECT * FROM entities"):
        nodes.append({
            "id": f"e{e['id']}", "kind": "entity", "type": e["etype"],
            "scope": "global", "label": e["name"], "content": e["summary"] or "",
            "created": e["created_at"][:10],
        })
    for edge in conn.execute("SELECT * FROM edges"):
        links.append({
            "source": f"e{edge['src']}", "target": f"e{edge['dst']}",
            "kind": "edge", "rel": edge["rel"], "weight": edge["weight"],
        })
    for me in conn.execute("SELECT * FROM memory_entities"):
        if me["memory_id"] in memory_ids:
            links.append({
                "source": f"m{me['memory_id']}", "target": f"e{me['entity_id']}",
                "kind": "mention", "rel": "mentions", "weight": 0.4,
            })
    for l in conn.execute(
        "SELECT *, julianday('now') - julianday(last_reinforced) AS age_days FROM memory_links"
    ):
        if l["src_memory"] in memory_ids and l["dst_memory"] in memory_ids:
            links.append({
                "source": f"m{l['src_memory']}", "target": f"m{l['dst_memory']}",
                "kind": "link", "rel": l["rel"],
                "weight_raw": l["weight"], "age_days": round(l["age_days"], 2),
                "weight": round(effective_at(l["weight"], l["age_days"], half), 4),
            })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "link_half_life_days": half,
        "link_prune_floor": float(cfg["link_prune_floor"]),
        "counts": {"nodes": len(nodes), "links": len(links)},
        "nodes": nodes,
        "links": links,
    }


def build_html(data: dict | None, serve_mode: bool = False) -> str:
    template = (ASSETS / "template.html").read_text(encoding="utf-8")
    lib = (ASSETS / "force-graph.min.js").read_text(encoding="utf-8")
    # strip any sourcemap pointer: the file must trigger zero external fetches
    lib = "\n".join(l for l in lib.splitlines() if not l.strip().startswith("//# sourceMappingURL"))
    html = template.replace("/*__LIB__*/", lib)
    html = html.replace("__MODE__", "serve" if serve_mode else "embedded")
    # <-escape so content containing "</script>" can never break out of
    # the embedding script block; identical JSON once parsed.
    payload = "null" if serve_mode else json.dumps(data).replace("<", "\\u003c")
    html = html.replace("__DATA__", payload)
    return html


def write_viewer(
    db_path: Path,
    out_path: Path,
    include_quarantine: bool = False,
    include_superseded: bool = False,
) -> Path:
    conn = db.connect(db_path)
    data = export_graph_json(
        conn, include_quarantine=include_quarantine, include_superseded=include_superseded
    )
    conn.close()
    out_path = Path(out_path)
    out_path.write_text(build_html(data), encoding="utf-8")
    return out_path


def serve(
    db_path: Path,
    port: int = 8377,
    include_quarantine: bool = False,
    include_superseded: bool = False,
):  # pragma: no cover - thin wrapper; handler logic tested via make_handler
    server = HTTPServer(("127.0.0.1", port), make_handler(
        db_path, include_quarantine, include_superseded))
    print(f"serving live graph on http://127.0.0.1:{port} (Ctrl+C to stop)")
    server.serve_forever()


def make_handler(db_path: Path, include_quarantine: bool, include_superseded: bool):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/data.json":
                conn = db.connect(db_path)
                body = json.dumps(export_graph_json(
                    conn, include_quarantine=include_quarantine,
                    include_superseded=include_superseded)).encode("utf-8")
                conn.close()
                ctype = "application/json"
            elif self.path == "/":
                body = build_html(None, serve_mode=True).encode("utf-8")
                ctype = "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler
