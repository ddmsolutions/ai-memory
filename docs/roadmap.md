# Roadmap

## v0.1 (now)

- SQLite store with the four memory types, FTS5 search, supersession, pinning
- Capture (Stop hook, memo blocks) and recall (SessionStart hook, compiled pack)
- `/memory` command: status, search, remember, consolidate/promote, forget, pin, entity
- Entity graph: typed nodes, typed weighted edges, neighbour queries

## v0.2

Build order and section detail: `docs/plan.md`.

- SubagentStop capture so background agents' memos are not lost
- Scoped memory: project scope resolved automatically from the working directory (per-agent scoping deferred)
- Decay pass: age out unpromoted, unrecalled episodics; confidence reinforcement on repeated recall
- `export` / `import` for moving a store between machines

## v0.3

- Automatic entity extraction during consolidation (model-driven, engine-verified upserts; pulled from v0.2 pending dogfooding evidence)
- Optional embedding layer behind the existing `search` interface (local model first, fail-soft), hybrid rank with FTS
- Graph queries in recall: pull the entity neighbourhood relevant to the current task into the pack
- Recall budget control: token-aware pack compilation with per-section caps
- Plugin marketplace listing

## Later

- Multi-store federation (query several stores by identifier without merging)
- A small HTML graph viewer for the entity store
- Consolidation quality metrics: which promoted memories actually get recalled and used
