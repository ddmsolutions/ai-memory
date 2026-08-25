# Project plan - ai-memory

The working plan for this project. One section per feature branch; a section is done when it meets the Definition of Done below. Update this file (mark sections complete, move scope) in the same commit as the change it describes.

## What is being built

A Claude Code plugin that gives sessions persistent memory across four stores: episodic (what happened), semantic (durable facts), procedural (how to work), and entity (a typed knowledge graph). Capture and recall are wired into the session lifecycle by hooks; consolidation is a model-driven pass over the CLI primitives. Local-first: one SQLite file, no server, no cloud, no required dependencies.

## Who it is for

- Primary: individual Claude Code users (solo developers, consultants) who want their agent to stop forgetting decisions, facts, and corrections between sessions.
- Secondary: teams running Claude Code in shared repos who want a per-project memory store checked out of band (never committed).
- First user and dogfooder: the project owner, running it against real daily work.

## Tech stack

Python 3.10+ standard library only. SQLite with FTS5. pytest (dev only). Claude Code plugin surface: hooks (SessionStart, Stop, SubagentStop), one slash command, plugin manifest. Constraint carried from AGENTS.md: no runtime dependencies, ever; optional layers must fail soft.

## Delivered

### v0.1 - core engine (complete, on main)

Four-store schema, FTS5 search, supersession, pinning, promote/consolidate primitives, recall pack compilation, Stop-hook memo capture, SessionStart injection, /memory command, entity graph CRUD, 9 tests, public repo.

## Current phase: v0.2 - make it trustworthy in daily use

Goal: after two weeks of real use, the store stays clean without manual effort, and no memo is lost regardless of where it was written.

Build order (one feature branch each, in this sequence):

### Section 1: SubagentStop capture
Extend capture so memos written by subagents are stored, not just main-loop turns. Reuse `extract_memos`; register the hook in hooks.json; dedup by session + content as now.
- Status: [ ] not started

### Section 2: Project scoping
Resolve scope automatically from the hook payload's working directory (map cwd to a stable project slug), so recall packs are project-relevant by default. `--scope` on the CLI keeps working; global memories always included. No per-agent scoping yet.
- Status: [ ] not started

### Section 3: Decay and reinforcement
A `decay` CLI command: episodics older than a configurable window (default 30 days) that were never promoted and never recalled are deleted; repeated recall bumps confidence. Deterministic, dry-run flag, never touches pinned or promoted rows. Wire into the /memory command as part of consolidate.
- Status: [ ] not started

### Section 4: Export / import
`export` to JSON (full store) and `import` with dedup, so a store can move between machines or be backed up. Round-trip test required.
- Status: [ ] not started

### Section 5: Release hygiene
Version bump to 0.2.0, changelog, docs updated (install, architecture where behaviour changed), tag `v0.2.0`.
- Status: [ ] not started

## Out of scope for v0.2 (deliberately)

- Automatic entity extraction during consolidation: model-driven and speculative; needs dogfooding evidence first. Moved to v0.3.
- Embedding/vector search: FTS5 is sufficient at current scale; revisit at v0.3 behind the existing `search` interface.
- Graph-aware recall, token-budgeted packs, marketplace listing: v0.3 (see `roadmap.md`).
- Any UI or viewer: later.

## First-draft review (pruning log)

- Cut from v0.2: automatic entity extraction (was on the roadmap for v0.2). Too complex for this phase; manual `entity add/link` covers the need while dogfooding tells us what extraction should actually do.
- Kept small: project scoping does cwd-based scope only; per-agent scoping deferred until subagent capture (Section 1) shows real demand.
- Sequencing rationale: capture completeness first (lost memos are unrecoverable), then relevance (scoping), then hygiene (decay), then portability.

## Definition of Done (per section)

Works end-to-end, failure paths handled, tests green (`python -m pytest tests/ -q`) including at least one new test for the section, docs updated, committed on a feature branch, pushed, merged to main, main pushed.
