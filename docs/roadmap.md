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

- Prospective memory: a fifth type for intentions ("next session, check X"; "when touching repo Y, remember Z") with a trigger condition (time or context), fired via the recall pack's pending-intentions section and turn-time recall, then marked complete or expired. Unlike the other types it has a terminal lifecycle.
- Valence on episodic rows: an `outcome` flag (success / failure / neutral) so "what went wrong last time we tried X" is answerable and consolidation can weight failures into procedural rules.
- Staleness on semantic rows: a `verify_by` date; recall flags facts past it as "verify before relying". A fact's age becomes visible instead of silent.
- Injection screen at capture: instruction-shaped memo content ("ignore previous...", imperatives aimed at the model) is flagged or refused, closing the poisoned-memo -> persistent-prompt-injection path. Pairs with the v0.2 secret filter.
- Purge by subject: delete everything about an entity or a session in one command, including graph edges. The GDPR-shaped forget.
- `why <id>`: explain a memory - captured when, from which session, promoted from what, recalled how often. The lineage columns already hold this; expose it.
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

- Handoff (working) memory: a session-continuity row ("state of play: migration half done, tests 3 and 7 failing") written by a dying session, consumed by the next, then discarded, never consolidated. Distinct lifecycle: one writer, one reader.
- Recall utility feedback: learn whether an injected memory actually helped (e.g. was its subject touched this session), so ranking improves instead of counting injections. The deepest open problem in the design.
- Case-based memory: a worked-examples library ("how we solved X"), promotable from episodic clusters
- Multi-store federation (query several stores by identifier without merging)
- A small HTML graph viewer for the entity store
- Consolidation quality metrics: which promoted memories actually get recalled and used (feeds utility feedback)

Deliberately NOT planned: a user-preference type (that is semantic/procedural with a scope), spatial/codebase memory (the entity graph covers it with etype file/system), and working-memory-as-scratchpad (the context window already is one).
