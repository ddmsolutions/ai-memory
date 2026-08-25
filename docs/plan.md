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

Goal: after two weeks of real use, memory is present when needed (not just at session start), the store stays clean without manual effort, no memo is lost regardless of where it was written, and nothing sensitive is ever persisted.

Honest exit condition: if after a month of dogfooding the store is not observably changing what the agent does, the answer is deletion, not tuning. Native context plus disciplined docs is a strong baseline; this project must beat it to earn its keep.

Build order (one feature branch each, in this sequence; foundations before features):

### Section 1: CI
GitHub Actions workflow: pytest on push and PR, Python 3.10 and 3.12, README badge. Guardrail before anything else changes.
- Status: [x] complete (issue #2)

### Section 2: Schema versioning and migrations
`PRAGMA user_version` plus a small ordered-migration runner in `db.py`. Required BEFORE dogfooding starts: the next schema change must not strand a live store. Test: create at old version, migrate, verify.
- Status: [x] complete (issue #3)

### Section 3: Config file
`~/.ai-memory/config.json` (override path via env), read once, fail-soft to defaults. Carries: decay window, recall pack size, per-section caps, scope mapping. Removes the hardcoded values the coding rules prohibit.
- Status: [x] complete (issue #4)

### Section 4: SubagentStop capture
Extend capture so memos written by subagents are stored, not just main-loop turns. Reuse `extract_memos`; register the hook in hooks.json; dedup by session + content as now.
- Status: [x] complete (issue #5)

### Section 5: Secret filter on capture
Redaction screen before any insert: common credential shapes (API key prefixes, bearer tokens, PEM blocks, high-entropy strings) are masked, with a test per pattern. A memory store must never hold a secret in plaintext.
- Status: [ ] not started

### Section 6: Turn-time recall
UserPromptSubmit hook: FTS-match the user's prompt against the store, inject the top task-relevant memories for THIS turn. The highest-value item in the phase: recall becomes present when needed, not only at session start. Budget-capped via config; fail-soft.
- Status: [ ] not started

### Section 7: Project scoping
Resolve scope automatically from the hook payload's working directory (map cwd to a stable project slug), so packs are project-relevant by default. `--scope` on the CLI keeps working; global memories always included. No per-agent scoping yet.
- Status: [ ] not started

### Section 8: Decay and reinforcement
A `decay` CLI command: episodics older than the configured window (default 30 days) that were never promoted and never recalled are deleted; repeated recall bumps confidence. Deterministic, dry-run flag, never touches pinned or promoted rows. Wire into /memory consolidate.
- Status: [ ] not started

### Section 9: Release hygiene
Version bump to 0.2.0, changelog, docs updated (install, architecture, data-architecture where behaviour changed), tag `v0.2.0`.
- Status: [ ] not started

## Out of scope for v0.2 (deliberately)

- Export / import: portability matters less than trustworthiness while the only user is the owner. Moved to v0.3.
- Automatic entity extraction during consolidation: model-driven and speculative; needs dogfooding evidence first. v0.3.
- Skill-based routing (proactive save/recall without typing /memory): v0.3, after the memo habit proves out.
- Contradiction/duplicate detection at consolidation: v0.3.
- Subagent spawn-time recall injection: deferred until Section 4 shows real demand.
- Embedding/vector search: FTS5 is sufficient at current scale; v0.3 behind the existing `search` interface.
- Graph-aware recall, marketplace listing, any UI or viewer: v0.3 or later (see `roadmap.md`).

## First-draft review (pruning log)

- Re-cut 2026-08-25 (element review): added CI, schema versioning, config, secret filter, and turn-time recall to v0.2; pushed export/import to v0.3. Rationale: the phase goal is trustworthiness in daily use, and a memory that only surfaces at session start, can strand data on schema change, or can persist a secret is not trustworthy. Portability can wait.
- Cut from v0.2 (original draft): automatic entity extraction. Too complex for this phase; manual `entity add/link` covers the need while dogfooding tells us what extraction should actually do.
- Kept small: project scoping does cwd-based scope only; per-agent scoping deferred until subagent capture shows real demand.
- Sequencing rationale: guardrails and foundations first (CI, migrations, config), then capture completeness and safety (lost memos are unrecoverable, leaked secrets worse), then recall relevance (turn-time, scoping), then hygiene (decay), then release.

## Definition of Done (per section)

Works end-to-end, failure paths handled, tests green (`python -m pytest tests/ -q`) including at least one new test for the section, docs updated, committed on a feature branch, pushed, merged to main, main pushed.
