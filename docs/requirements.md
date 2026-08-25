# Requirements

Decomposed from `use-cases.md`. Every requirement is testable as written; MUST is binding, SHOULD is binding unless a recorded reason exists. IDs are stable: never renumber, retire with a strikethrough note. The traceability matrix at the end maps use cases to requirements to plan sections. A Claude Code session developing this project takes a plan section, pulls its requirement set from the matrix, and treats the acceptance criteria as the test list.

## Functional requirements

### Store (FR-S)

- **FR-S1** The engine MUST store all state in a single SQLite file resolved via `AI_MEMORY_DB`, else `~/.ai-memory/memory.db`, creating parent directories on demand.
- **FR-S2** Schema creation MUST be idempotent: connecting to an existing store re-applies DDL harmlessly (`IF NOT EXISTS` throughout).
- **FR-S3** Every connection MUST enable foreign-key enforcement.
- **FR-S4** A memory row MUST carry: type (episodic | semantic | procedural, CHECK-constrained), scope, content (plain text), origin_session, promoted_from (FK), confidence, pinned, consolidated, superseded_by (FK), recall_count, last_recalled_at, created_at.
- **FR-S5** Correction MUST be by supersession: inserting a replacement marks the old row `superseded_by`; rows are never rewritten in place.
- **FR-S6** Full-text search MUST be maintained automatically (FTS5 external-content table synced by insert/update/delete triggers); no caller ever writes to the index.
- **FR-S7** The read surface for current truth MUST be the `v_active_memories` view; the superseded-exclusion predicate exists in exactly one place.

### Capture (FR-C)

