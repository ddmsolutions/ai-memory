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
    r.add_argument("content")
    r.add_argument("--type", dest="mtype", default="episodic", choices=store.MEMORY_TYPES)
    r.add_argument("--scope", default="global")
    r.add_argument("--session", dest="origin_session", help="session id this memory was captured from")
    r.add_argument("--confidence", type=float, default=0.7)
    r.add_argument("--pin", action="store_true")
    r.add_argument("--supersedes", type=int, help="id of the memory this one replaces")
    r.add_argument("--valence", choices=store.VALENCES, help="outcome of the episode")
    r.add_argument("--verify-by", dest="verify_by", help="ISO date after which this fact needs re-verification")

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

    c = sub.add_parser("consolidate", help="list unconsolidated episodics (distil with promote)")
    c.add_argument("--limit", type=int, default=50)

    d = sub.add_parser("decay", help="age out old unpromoted, unrecalled, unpinned episodics")
    d.add_argument("--dry-run", action="store_true", help="list what would go, delete nothing")

    ev = sub.add_parser("eval", help="run a labelled question set against recall (read-only)")
    ev.add_argument("--questions", type=Path, required=True)
    ev.add_argument("--k", type=int, default=5)
    ev.add_argument("--out", type=Path, help="write the full JSON report here")

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
    es = esub.add_parser("show")
    es.add_argument("name")
    em = esub.add_parser("mention", help="link a memory to an entity it mentions")
    em.add_argument("memory_id", type=int)
    em.add_argument("name")
    em.add_argument("--etype", default=None)
    eab = esub.add_parser("about", help="everything we know about an entity")
    eab.add_argument("name")

    w = sub.add_parser("why", help="explain a memory: origin, lineage, corrections, usage")
    w.add_argument("id", type=int)

    it = sub.add_parser("intend", help="prospective memory: reminders with a trigger")
    itsub = it.add_subparsers(dest="intend_command", required=True)
    ita = itsub.add_parser("add")
    ita.add_argument("content")
    grp = ita.add_mutually_exclusive_group(required=True)
    grp.add_argument("--when", help="ISO date the reminder becomes due")
    grp.add_argument("--on", help="context words that fire the reminder in a prompt")
    ita.add_argument("--scope", default="global")
    itl = itsub.add_parser("list")
    itl.add_argument("--all", action="store_true", help="include fired/done/expired")
    for name in ("done", "expire", "rearm"):
        cmd = itsub.add_parser(name)
        cmd.add_argument("id", type=int)

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

    pg = sub.add_parser("purge", help="erase everything about an entity or session (hard delete)")
    pg.add_argument("--entity")
    pg.add_argument("--session")
    pg.add_argument("--yes", action="store_true", help="actually delete; without it, dry-run report only")
    return p


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
            conn, args.content, mtype=args.mtype, scope=args.scope,
            origin_session=args.origin_session,
            confidence=args.confidence, pinned=args.pin, supersedes=args.supersedes,
            valence=args.valence, verify_by=args.verify_by,
        )
        print(f"remembered #{mid} ({args.mtype})")
    elif args.command == "search":
        for row in store.search(conn, args.query, mtype=args.mtype, scope=args.scope, limit=args.limit):
            print(f"#{row['id']} [{row['type']}/{row['scope']}] {row['content']}")
    elif args.command == "recall":
        print(store.recall_pack(conn, task=args.task, scope=args.scope, limit=args.limit))
    elif args.command == "forget":
        store.forget(conn, args.id)
        print(f"forgot #{args.id}")
    elif args.command == "pin":
        store.set_pin(conn, args.id, not args.off)
        print(f"{'unpinned' if args.off else 'pinned'} #{args.id}")
    elif args.command == "consolidate":
        rows = store.unconsolidated(conn, limit=args.limit)
        if not rows:
            print("nothing to consolidate")
        for row in rows:
            tag = f" [{row['valence']}]" if row["valence"] else ""
            print(f"#{row['id']} {row['created_at']}{tag} {row['content']}")
    elif args.command == "eval":
        from . import evalharness

        report = evalharness.run_eval_file(conn, args.questions, k=args.k, out_path=args.out)
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
            edge_id = graph.link(conn, args.src, args.dst, rel=args.rel, weight=args.weight)
            print(f"edge #{edge_id} {args.src} -{args.rel}-> {args.dst}")
        elif args.entity_command == "show":
            print(graph.describe(conn, args.name))
        elif args.entity_command == "mention":
            graph.mention(conn, args.memory_id, args.name, etype=args.etype)
            print(f"memory #{args.memory_id} mentions {args.name}")
        elif args.entity_command == "about":
            rows = graph.memories_about(conn, args.name)
            if not rows:
                print(f"nothing recorded about {args.name}")
            for row in rows:
                print(f"#{row['id']} [{row['type']}] {row['content']}")
    elif args.command == "why":
        print(store.why(conn, args.id))
    elif args.command == "intend":
        if args.intend_command == "add":
            kind = "time" if args.when else "context"
            iid = store.intend(conn, args.content, kind, args.when or args.on, scope=args.scope)
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
