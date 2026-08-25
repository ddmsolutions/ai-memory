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

    s = sub.add_parser("search", help="full-text search")
    s.add_argument("query")
    s.add_argument("--type", dest="mtype", choices=store.MEMORY_TYPES)
    s.add_argument("--scope")
    s.add_argument("--limit", type=int, default=20)

    rc = sub.add_parser("recall", help="compile a recall pack (markdown)")
    rc.add_argument("--task")
    rc.add_argument("--scope", default="global")
    rc.add_argument("--limit", type=int, default=12)

    f = sub.add_parser("forget", help="delete a memory by id")
    f.add_argument("id", type=int)

    pin = sub.add_parser("pin", help="pin or unpin a memory")
    pin.add_argument("id", type=int)
    pin.add_argument("--off", action="store_true")

    c = sub.add_parser("consolidate", help="list unconsolidated episodics (distil with promote)")
    c.add_argument("--limit", type=int, default=50)

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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = db.connect(args.db)

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
            print(f"#{row['id']} {row['created_at']} {row['content']}")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