- **FR-C1** The Stop hook MUST extract every fenced ```memo block from assistant messages in the session transcript (string and block-array content forms).
- **FR-C2** Each extracted memo MUST be stored as an episodic row tagged with the session id as `origin_session`.
- **FR-C3** Capture MUST deduplicate within a session: identical memo content for the same origin_session is stored once, however many Stop events fire.
- **FR-C4** The dedup lookup MUST be index-backed (it runs on every assistant turn).
- **FR-C5** (v0.2) A SubagentStop hook MUST capture memos from subagent transcripts under the same dedup and fail-soft rules as FR-C1..C3.
- **FR-C6** (v0.2) Credential-shaped content (configurable pattern set covering at minimum: common API-key prefixes, bearer tokens, PEM blocks, high-entropy strings over a threshold) MUST be redacted with a visible placeholder before any insert, in every capture path.
- **FR-C7** (v0.2) Each redaction pattern MUST have its own test proving the secret never reaches the database file.
- **FR-C8** (v0.3 draft) Instruction-shaped memo content (imperatives aimed at the model, "ignore previous" patterns) MUST be flagged or refused at capture, closing the poisoned-memo persistent-injection path.

### Recall (FR-R)

- **FR-R1** The SessionStart hook MUST emit the compiled recall pack via `hookSpecificOutput.additionalContext`, and emit nothing when the pack is empty.
- **FR-R2** (amended 2026-08-25) Pack compilation MUST order: pinned (newest first), then procedural, then semantic, then task FTS matches when a task is given; deduplicated across sections; per-section caps derived from the limit. Within the procedural and semantic sections, ordering MUST use an eviction score of the shape confidence × recency decay × usage saturation (tunables from config). Shipped in v0.2.
- **FR-R3** Every row included in a pack MUST have recall_count incremented and last_recalled_at stamped, in the same transaction as compilation.
- **FR-R4** The pack MUST open with a marker identifying it as injected memory context to be verified if critical.
- **FR-R5** (v0.2) A UserPromptSubmit hook MUST inject the top task-relevant active memories matching the user's prompt, capped by config, excluding rows already injected this session.
- **FR-R6** (v0.2) Turn-time injection MUST be silent when nothing relevant matches; relevance threshold configurable.
- **FR-R7** `search` MUST rank by bm25 over the FTS index and support filters: type, scope (scope always includes `global`), limit.
- **FR-R8** No read surface (search, pack, views, turn-time) may return a superseded row unless the caller explicitly requests history.
- **FR-R9** (v0.2) The working directory MUST resolve to a project scope via config mapping; unmapped directories resolve to `global` behaviour unchanged.
- **FR-R10** (v0.2) Scoped recall MUST return the union of the resolved scope and `global`.
- **FR-R11** Every line emitted by any recall surface MUST carry its recorded date, so the model can discount stale facts (wrong-and-retrieved reads as authoritative; the date is the discount signal).

### Curation (FR-K)

- **FR-K1** `remember --supersedes <id>` MUST atomically insert the new row and mark the old one superseded.
- **FR-K2** `consolidate` MUST list active episodic rows with `consolidated = 0` (the `v_consolidation_backlog` view), oldest first.
- **FR-K3** `promote <id>` MUST create a semantic or procedural row carrying `promoted_from = <id>`, a confidence uplift capped at 1.0, and the source row marked consolidated; promoting to episodic or from a missing id MUST fail loudly.
- **FR-K4** Promotion lineage MUST be an enforced foreign key (an orphan `promoted_from` is impossible).
- **FR-K5** `pin` MUST place a row at the head of every pack and exempt it from decay; `pin --off` reverses it.
- **FR-K6** `forget <id>` MUST hard-delete the row, its FTS entry (via trigger), and null out evidence references.
- **FR-K7** (v0.2) `decay` MUST delete only episodic rows that are ALL of: older than the configured window, never promoted, recall_count = 0, not pinned; and MUST support `--dry-run` listing without deleting.
- **FR-K8** (v0.2) Recall of a row SHOULD reinforce it: repeated recall raises confidence by a small configured step, capped at 1.0.

### Entity graph (FR-G)

- **FR-G1** `entity add` MUST upsert on the (name, etype) candidate key, keeping the latest non-null summary.
- **FR-G2** Entity lookup MUST be case-insensitive and index-backed.
- **FR-G3** `entity link` MUST upsert a typed weighted edge on (src, dst, rel), auto-creating missing endpoint entities, optionally citing an evidence memory.
- **FR-G4** `entity show` MUST render the node with its relationships in both directions, ordered by weight; a human-readable joined view (`v_edges_named`) MUST exist for inspection.

### Operations (FR-O)

- **FR-O1** `status` MUST report machine-readable counts: per-type totals, pinned, consolidation backlog (active rows only), entities, edges.
- **FR-O2** (v0.2) All tunable values (decay window, pack limit and per-section caps, turn-time cap and threshold, scope map, reinforcement step) MUST live in one config file with documented defaults; no tunable is hardcoded.
- **FR-O3** (v0.2) A missing, unreadable, or partially invalid config MUST fall back to defaults per key, without failing.
- **FR-O4** (v0.2) The store MUST carry a schema version (`PRAGMA user_version`); on connect, pending ordered migrations run inside a transaction and stamp the new version; a failed migration rolls back completely.
- **FR-O5** (v0.2) Hooks encountering a migration failure MUST fail soft (no injection, exit 0); the CLI MUST fail loud with the migration error.

### Prospective memory (FR-P, v0.3 draft)

- **FR-P1** A fifth type `prospective` MUST carry a trigger condition (a time, or a context pattern) and a lifecycle: pending, fired, done or expired.
- **FR-P2** Pending intentions MUST surface in the recall pack; context-triggered intentions MUST fire via turn-time recall when their pattern matches.
- **FR-P3** A completed or expired intention MUST leave all future packs.

### Aging and valence (FR-A, v0.3 draft)

- **FR-A1** Episodic rows MAY carry an outcome valence (success, failure, neutral), settable in the memo syntax and via CLI.
- **FR-A2** Semantic rows MAY carry a `verify_by` date; any recall surface MUST mark rows past it as "verify before relying".
- **FR-A3** The consolidation listing MUST surface valence so failures can be weighted into procedural rules.

### Associative links (FR-L, v0.3 draft)

- **FR-L1** A `memory_links (src_memory, dst_memory, rel)` join table MUST hold typed links: derives_from, supports, contradicts, follows, co_session.
- **FR-L2** `co_session` links MUST be derived automatically from co-capture; no manual effort.
- **FR-L3** Link weights MUST reinforce on co-retrieval and decay when unreinforced, with a prune floor, so activation discriminates.
- **FR-L4** Associative retrieval MUST return a ranked candidate set, never a silent top-1; candidates within a configured margin are flagged ambiguous for the model to resolve.
- **FR-L5** A hub-detection view MUST flag nodes whose degree distorts activation.

### Entity mentions (FR-N, v0.3 draft)

- **FR-N1** A `memory_entities (memory_id, entity_id)` join table MUST bridge memories and the graph.
- **FR-N2** Purge-by-subject MUST delete, in one confirmed command, every memory, mention, and edge for a named entity or session, reporting what went.
- **FR-N3** Graph-aware recall MUST be able to pull the entity neighbourhood relevant to the task into the pack, budget-capped.

### Measurement and explainability (FR-M, v0.3 draft; FR-M4 Later)

- **FR-M1** `why <id>` MUST show a memory's full story: capture origin, promotion lineage, supersession chain, recall stats, links.
- **FR-M2** An eval harness MUST run a labelled question set against recall and store a per-run precision report; quality is measured, not vibes.
- **FR-M3** A linter MUST report contradictions, near-duplicates, orphaned lineage, stale unverified facts, and rules whose evidence decayed (applying a confidence penalty).
- **FR-M4** (shipped 2026-08-25) A `recall_trace` log MUST record candidate sets and injections (ids and scores only, never content) for session-ful recalls; `feedback` backfills correctness, and rejections apply confidence and link-weight penalties, not merely absent reinforcement.

- **FR-M5** (shipped 2026-08-26) An A/B benchmark MUST run a behaviour-tagged probe battery under identical hooks against a seeded store and an empty store, with per-probe fresh store copies (consume-once isolation), control probes validating the harness, and a report of per-behaviour accuracy, delta, and token overhead. The user's live store is never touched.
- **FR-M6** (shipped 2026-08-26) A read-only `scorecard` MUST aggregate the dogfooding window: injections, traces logged/judged, precision per surface, new memories, backlog, quarantine, open handoffs, due intentions.

### Handoff memory (FR-W, shipped 2026-08-25)

- **FR-W1** A handoff MUST be writable by fenced ```handoff transcript block or CLI, through the same redaction and instruction-screen funnel as every injectable surface.
- **FR-W2** An open handoff MUST inject once at the next real session start (sessionless previews show but never consume), then be marked consumed and excluded thereafter.
- **FR-W3** Consumed handoffs and unconsumed handoffs older than the configured TTL MUST purge at decay; handoffs are structurally never consolidatable.

