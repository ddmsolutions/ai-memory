"""CLI entry point: python -m ai_memory <command> ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, db, graph, store


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai-memory", description="Persistent memory for Claude Code")
    p.add_argument("--db", type=Path, default=None, help="path to memory.db (default ~/.ai-memory/memory.db, or $AI_MEMORY_DB)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database")
    sub.add_parser("status", help="store counts")
    sub.add_parser("version", help="print version")

    r = sub.add_parser("remember", help="store a memory")
    r.add_argument("content", nargs="?", help="omit (or use -) to read from stdin, immune to shell quoting")
    r.add_argument("--type", dest="mtype", default="episodic", choices=store.MEMORY_TYPES)
    r.add_argument("--scope", default="global")
    r.add_argument("--session", dest="origin_session", help="session id this memory was captured from")
    r.add_argument("--confidence", type=float, default=0.7)
    r.add_argument("--pin", action="store_true")
    r.add_argument("--supersedes", type=int, help="id of the memory this one replaces")
    r.add_argument("--valence", choices=store.VALENCES, help="outcome of the episode")
    r.add_argument("--verify-by", dest="verify_by", help="ISO date after which this fact needs re-verification")
    r.add_argument("--origin", choices=store.ORIGINS, default="agent",
                   help="trust level bound at write time (#64); owner for content you typed yourself")

    s = sub.add_parser("search", help="full-text search")
    s.add_argument("query")
    s.add_argument("--type", dest="mtype", choices=store.MEMORY_TYPES)
    s.add_argument("--scope")
    s.add_argument("--limit", type=int, default=20)

    rc = sub.add_parser("recall", help="compile a recall pack (markdown)")
    rc.add_argument("--task")
    rc.add_argument("--scope", default="global")
    rc.add_argument("--limit", type=int, default=None, help="default: config pack_limit")

    f = sub.add_parser("forget", help="delete a memory by id")
    f.add_argument("id", type=int)

    pin = sub.add_parser("pin", help="pin or unpin a memory")
    pin.add_argument("id", type=int)
    pin.add_argument("--off", action="store_true")

    tru = sub.add_parser("trust", help="set a memory's origin trust; running this IS the human approval (#64)")
    tru.add_argument("id", type=int)
    tru.add_argument("--origin", required=True, choices=store.ORIGINS)

    c = sub.add_parser("consolidate", help="list unconsolidated episodics (distil with promote)")
    c.add_argument("--limit", type=int, default=50)

    d = sub.add_parser("decay", help="age out old unpromoted, unrecalled, unpinned episodics")
    d.add_argument("--dry-run", action="store_true", help="list what would go, delete nothing")

    tn = sub.add_parser("tune", help="grid-search tunables against the eval set; adopt only non-degrading configs")
    tn.add_argument("--questions", type=Path)
    tn.add_argument("--k", type=int, default=5)
    tn.add_argument("--grid", type=Path, help="JSON {knob: [values]}; default: built-in retrieval grid")
    tn.add_argument("--adopt", action="store_true", help="write the winning config (previous kept at .prev)")
    tn.add_argument("--revert", action="store_true", help="restore the previous config")

    ev = sub.add_parser("eval", help="run a labelled question set against recall (read-only)")
    ev.add_argument("--questions", type=Path, required=True)
    ev.add_argument("--k", type=int, default=5)
    ev.add_argument("--out", type=Path, help="write the full JSON report here")
    gr = sub.add_parser("graph", help="visual graph viewer: self-contained offline HTML (or --serve for live)")
    gr.add_argument("--out", type=Path, default=Path("graph.html"))
    gr.add_argument("--open", action="store_true", help="open in the default browser")
    gr.add_argument("--include-quarantine", action="store_true")
    gr.add_argument("--include-superseded", action="store_true")
    gr.add_argument("--serve", type=int, metavar="PORT", help="live mode: localhost server, refresh to see DB edits")
    gr.add_argument("--scope", help="embed only this scope plus global (sharing safety)")

    ob = sub.add_parser("observe", help="self-maintenance: read health surfaces, draft the issues they imply")
    ob.add_argument("--post", action="store_true", help="post directly via gh (only honoured when config observer_post = direct)")
    ob.add_argument("--drafts-dir", type=Path)

    po = sub.add_parser("policy", help="policy learning: label quarantine outcomes, validate and adopt patterns")
    posub = po.add_subparsers(dest="policy_command", required=True)
    por = posub.add_parser("release", help="quarantined row judged harmless (records false-positive label)")
    por.add_argument("id", type=int)
    por.add_argument("--scope", default="global")
    poh = posub.add_parser("hostile", help="confirm a quarantined row as genuinely hostile")
    poh.add_argument("id", type=int)
    pov = posub.add_parser("validate", help="corpus-check a proposed instruction pattern")
    pov.add_argument("regex")
    poa = posub.add_parser("adopt", help="adopt a validated pattern into config (human approval step)")
    poa.add_argument("regex")
    poa.add_argument("--label", required=True)
    poa.add_argument("--kind", choices=("instruction", "secret"), default="instruction")

    ac = sub.add_parser("autoconsolidate", help="gated hygiene pass: snapshot, dedupe, triage, decay, regression check")
    ac.add_argument("--questions", type=Path, help="eval set for the regression gate (strongly recommended)")
    ac.add_argument("--dry-run", action="store_true")

    eg = sub.add_parser("eval-grow", help="generate eval questions from real failures")
    eg.add_argument("--out", type=Path, default=Path("evals/generated.json"))
    eg.add_argument("--days", type=int, default=30)

    pr = sub.add_parser("promote", help="promote an episodic into semantic/procedural")
    pr.add_argument("id", type=int)
    pr.add_argument("--type", dest="mtype", required=True, choices=("semantic", "procedural"))
    pr.add_argument("--content", help="rewritten distilled content (defaults to original)")

    e = sub.add_parser("entity", help="entity graph operations")
    esub = e.add_subparsers(dest="entity_command", required=True)
    ea = esub.add_parser("add")
    ea.add_argument("--name", required=True)
    ea.add_argument("--etype", default="thing")
    ea.add_argument("--summary")
    el = esub.add_parser("link")
    el.add_argument("src")
    el.add_argument("dst")
    el.add_argument("--rel", required=True)
    el.add_argument("--weight", type=float, default=1.0)
    el.add_argument("--from", dest="valid_from", default="",
                    help="ISO date the relationship began (#68); allows the same rel to recur")
    el.add_argument("--replaces", action="store_true",
                    help="close any other open window of this src/dst/rel first")
    ec = esub.add_parser("close", help="close an edge's validity window (kept, not deleted)")
    ec.add_argument("src")
    ec.add_argument("dst")
    ec.add_argument("--rel", required=True)
    ec.add_argument("--on", help="ISO end date (default today)")
    es = esub.add_parser("show")
    es.add_argument("name")
    es.add_argument("--history", action="store_true",
                    help="include closed validity windows")
    em = esub.add_parser("mention", help="link a memory to an entity it mentions")
    em.add_argument("memory_id", type=int)
    em.add_argument("name")
    em.add_argument("--etype", default=None)
    eab = esub.add_parser("about", help="everything we know about an entity")
    eab.add_argument("name")
    ew = esub.add_parser("why", help="why we believe an edge: windows, source, evidence (#71)")
    ew.add_argument("src")
    ew.add_argument("dst")
    ew.add_argument("--rel", required=True)
    esub.add_parser("backfill", help="mention-link existing memories via their entities: lines")
    erl = esub.add_parser("role", help="role as a first-class node: holder -holds-> role [-at-> org]")
    erl.add_argument("holder")
    erl.add_argument("title")
    erl.add_argument("--at", dest="org")
    erf = esub.add_parser("reify", help="convert an edge into a per-instance role node")
    erf.add_argument("src")
    erf.add_argument("rel")
    erf.add_argument("dst")

    sub.add_parser("lint", help="store health: duplicates, overdue facts, stale rules, contradictions, quarantine")
    sc = sub.add_parser("scorecard", help="weekly dogfood scorecard (read-only)")
    sc.add_argument("--days", type=int, default=7)
    ei = sub.add_parser("embed-index", help="embed active memories via the configured local model (no-op when disabled)")
    ei.add_argument("--force", action="store_true", help="drop and re-embed (after model/prefix changes)")

    w = sub.add_parser("why", help="explain a memory: origin, lineage, corrections, usage")
    w.add_argument("id", type=int)

    it = sub.add_parser("intend", help="prospective memory: reminders with a trigger")
    itsub = it.add_subparsers(dest="intend_command", required=True)
    ita = itsub.add_parser("add")
    ita.add_argument("content", nargs="?", help="omit (or use -) to read from stdin")
    grp = ita.add_mutually_exclusive_group(required=True)
    grp.add_argument("--when", help="ISO date the reminder becomes due")
    grp.add_argument("--on", help="context words that fire the reminder in a prompt")
    ita.add_argument("--scope", default="global")
    itl = itsub.add_parser("list")
    itl.add_argument("--all", action="store_true", help="include fired/done/expired")
    for name in ("done", "expire", "rearm"):
        cmd = itsub.add_parser(name)
        cmd.add_argument("id", type=int)

    ho = sub.add_parser("handoff", help="state of play for the next session (one writer, one reader)")
    hosub = ho.add_subparsers(dest="handoff_command", required=True)
    hoa = hosub.add_parser("add")
    hoa.add_argument("content", nargs="?", help="omit (or use -) to read from stdin")
    hoa.add_argument("--scope", default="global")
    hosub.add_parser("list")

    tr = sub.add_parser("trace", help="recall traces: what was considered and injected")
    trsub = tr.add_subparsers(dest="trace_command", required=True)
    trl = trsub.add_parser("list")
    trl.add_argument("--limit", type=int, default=20)
    trs = trsub.add_parser("show")
    trs.add_argument("id", type=int)

    fb = sub.add_parser("feedback", help="judge a recall trace; rejection penalises rows and links")
    fb.add_argument("id", type=int)
    fbg = fb.add_mutually_exclusive_group(required=True)
    fbg.add_argument("--useful", action="store_true")
    fbg.add_argument("--not-useful", dest="not_useful", action="store_true")
    fb.add_argument("--note")

    lk = sub.add_parser("link", help="curated typed link between two memories")
    lk.add_argument("src", type=int)
    lk.add_argument("dst", type=int)
    lk.add_argument("--rel", required=True, choices=store.LINK_RELS)
    lk.add_argument("--weight", type=float, default=0.5)

    rel = sub.add_parser("related", help="ranked candidate set of linked memories")
    rel.add_argument("id", type=int)

    ex = sub.add_parser("export", help="export the full store as JSON")
    ex.add_argument("--out", type=Path, help="write to file (default: stdout)")
    im = sub.add_parser("import", help="import an export file (deduplicating)")
    im.add_argument("file", type=Path)
    sd = sub.add_parser("seed", help="seed the store from an existing CLAUDE.md or notes file")
    sd.add_argument("file", type=Path)
    sd.add_argument("--scope", default="global")
    bk = sub.add_parser("backup", help="timestamped JSON export of the full store")
    bk.add_argument("--out", type=Path, help="directory (default ~/.ai-memory/backups)")

    pg = sub.add_parser("purge", help="erase everything about an entity or session (hard delete)")
    pg.add_argument("--entity")
    pg.add_argument("--session")
    pg.add_argument("--yes", action="store_true", help="actually delete; without it, dry-run report only")
    return p


def _content_or_stdin(value: str | None) -> str:
    """Shell quoting mangles real-world content (quotes, apostrophes, leading
    hyphens); stdin bypasses the shell entirely. Empty input fails loud."""
    if value is not None and value != "-":
        return value
    text = sys.stdin.read().strip()
    if not text:
        raise SystemExit("error: no content provided (argument or stdin)")
    return text


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        conn = db.connect(args.db)
    except db.MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.command == "init":
        print(f"initialised {args.db or db.default_db_path()}")
    elif args.command == "version":
        print(__version__)
    elif args.command == "status":
        print(json.dumps(store.status(conn), indent=2))
    elif args.command == "remember":
        mid = store.remember(
            conn, _content_or_stdin(args.content), mtype=args.mtype, scope=args.scope,
            origin_session=args.origin_session,
            confidence=args.confidence, pinned=args.pin, supersedes=args.supersedes,
            valence=args.valence, verify_by=args.verify_by, origin=args.origin,
        )
        print(f"remembered #{mid} ({args.mtype})")
    elif args.command == "search":
        import os as _os

        from . import config as _config

        _cfg = _config.load()
        preferred = None if args.scope else _config.resolve_scope(_os.getcwd(), _cfg)
        for row in store.search(conn, args.query, mtype=args.mtype, scope=args.scope,
                                limit=args.limit, cfg=_cfg, preferred_scope=preferred):
            print(f"#{row['id']} [{row['type']}/{row['scope']}] {row['content']}")
    elif args.command == "recall":
        print(store.recall_pack(conn, task=args.task, scope=args.scope, limit=args.limit))
    elif args.command == "forget":
        store.forget(conn, args.id)
        print(f"forgot #{args.id}")
    elif args.command == "pin":
        store.set_pin(conn, args.id, not args.off)
        print(f"{'unpinned' if args.off else 'pinned'} #{args.id}")
    elif args.command == "trust":
        try:
            report = store.set_trust(conn, args.id, args.origin)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"#{report['id']} origin: {report['before']} -> {report['after']}")
    elif args.command == "consolidate":
        rows = store.unconsolidated(conn, limit=args.limit)
        if not rows:
            print("nothing to consolidate")
        for row in rows:
            tag = f" [{row['valence']}]" if row["valence"] else ""
            print(f"#{row['id']} {row['created_at']}{tag} {row['content']}")
    elif args.command == "tune":
        from . import tuning

        if args.revert:
            print("reverted" if tuning.revert() else "nothing to revert (no .prev)")
            return 0
        if not args.questions:
            print("error: --questions required (or use --revert)", file=sys.stderr)
            return 1
        questions = json.loads(args.questions.read_text(encoding="utf-8"))
        grid = json.loads(args.grid.read_text(encoding="utf-8")) if args.grid else None
        report = tuning.tune(conn, questions, grid=grid, k=args.k)
        print(json.dumps({key: report[key] for key in ("baseline", "best", "adoptable")}, indent=2))
        if args.adopt:
            if report["adoptable"]:
                target = tuning.adopt(report["best"]["overrides"])
                print(f"adopted into {target} (revert with: tune --revert)")
            else:
                print("NOT adopted: best cell does not beat baseline without degradation")
    elif args.command == "graph":
        from . import db as _db, viewer

        conn.close()
        path = args.db or _db.default_db_path()
        if args.serve:
            viewer.serve(path, port=args.serve,
                         include_quarantine=args.include_quarantine,
                         include_superseded=args.include_superseded)
        else:
            out = viewer.write_viewer(path, args.out,
                                      include_quarantine=args.include_quarantine,
                                      include_superseded=args.include_superseded,
                                      scope=args.scope)
            print(f"graph written: {out}")
            if args.open:
                import webbrowser

                webbrowser.open(out.resolve().as_uri())
    elif args.command == "observe":
        from . import observer

        drafts = observer.observe(conn)
        if not drafts:
            print("nothing to raise: health surfaces are quiet")
        else:
            report = observer.emit(drafts, drafts_dir=args.drafts_dir, post_direct=args.post)
            print(json.dumps(report, indent=2))
    elif args.command == "policy":
        from . import policy

        try:
            if args.policy_command == "release":
                policy.release(conn, args.id, scope=args.scope)
                print(f"released #{args.id} to scope {args.scope} (false positive recorded)")
            elif args.policy_command == "hostile":
                policy.confirm_hostile(conn, args.id)
                print(f"confirmed #{args.id} hostile")
            elif args.policy_command == "validate":
                print(json.dumps(policy.validate(conn, args.regex), indent=2))
            elif args.policy_command == "adopt":
                verdict = policy.validate(conn, args.regex)
                if not verdict["valid"]:
                    print(f"NOT adopted, corpus regressions: {json.dumps(verdict)}", file=sys.stderr)
                    return 1
                target = policy.adopt(args.regex, args.label, kind=args.kind)
                print(f"adopted into {target} (revert with: tune --revert)")
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    elif args.command == "autoconsolidate":
        from . import autoconsolidate, db as _db

        conn.close()
        questions = json.loads(args.questions.read_text(encoding="utf-8")) if args.questions else None
        report = autoconsolidate.run(
            args.db or _db.default_db_path(), questions=questions, dry_run=args.dry_run
        )
        print(json.dumps(report, indent=2))
    elif args.command == "eval-grow":
        from . import evalharness

        print(json.dumps(evalharness.grow_questions(conn, args.out, days=args.days)))
    elif args.command == "eval":
        from . import evalharness

        from . import config as _config

        report = evalharness.run_eval_file(conn, args.questions, k=args.k, out_path=args.out,
                                           cfg=_config.load())
        summary = {key: report[key] for key in ("questions", "hits", "hit_rate", "mrr", "misses")}
        print(json.dumps(summary, indent=2))
    elif args.command == "decay":
        rows = store.decay(conn, dry_run=args.dry_run)
        verb = "would decay" if args.dry_run else "decayed"
        if not rows:
            print("nothing to decay")
        for row in rows:
            print(f"{verb} #{row['id']} {row['created_at']} {row['content']}")
    elif args.command == "promote":
        new_id = store.promote(conn, args.id, args.mtype, content=args.content)
        print(f"promoted #{args.id} -> #{new_id} ({args.mtype})")
    elif args.command == "entity":
        if args.entity_command == "add":
            eid = graph.add_entity(conn, args.name, etype=args.etype, summary=args.summary)
            print(f"entity #{eid} {args.name} ({args.etype})")
        elif args.entity_command == "link":
            edge_id = graph.link(conn, args.src, args.dst, rel=args.rel, weight=args.weight,
                                 valid_from=args.valid_from, replaces=args.replaces)
            print(f"edge #{edge_id} {args.src} -{args.rel}-> {args.dst}")
        elif args.entity_command == "close":
            n = graph.close_edge(conn, args.src, args.dst, args.rel, on=args.on)
            print(f"closed {n} window(s): {args.src} -{args.rel}-> {args.dst}"
                  if n else "no open edge matched")
        elif args.entity_command == "show":
            print(graph.describe(conn, args.name, history=args.history))
        elif args.entity_command == "mention":
            graph.mention(conn, args.memory_id, args.name, etype=args.etype)
            print(f"memory #{args.memory_id} mentions {args.name}")
        elif args.entity_command == "role":
            try:
                graph.add_role(conn, args.holder, args.title, org=args.org)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"{args.holder} -holds-> {args.title}" + (f" @ {args.org}" if args.org else ""))
        elif args.entity_command == "reify":
            try:
                graph.reify_edge(conn, args.src, args.rel, args.dst)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"reified: {args.src} -has_role-> [{args.rel}] -with-> {args.dst}")
        elif args.entity_command == "backfill":
            print(json.dumps(graph.backfill_mentions(conn)))
        elif args.entity_command == "why":
            print(graph.edge_why(conn, args.src, args.rel, args.dst))
        elif args.entity_command == "about":
            rows = graph.memories_about(conn, args.name)
            if not rows:
                print(f"nothing recorded about {args.name}")
            for row in rows:
                print(f"#{row['id']} [{row['type']}] {row['content']}")
    elif args.command == "embed-index":
        from . import config as _config, embeddings

        cfg = _config.load()
        if not cfg["embed_enabled"]:
            print("embeddings disabled (config embed_enabled)")
        else:
            print(f"embedded {embeddings.index_memories(conn, cfg, force=args.force)} memories")
    elif args.command == "scorecard":
        print(json.dumps(store.scorecard(conn, days=args.days), indent=2))
    elif args.command == "lint":
        findings = store.lint(conn)
        if not findings:
            print("store is clean")
        for f in findings:
            print(f"[{f['issue']}] #{f['ids']}: {f['detail']}")
    elif args.command == "why":
        print(store.why(conn, args.id))
    elif args.command == "intend":
        if args.intend_command == "add":
            kind = "time" if args.when else "context"
            iid = store.intend(conn, _content_or_stdin(args.content), kind, args.when or args.on, scope=args.scope)
            print(f"intention #{iid} ({kind}: {args.when or args.on})")
        elif args.intend_command == "list":
            where = "" if args.all else "WHERE status = 'pending'"
            for row in conn.execute(f"SELECT * FROM intentions {where} ORDER BY id"):
                print(f"#{row['id']} [{row['status']}] ({row['trigger_kind']}:"
                      f" {row['trigger_value']}) {row['content']}")
        else:
            status = {"done": "done", "expire": "expired", "rearm": "pending"}[args.intend_command]
            store.resolve_intention(conn, args.id, status)
            print(f"intention #{args.id} -> {status}")
    elif args.command == "handoff":
        if args.handoff_command == "add":
            try:
                hid = store.handoff_write(conn, _content_or_stdin(args.content), scope=args.scope)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"handoff #{hid} queued for the next session")
        else:
            rows = conn.execute("SELECT * FROM handoffs ORDER BY id").fetchall()
            if not rows:
                print("no handoffs")
            for row in rows:
                state = f"consumed by {row['consumed_by']}" if row["consumed_at"] else "open"
                print(f"#{row['id']} [{state}/{row['scope']}] {row['content']}")
    elif args.command == "trace":
        if args.trace_command == "list":
            rows = conn.execute(
                "SELECT id, surface, cue, injected, was_useful, created_at FROM recall_trace"
                " ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()
            if not rows:
                print("no traces")
            for row in rows:
                judged = {None: "unjudged", 0: "not useful", 1: "useful"}[row["was_useful"]]
                print(f"#{row['id']} {row['created_at']} [{row['surface']}/{judged}]"
                      f" cue: {row['cue']} injected: {row['injected']}")
        else:
            row = conn.execute("SELECT * FROM recall_trace WHERE id = ?", (args.id,)).fetchone()
            print(json.dumps(dict(row), indent=2) if row else f"no trace #{args.id}")
    elif args.command == "feedback":
        try:
            report = store.feedback(conn, args.id, useful=args.useful, note=args.note)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(report))
    elif args.command == "link":
        store.link_memories(conn, args.src, args.dst, rel=args.rel, weight=args.weight)
        print(f"#{args.src} -{args.rel}-> #{args.dst}")
    elif args.command == "related":
        candidates = store.related(conn, args.id)
        if not candidates:
            print("no linked memories")
        for c in candidates:
            flag = "  AMBIGUOUS" if c["ambiguous_with_top"] else ""
            print(f"#{c['id']} {c['score']:.3f} [{c['rel']}/{c['direction']}] {c['content']}{flag}")
    elif args.command == "export":
        from . import portability

        if args.out:
            portability.export_to_file(conn, args.out)
            print(f"exported to {args.out}")
        else:
            print(json.dumps(portability.export_store(conn), indent=1))
    elif args.command == "import":
        from . import portability

        try:
            print(json.dumps(portability.import_from_file(conn, args.file)))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    elif args.command == "backup":
        from datetime import datetime, timezone

        from . import portability

        outdir = args.out or (Path.home() / ".ai-memory" / "backups")
        outdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = outdir / f"memory-{stamp}.json"
        portability.export_to_file(conn, target)
        print(f"backup written: {target}")
    elif args.command == "seed":
        from . import portability

        report = portability.seed_from_markdown(conn, args.file, scope=args.scope)
        print(f"seeded {report['imported']} memories, skipped {report['skipped']} existing")
    elif args.command == "purge":
        try:
            report = graph.purge_subject(
                conn, entity_name=args.entity, session_id=args.session,
                dry_run=not args.yes,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        verb = "would erase" if report["dry_run"] else "erased"
        print(f"{verb}: {report['memories']} memories, {report['entities']} entities,"
              f" {report['edges']} edges" + ("" if args.yes else "  (add --yes to delete)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
