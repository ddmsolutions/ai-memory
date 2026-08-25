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

## v0.3 - SHIPPED 2026-08-25 (v0.3.0)

Everything below delivered except where marked; details in CHANGELOG.md.

- Prospective memory: a fifth type for intentions ("next session, check X"; "when touching repo Y, remember Z") with a trigger condition (time or context), fired via the recall pack's pending-intentions section and turn-time recall, then marked complete or expired. Unlike the other types it has a terminal lifecycle.
- Valence on episodic rows: an `outcome` flag (success / failure / neutral) so "what went wrong last time we tried X" is answerable and consolidation can weight failures into procedural rules.
- Staleness on semantic rows: a `verify_by` date; recall flags facts past it as "verify before relying". A fact's age becomes visible instead of silent.
- Injection screen at capture: instruction-shaped memo content ("ignore previous...", imperatives aimed at the model) is flagged or refused, closing the poisoned-memo -> persistent-prompt-injection path. Pairs with the v0.2 secret filter.
- Purge by subject: delete everything about an entity or a session in one command, including graph edges. The GDPR-shaped forget.
- `why <id>`: explain a memory - captured when, from which session, promoted from what, recalled how often. The lineage columns already hold this; expose it.
- `export` / `import` for moving a store between machines (pushed from v0.2: trustworthiness before portability)
- Skill-based routing: a description-routed skill so the model saves and consults memory proactively, not only via /memory
- Associative links: `memory_links (src_memory, dst_memory, rel)` join table with a closed vocabulary (derives_from, supports, contradicts, follows) plus free `co_session` links derived automatically from co-capture (accumulate the data early, activate retrieval over it only when the eval harness can measure it). Curated links store decision chains (conclusion linked to premises), NOT raw chain-of-thought transcripts. Weighted links reinforce on co-retrieval and decay when unreinforced (Hebbian), with hub detection, so activation discriminates instead of connecting everything to everything. Recall principle: associative retrieval returns a RANKED CANDIDATE SET, never top-1; close-scored candidates are handed to the model to disambiguate, because context-holding is where the model earns its keep.
- Entity mentions: `memory_entities (memory_id, entity_id)` join table bridging the memory store and the graph. Makes "everything we know about X" one query, and is the implementation substrate for purge-by-subject, `why`, and graph-aware recall.
- [SHIPPED 2026-08-25] Eval harness: `eval --questions <file>` runs a labelled set against retrieval read-only, reporting hit rate + MRR (issue #19). Build the real ~20-question set from dogfooding misses; NFR-11 now enforceable.
- Memory linter: one health pass reporting contradictions and near-duplicates (a confirmed conflict recorded as a `contradicts` link in memory_links), orphaned promotion lineage, stale unverified facts, and procedural rules whose evidence has decayed away, which take a confidence penalty rather than persisting unchallenged
- Subagent spawn-time recall injection (pairs with v0.2 SubagentStop capture)
- Automatic entity extraction during consolidation (model-driven, engine-verified upserts; pulled from v0.2 pending dogfooding evidence)
- Optional embedding layer behind the existing `search` interface (local model first, fail-soft), hybrid rank with FTS
- Graph queries in recall: pull the entity neighbourhood relevant to the current task into the pack
- Recall budget control: token-aware pack compilation with per-section caps
- Plugin marketplace listing
- Onboarding importer: seed a store from an existing CLAUDE.md or notes file
- CONTRIBUTING.md once outside interest appears

## Later

- [SHIPPED 2026-08-25, v0.4] Handoff (working) memory: a session-continuity row ("state of play: migration half done, tests 3 and 7 failing") written by a dying session, consumed by the next, then discarded, never consolidated. Distinct lifecycle: one writer, one reader.
- [SHIPPED 2026-08-25, v0.4] Recall utility feedback: learn whether an injected memory actually helped, so ranking improves instead of counting injections. Design: a `recall_trace` log per retrieval (candidate set with scores, what was chosen, by whom), `was_correct` backfilled from feedback, and rejections applying a weight PENALTY to the links that surfaced them, not merely absent reinforcement, because reinforce-only systems drift toward plausible nonsense. The deepest open problem in the design; the eval harness is its measurement substrate.
- Multi-user PostgreSQL backend (far): a team-shared application-memory tier (multi-tenant with row-level security, single write path with supersession, promotion from local stores as the only ingress) behind the same engine interface. The local SQLite store stays the default and the system of origin; this tier exists for teams, not to replace local-first. Bitemporality deliberately excluded unless "what did we believe in March" becomes a real query.
- Case-based memory: a worked-examples library ("how we solved X"), promotable from episodic clusters
- Multi-store federation (query several stores by identifier without merging)
- A small HTML graph viewer for the entity store
- Consolidation quality metrics: which promoted memories actually get recalled and used (feeds utility feedback)

Deliberately NOT planned: a user-preference type (that is semantic/procedural with a scope), spatial/codebase memory (the entity graph covers it with etype file/system), and working-memory-as-scratchpad (the context window already is one).