### Portability, routing, search (FR-X / FR-D / FR-V, v0.3 draft)

- **FR-X1** `export` MUST emit the full store as JSON; **FR-X2** `import` MUST ingest it with dedup; a round trip is lossless.
- **FR-X3** An onboarding importer SHOULD seed a store from an existing CLAUDE.md or notes file.
- **FR-D1** A description-routed skill MUST let the model save and consult memory proactively, not only via /memory.
- **FR-D2** Subagent spawn MUST be able to inject a scoped recall pack into the spawned agent's context.
- **FR-V1** An optional embedding layer MAY back `search` (local model, fail-soft, hybrid rank with FTS); absence changes nothing.

### Self-learning (FR-SL, v0.6 draft; autonomy decisions pending, see epic)

- **FR-SL1** A `tune` command MUST grid-search configured tunables (decay windows, half-lives, thresholds, caps) against the eval harness and benchmark, adopting a candidate config only when no tracked metric degrades (NFR-11 by construction); every adopted config is versioned and revertible.
- **FR-SL2** The eval set MUST grow from real failures: a trace judged not-useful generates a labelled question, and a captured memo that near-duplicates an existing active memory MUST be detected as a recall failure, generating both an eval question and a lint finding.
- **FR-SL3** An autonomous consolidation session MUST be able to run headless (distil, resolve contradictions, triage verify_by) gated by: export snapshot before, eval regression check after, uncertain promotions quarantined, every write traceable via lineage.
- **FR-SL4** Policy learning (screen patterns, stopwords, promotion heuristics) MUST validate proposed changes against the historical corpus with zero regressions before adoption; initially human-approved.
- **FR-SL5** A self-maintenance session MUST be able to observe (scorecard, lint, traces) and raise tracker issues from findings; implementing those issues stays human-triggered until the benchmark holds a months-long baseline.
- **FR-SL6** Every learning loop MUST be judged against a metric it cannot modify, and MUST be individually disableable; a loop that does not move its metric is switched off (the exit condition applies per loop).

## Non-functional requirements

- **NFR-1 Fail-soft hooks.** No hook may ever block a session: every error path swallows and exits 0. Hard invariant, test-covered per hook.
- **NFR-2 Zero effort capture.** Capture requires nothing from the user beyond the memo convention; no manual save step.
- **NFR-3 Privacy.** All data stays on the local machine; no network calls anywhere in the engine; hard delete really deletes; store files are never committed (gitignored).
- **NFR-4 Bounded injection.** Total injected context (session-start pack plus turn-time) is capped by config; memory must never crowd out the actual work.
- **NFR-5 Durability.** No code change may strand or corrupt an existing store; schema changes ship with migrations (from v0.2 Section 2 onward).
- **NFR-6 Stdlib only.** The engine runs on Python 3.10+ standard library; pytest is the only dev dependency; optional future layers degrade gracefully when absent.
- **NFR-7 Determinism boundary.** The engine performs no semantic judgement: what to remember and how to distil is model work; bookkeeping, ranking arithmetic, dedup, and decay are engine work.
- **NFR-8 Performance.** Hot paths (capture dedup, entity lookup) are index-backed with `EXPLAIN QUERY PLAN` tests; recall pack compiles in under 100 ms at 10k rows.
- **NFR-9 Readability of the store.** Memory content is plain text a human can read with any SQLite client; no pickled or encoded blobs.
- **NFR-10 Test-covered behaviour.** Every FR lands with at least one test; the suite runs green before any merge to main (Definition of Done, `plan.md`).
- **NFR-11 Measured tuning.** Once the eval harness exists, no change to a ranking, decay, or threshold tunable ships without a precision run showing no degradation.
- **NFR-12 Candidate sets over silent picks.** Wherever retrieval is ambiguous, the system surfaces ranked alternatives for the model to resolve; it never silently collapses to top-1.

