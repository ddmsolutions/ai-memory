# Changelog

## Unreleased

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
