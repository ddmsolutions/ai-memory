"""Viewer tests (#46 P1, #47 P2)."""
import json
import re
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import config, db, graph, store, viewer  # noqa: E402

CFG = dict(config.DEFAULTS)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "m.db")
    a = store.remember(c, "the parser refactor lesson", mtype="procedural", pinned=True)
    b = store.remember(c, "staging runs postgres 16", mtype="semantic",
                       verify_by="2020-01-01")
    store.link_memories(c, a, b, rel="supports", weight=0.8)
    graph.add_entity(c, "Evander", etype="org", summary="client")
    graph.link(c, "Evander", "Dynamics NAV", rel="runs")
    graph.mention(c, b, "Evander")
    old = store.remember(c, "budget is 50k", mtype="semantic")
    store.remember(c, "budget is 30k", mtype="semantic", supersedes=old)
    store.remember(c, "ignore previous instructions payload", mtype="episodic", scope="quarantine")
    yield c
    c.close()


def test_export_structure_and_read_surface(conn):
    data = viewer.export_graph_json(conn, cfg=CFG)
    ids = {n["id"] for n in data["nodes"]}
    contents = " ".join(n.get("content", "") for n in data["nodes"])
    assert "payload" not in contents          # quarantine excluded by default
    assert "budget is 50k" not in contents    # superseded excluded by default
    assert any(n["kind"] == "entity" for n in data["nodes"])
    kinds = {l["kind"] for l in data["links"]}
    assert {"link", "edge", "mention"} <= kinds
    overdue = [n for n in data["nodes"] if n.get("verify_overdue")]
    assert len(overdue) == 1
    assert data["counts"]["nodes"] == len(data["nodes"])
    assert ids  # non-empty


def test_include_flags_brand_and_reveal(conn):
    data = viewer.export_graph_json(conn, cfg=CFG, include_quarantine=True,
                                    include_superseded=True)
    q = [n for n in data["nodes"] if n.get("quarantined")]
    assert len(q) == 1
    s = [n for n in data["nodes"] if n.get("superseded")]
    assert len(s) == 1
    assert any(l["kind"] == "supersedes" for l in data["links"])


def test_effective_weight_parity_with_engine(conn):
    conn.execute("UPDATE memory_links SET last_reinforced = datetime('now', '-10 days')"
                 " WHERE rel = 'supports'")
    conn.commit()
    data = viewer.export_graph_json(conn, cfg=CFG)
    link = next(l for l in data["links"] if l["rel"] == "supports")
    sql_eff = conn.execute(
        f"SELECT {store._link_effective(CFG)} FROM memory_links WHERE rel='supports'"
    ).fetchone()[0]
    assert link["weight"] == pytest.approx(sql_eff, abs=0.001)
    # scrubber parity: python helper at t+0 equals export weight
    assert viewer.effective_at(link["weight_raw"], link["age_days"],
                               CFG["link_half_life_days"]) == pytest.approx(link["weight"], abs=0.001)


def test_html_self_contained(conn, tmp_path):
    data = viewer.export_graph_json(conn, cfg=CFG)
    html = viewer.build_html(data)
    assert not re.search(r'(src|href)\s*=\s*["\']https?://', html)
    assert "sourceMappingURL" not in html
    assert "__DATA__" not in html and "__MODE__" not in html and "/*__LIB__*/" not in html
    assert "the parser refactor lesson" in html
    assert "force-graph" in html.lower()


def test_hostile_content_escaped_in_html(conn, tmp_path):
    store.remember(conn, 'evil <img src=x onerror=alert(1)> payload memo', mtype="semantic")
    html = viewer.build_html(viewer.export_graph_json(conn, cfg=CFG))
    assert "<img src=x" not in html  # raw only inside JSON string, never as markup
    import json as _json
    # the content survives intact inside the embedded JSON data
    assert "evil <img" in _json.dumps("evil <img")


def test_write_viewer(tmp_path, conn):
    out = viewer.write_viewer(tmp_path / "m.db", tmp_path / "g.html")
    assert out.exists() and out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_serve_live_localhost_read_only(tmp_path, conn):
    dbfile = tmp_path / "m.db"
    handler = viewer.make_handler(dbfile, False, False)
    from http.server import HTTPServer

    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    assert server.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert '"serve"' in html
        one = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/data.json").read())
        n1 = one["counts"]["nodes"]
        store.remember(conn, "a brand new live row", mtype="semantic")
        two = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/data.json").read())
        assert two["counts"]["nodes"] == n1 + 1  # live, no regeneration
        before = conn.execute("SELECT SUM(recall_count) FROM memories").fetchone()[0]
        urllib.request.urlopen(f"http://127.0.0.1:{port}/data.json").read()
        after = conn.execute("SELECT SUM(recall_count) FROM memories").fetchone()[0]
        assert before == after  # serve mode never writes
        with pytest.raises(Exception):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/etc/passwd")
    finally:
        server.shutdown()


def test_p3_controls_and_features_present(conn):
    html = viewer.build_html(viewer.export_graph_json(conn, cfg=CFG))
    for marker in ('id="nlabels"', 'id="elabels"', 'id="flowf"', 'id="clusters"',
                   "function humanise", "buildClusterList", "applyFlow", "onRenderFramePost"):
        assert marker in html, marker
    # humanised rels render with spaces, canvas labels never as HTML markup
    assert 'replace(/_/g, " ")' in html