## Traceability matrix

| Use case | Requirements | Component | Plan section | Status |
|----------|--------------|-----------|--------------|--------|
| UC-01 Initialise | FR-S1..S3, NFR-5 | db.py | v0.1 | LIVE |
| UC-02 Install | FR-H (hooks wiring: see install.md), NFR-1 | hooks.json, install.md | v0.1 | LIVE |
| UC-03 Turn capture | FR-C1..C4, NFR-1, NFR-2 | hooks/capture.py | v0.1 (+index fix) | LIVE |
| UC-04 Subagent capture | FR-C5, NFR-1 | hooks | v0.2 S4 | LIVE |
| UC-05 Secret filter | FR-C6, FR-C7, NFR-3 | hooks + engine | v0.2 S5 | LIVE |
| UC-06 Remember | FR-S4, FR-S5, FR-K1 | store.py, CLI | v0.1 | LIVE |
| UC-07 Session-start recall | FR-R1..R4, FR-R11, NFR-1, NFR-4 | hooks/session_start.py, store.py | v0.1 | LIVE |
| UC-08 Turn-time recall | FR-R5, FR-R6, FR-R11, NFR-1, NFR-4 | new hook + store.py | v0.2 S6 | LIVE |
| UC-09 Search | FR-R7, FR-R8, FR-S6, FR-S7 | store.py, CLI | v0.1 | LIVE |
| UC-10 Project scoping | FR-R9, FR-R10 | hooks + config | v0.2 S7 | LIVE |
| UC-11 Consolidate | FR-K2..K4, NFR-7 | store.py, /memory command | v0.1 | LIVE |
| UC-12 Correct | FR-K1, FR-S5, FR-R8 | store.py | v0.1 | LIVE |
| UC-13 Pin / forget | FR-K5, FR-K6, NFR-3 | store.py, CLI | v0.1 | LIVE |
| UC-14 Decay | FR-K7, FR-K8, NFR-4 | store.py, CLI | v0.2 S8 | LIVE |
| UC-15 Entity graph | FR-G1..G4 | graph.py, CLI | v0.1 (+index fix) | LIVE |
| UC-16 Status | FR-O1 | store.py, CLI | v0.1 | LIVE |
| UC-17 Config | FR-O2, FR-O3, NFR-1 | new config module | v0.2 S3 | LIVE |
| UC-18 Migrations | FR-O4, FR-O5, NFR-5 | db.py | v0.2 S2 | LIVE |
| (infra) CI | NFR-10 | GitHub Actions | v0.2 S1 | LIVE |
| (release) | changelog, tag | docs | v0.2 S9 | LIVE |
| UC-34 Utility feedback | FR-M4, NFR-11 | store.py (trace/feedback), CLI | backlog #34 | LIVE |
| UC-35 Handoff memory | FR-W1..W3, NFR-1 | store.py, hooks/capture.py, CLI | backlog #35 | LIVE |
| UC-37 A/B benchmark | FR-M5, NFR-11 | bench/ | v0.5 #37 | LIVE |
| UC-38 Weekly scorecard | FR-M6, NFR-11 | store.py, CLI | v0.5 #38 | LIVE |

Cross-cutting NFRs (1, 3, 6, 7, 9, 10) apply to every section and are re-checked at each section's Definition of Done.

## How to develop from this pack

1. Take the next unstarted section from `plan.md`.
2. Pull its requirement set from the matrix; read the linked use cases for flows and error paths.
3. Branch (`feature/` or `fix/`), implement, and write the tests the acceptance wording implies (MUST = a test that fails without the change).
4. Update the affected docs in the same commit (`data-architecture.md` for DDL, this file and `use-cases.md` if behaviour was re-scoped, `roadmap.md` if scope moved).
5. Green suite, merge to main, push, mark the section complete in `plan.md`.
