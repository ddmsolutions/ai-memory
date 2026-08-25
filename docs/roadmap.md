# Roadmap

## v0.1 (now)

- SQLite store with the four memory types, FTS5 search, supersession, pinning
- Capture (Stop hook, memo blocks) and recall (SessionStart hook, compiled pack)
- `/memory` command: status, search, remember, consolidate/promote, forget, pin, entity
- Entity graph: typed nodes, typed weighted edges, neighbour queries

## v0.2 - trustworthy in daily use

Build order and section detail: `docs/plan.md`.

- CI (pytest on push/PR) and schema versioning + migrations, before any live store exists
- Config file for the currently hardcoded knobs (decay window, pack size, scope mapping)
- SubagentStop capture so background agents' memos are not lost
- Secret filter: credential-shaped content redacted before any insert
- Turn-time recall: UserPromptSubmit hook injects task-relevant memories per turn, not just at session start
- Scoped memory: project scope resolved automatically from the working directory (per-agent scoping deferred)
- Decay pass: age out unpromoted, unrecalled episodics; confidence reinforcement on repeated recall

## v0.3

- `export` / `import` for moving a store between machines (pushed from v0.2: trustworthiness before portability)
- Skill-based routing: a description-routed skill so the model saves and consults memory proactively, not only via /memory
- Contradiction and near-duplicate detection at consolidation
- Subagent spawn-time recall injection (pairs with v0.2 SubagentStop capture)
- Automatic entity extraction during consolidation (model-driven, engine-verified upserts; pulled from v0.2 pending dogfooding evidence)
- Optional embedding layer behind the existing `search` interface (local model first, fail-soft), hybrid rank with FTS
- Graph queries in recall: pull the entity neighbourhood relevant to the current task into the pack
- Recall budget control: token-aware pack compilation with per-section caps
- Plugin marketplace listing
- Onboarding importer: seed a store from an existing CLAUDE.md or notes file
- CONTRIBUTING.md once outside interest appears

## Later

- Multi-store federation (query several stores by identifier without merging)
- A small HTML graph viewer for the entity store
- Consolidation quality metrics: which promoted memories actually get recalled and used
