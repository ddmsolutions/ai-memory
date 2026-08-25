# Changelog

## v0.4.0 - 2026-08-25

The learning release: recall that measures itself, and memory that crosses session boundaries cleanly.

### Added
- Recall utility feedback (schema v9): every session-ful injection leaves a trace (ids and scores only, never content); `trace list/show` + `feedback <id> --useful|--not-useful`; rejection cuts injected rows' confidence and co_session link weights; `v_recall_precision` per surface; traces purge at `trace_retention_days` (#34)
- Handoff memory (schema v10): fenced ```handoff blocks (or `handoff add`) carry state of play to the next session; injected once at real session start, then consumed; TTL purge; same redact + instruction-screen funnel as every injectable surface; structurally never consolidatable (#35)
- Documentation audit: data-architecture now covers all ten schema versions (intentions, links, embeddings, traces, handoffs were undocumented); README/architecture describe all six memory kinds and four hooks; command and skill route the full CLI

## v0.3.0 - 2026-08-25

Richer memory: a fifth memory type, associative structure, measurement, and hardened trust boundaries.

### Fixed (cold code review before release)
- Quarantine now enforced at the read layer (schema v8): v_active_memories excludes the quarantine scope, closing confirmed escapes via graph mention lines, memory links, and default search; review remains via lint (truncated, labelled untrusted)
- Intentions go through the same redaction + instruction screen as memories; instruction-shaped reminders are refused
- Export now carries memory_links, intentions, and embeddings (was silent data loss); import screens instruction-shaped rows into quarantine; seed screens lines
- Sessionless pack compiles (CLI preview, spawn injection) no longer fire intentions or bump recall counters
- Evidence-decay penalty now fires on forget of a promoted source (was unreachable); purge erases matching intentions; pack budget includes intention lines; entity graph matching is word-boundary; embedding hits survive the turn-recall threshold

- Distribution pack: marketplace manifest for `/plugin marketplace add ddmsolutions/ai-memory`, CONTRIBUTING.md, and `seed` onboarding importer (CLAUDE.md bullets to typed memories, deduped, redacted) (#33)

- Optional embedding layer (schema v7): Ollama-compatible local model, `embed-index` CLI, hybrid search with FTS results leading; disabled by default, any failure degrades silently to FTS-only, quarantined rows never embedded (#32)

- Subagent spawn injection: PreToolUse hook on Task appends a small scoped pack (compiled against the subagent's own prompt) via updatedInput; spawn_pack_limit 0 disables (#31)
- Proactive skill: description-routed skills/remembering routes save (memo/typed/correction/reminder) and consult (search/about/why) without the user typing /memory (#30)

- Memory linter: `lint` reports duplicates, overdue facts, stale rules, unresolved contradictions, quarantine backlog, weak evidence; decay now applies the evidence-decay confidence penalty to rules whose source episode ages out (#23)
- Graph-aware recall: task packs gain a budget-capped Known connections section from the entity neighbourhood (#27)

- Prospective memory (schema v5): `intend` with time or context triggers; due intentions lead the pack, context intentions fire at turn time, each fires once; done/expired/rearm lifecycle (#20)
- Associative links (schema v6): curated typed links + automatic co_session from co-capture, Hebbian reinforce on co-retrieval with time decay and pruning, `related <id>` returns a ranked candidate set with ambiguity flags, never silent top-1 (#24)

- Export / import: full-store JSON, verbatim rows, dedup on re-import, all internal references (lineage, corrections, edges, mentions) remapped; lossless round trip proven by test (#29)

- `why <id>`: a memory's full story - origin session, promotion lineage both directions, correction chain, valence, mentions, usage (#28)

- Mentions bridge (schema v4): `memory_entities` join table, `entity mention` / `entity about` CLI, `v_entity_memories` view (#25)
- Purge by subject: `purge --entity X | --session Y --yes` erases memories, mentions, edges and the entity; secure_delete + VACUUM guarantee no residual bytes (#26)

- Injection screen (FR-C8): instruction-shaped memos (override/hijack/concealment/system-prompt patterns) are quarantined under a reserved scope, excluded from every recall surface, reviewable via explicit search (#22)

- Valence + staleness (schema v3): `valence` on episodes (CLI flag + `valence:` memo line, surfaced in the consolidation listing) and `verify_by` on facts; overdue facts recall with a VERIFY warning (#21)

- Eval harness: `eval --questions <file> [--k N] [--out report.json]`, read-only retrieval scoring (hit rate, MRR) so tunable changes can prove non-degradation (NFR-11) (#19)

## v0.2.0 - 2026-08-25

The trustworthiness release: memory present when needed, store stays clean without manual effort, no memo lost regardless of where it was written, nothing sensitive persisted.

### Added
- CI: pytest matrix (Python 3.10/3.12, ubuntu/windows) on every push and PR (#2)
- Schema versioning: `PRAGMA user_version` + ordered transactional migration runner; failed migrations roll back completely, CLI fails loud, hooks fail soft (#3)
- Config file `~/.ai-memory/config.json`: all tunables in one place, per-key fail-soft defaults (#4)
- SubagentStop capture: subagent memos land under the same session dedup (#5)
- Secret redaction before any insert: builtin credential patterns, charset-aware entropy screen, config-extendable, per-pattern tests proving secrets never reach the DB file (#6)
- Turn-time recall: UserPromptSubmit hook injects prompt-relevant memories mid-session, stopword-filtered, config-capped, with session-level injection dedup (`injection_log`, migration 2) and a configurable bm25 relevance floor (#7)
- Scored pack ordering: confidence x recency decay x usage saturation (#7)
- Project scoping: working directory resolves to a memory scope via config map, longest prefix wins, unmapped stays global (#8)
- Decay: age out old, unpromoted, unrecalled, unpinned episodics with dry-run; recall reinforces confidence, capped at 1.0 (#9)
- Every recalled line carries its recorded date; WAL journal mode

### Fixed (cold code review before release)
- Decay can no longer delete a row that supersedes another, which would have resurrected corrected facts as current truth
- Redaction moved into `store.remember`, covering the CLI path, not just hooks
- Capture hook fully fail-soft on unreadable transcripts; dedups within a single transcript; resolves scope from cwd
- Migration runner re-checks the version under the write lock (concurrent hooks) and survives version-stripped stores
- Pack limit is a true total budget; pinned rows survive session resume
- FTS quote escaping; config rejects non-finite and negative numbers; injection_log gets 14-day retention

## v0.1.0 - 2026-08-25

- Four-store engine (episodic, semantic, procedural, entity graph) in one SQLite file, 3NF with documented exceptions, FTS5 search, supersession, pinning, promotion lineage
- Stop-hook memo capture, SessionStart recall pack, `/memory` command, plugin manifest
- CLI: init, remember, search, recall, consolidate, promote, pin, forget, entity add/link/show, status
- Indexes proven by query-plan tests; read views (`v_active_memories`, `v_consolidation_backlog`, `v_edges_named`)
