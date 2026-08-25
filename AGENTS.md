# AGENTS.md - ai-memory

Project-specific rules for coding agents working in this repo. Extends the owner's universal coding rules (feature branches, commit-and-push every working change, `type: description` messages, files under 300 lines, functions under 50, no hardcoded values, loading/error handling on every path that can fail).

## What this is

A Claude Code plugin providing persistent memory in four stores: episodic, semantic, procedural, and entity (knowledge graph). Local-first, one SQLite file, stdlib only. See `README.md` and `docs/architecture.md` before changing anything.

## Stack

- Python 3.10+, standard library ONLY. Do not add dependencies; if a feature seems to need one, it can almost certainly be built on sqlite3/json/re in under 50 lines, or it belongs on the roadmap as an optional fail-soft layer.
- SQLite with FTS5. Schema lives in `ai_memory/db.py` and nowhere else.
- pytest for tests (dev-only dependency).

## Layout

```
ai_memory/        the engine: db.py (schema), store.py (memories), graph.py (entities), __main__.py (CLI)
hooks/            Claude Code hook scripts + hooks.json (SessionStart inject, Stop capture)
commands/         the /memory slash command
docs/             architecture, install, roadmap
tests/            pytest suite; every behaviour change lands with a test
.claude-plugin/   plugin manifest
```

## Conventions

- Hooks MUST fail soft: catch everything, exit 0. A broken memory store must never block a session. This is a hard invariant; there is a test for capture and there must remain one for any new hook.
- Memory rows are plain readable text. No opaque blobs, no pickled objects.
- Supersession over mutation: to correct a memory, insert a new row with `supersedes`, never rewrite history in place.
- The engine does deterministic bookkeeping; judgement (what to remember, how to distil) belongs to the model via `commands/memory.md`. Keep that boundary.
- DB path resolution goes through `db.default_db_path()` (`AI_MEMORY_DB` override). Never hardcode a path.
- No em or en dashes in any file. Hyphens, commas, colons.
- British spelling in prose, US spelling only where an API demands it.

## Working here

- Branch from `main` (`feature/`, `fix/`, `experiment/`), push every commit, merge locally when tested.
- Run `python -m pytest tests/ -q` before any merge; all tests green is part of "done".
- `*.db` files are gitignored instance data. Never commit one, never commit anything containing real memory content.
- Update `docs/roadmap.md` when scope moves between versions, in the same commit as the change.
