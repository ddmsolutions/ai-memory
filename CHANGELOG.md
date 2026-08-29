# Changelog

## v0.8.6 - 2026-08-29

### Changed
- Viewer: the relation filter is now a tickbox list matching the entity-type
  filter - multiple relation types combine, per-rel counts shown, all on by
  default - replacing the single-select dropdown.

## v0.8.5 - 2026-08-29

### Added
- Typed entity mentions (#80): `entities: Alice (person), Acme (company)` -
  an optional parenthesised type per name, parsed deterministically
  (multi-word parentheticals stay part of the name). A typed mention of an
  existing 'thing' entity upgrades it in place (thing -> specific only,
  never the reverse; UNIQUE twin collisions skip). `entity retype` repairs
  existing rows (merge suggested on collision); lint nudges once, with
  samples, when 5+ active 'thing' entities accumulate. The remembering
  skill teaches the typed syntax.

## v0.8.4 - 2026-08-29

### Changed
- The `everything` preset now keeps a connection floor of 1 rather than 0. A node with nothing attached to it carries no information in a force layout, it only fills space, so no preset strands nodes any more

## v0.8.3 - 2026-08-29

### Fixed
- The viewer's connection floor stranded the very nodes it exists to remove. Dropping a node lowers its neighbours' degree, so a single pass left nodes that only met the floor through neighbours which were themselves pruned. Pruning now runs to a fixed point (capped at 25 passes). On the live store the professional preset went from 532 nodes with 3 stranded to 508 with none; it needs 5 passes to settle

## v0.8.2 - 2026-08-29

### Added
- Viewer clutter controls for large graphs. An entity-type filter (checkbox per etype, with counts), a `memory nodes` master toggle, `Connections` min/max bounds that count degree over the links surviving the other filters, and four one-click presets: professional, personal, infrastructure, everything. The viewer now opens on the professional preset rather than the full graph
- The counts line reports `showing X of Y nodes` instead of the store total, so it is clear how much the filters are removing

## v0.8.1 - 2026-08-29

### Fixed
- Migration 15 could not run against a store that already had edges (#71 provenance columns). SQLite runs a full-table constraint scan for an `ALTER TABLE ADD COLUMN` carrying a CHECK; the `confidence` column added earlier in the same migration has no stored value on existing rows, so that scan read it as NULL and the upgrade died with `NOT NULL constraint failed`, leaving the store stranded at 14. The CHECK-carrying columns are now added first. Same resulting schema. Every existing graph was affected; empty and fresh stores never saw it
- Migration tests all started from an empty store, which is why a rows-only failure shipped. Added a regression test that applies migration 15 verbatim to a populated `edges` table
- Migration 21 materialises the defaults that `ALTER TABLE ADD COLUMN` never wrote. SQLite does not rewrite existing rows for an added column: it returns the default on read but stores nothing, so those records stay short and `PRAGMA integrity_check` reports `NULL value in <table>.<column>` for every NOT NULL column added that way. Affected `edges`, `memory_entities`, `entities` and `memories`. Reads were always correct, which is why it went unnoticed

## v0.8.0 - 2026-08-29

The research-hardening release: twelve issues from the 2026-08-29 agent-memory
field research and the workspace-engine schema comparison, in one branch.
Migrations 12-19; every store upgrades in place (snapshot taken first).

### Added
- Origin trust levels (#64): `memories.origin` ('owner'|'agent'|'external')
  bound at write time; promotion inherits (Biba non-elevation - a rewrite
  cannot launder external content into trusted memory); recall ranks by
  origin weight and marks external rows; `trust <id> --origin X` is the only
  elevation path (human-invoked); memo syntax `origin: external`
  (downgrade-only - a memo claiming owner is ignored); lint suggests
  elevation for independently corroborated external rows
- Safety-triggered forgetting (#65): `quarantine <id>` cascades over the
  contamination set (promoted_from children + derives_from links,
  transitive) and suspends machine-sourced edges whose whole evidence set is
  contaminated; `policy sweep <regex>` retro-sweeps active rows a hostile
  pattern matches; nothing deleted, everything reviewable via policy
  release/hostile
- LongMemEval retrieval adapter (#66): `bench/longmemeval.py` runs the
  benchmark's haystacks through the REAL insert funnel and production hybrid
  search, reporting evidence recall@k + MRR per question type with the
  overfitting caveat embedded; dataset downloaded separately
- Dedupe-first consolidation (#67): promote() refuses non-episodic sources
  (no summary-of-summary chains; lint catches legacy ones); `summarise`
  consolidates a cluster with every original linked derives_from and the
  least-trusted origin carried; autoconsolidate attaches an episode as
  evidence of an existing identical durable row instead of forking a rewrite
- Valid-time windows on entity edges (#68): t_valid/t_invalid with
  UNIQUE(src,dst,rel,t_valid) - the same relationship can recur;
  supersession closes windows, never deletes; `entity close`, `entity link
  --from/--replaces`, `entity show --history`; valid-time only,
  bitemporality stays excluded
- Entity aliases + merge (#69): entity_aliases (normalised lookup keys) +
  merge tombstones; resolution order exact canonical > alias >
  suffix-stripped SUGGESTION; ambiguity returns the candidate set
  interactively and links NOTHING headlessly (lint: ambiguous_alias);
  `entity alias add/list/remove`, `entity resolve`, `entity merge`;
  purge-by-alias reaches the canonical entity
- Graph type registry (#70): governed ontology (is_a hierarchy, abstract,
  symmetric, endpoint constraints, retired) seeded with a generic core;
  permissive by default with lint findings, `graph_strict` refuses at write;
  `entity type list/add/retire`
- Edge provenance (#71): edges carry source channel
  ('manual'|'consolidate'|'extract'), per-channel confidence, status, and an
  edge_sources evidence set; deterministic corroboration reinforcement;
  `entity why src dst --rel`; lint: edge_evidence_gone
- Mention roles (#72): memory_entities.role ('subject'|'mentioned') +
  confidence; first entity on an entities: line is the subject; about-X
  ranks subject rows first; upgrade-only, promote inherits
- External identity refs (#73): entity_refs (kind,value) unique store-wide -
  what an entity IS vs what it is CALLED (#69); conflicting ref errors with
  a merge suggestion; `entity ref add/list`, `entity resolve kind=value`
- Capture idempotence + ANN (#74): memories.line_hash (partial unique) makes
  capture/import re-runnable (session-bound: same memo from another session
  is corroboration, not a duplicate); optional sqlite-vec vec0 index behind
  semantic search, JSON-scan fail-soft; `[vec]` extra
- MCP server (#61): `ai_memory_mcp/` package (`[mcp]` extra, `python -m
  ai_memory_mcp`), 19 tools over the same funnel/quarantine/scope
  invariants; destructive + trust-bearing surfaces (forget, pin, trust,
  purge, import, tuning) deliberately absent; registration is opt-in via
  `.claude.json` (documented) so the base plugin stays dependency-free;
  separate CI job

### Fixed (cold-review pass on the release branch)
- MCP `why` no longer echoes quarantined content to a model caller - a
  labelled stub replaces it; the human CLI keeps full access
- Viewer: edge provenance key renamed `channel` - it collided with the D3
  `source` endpoint key, breaking every entity edge in the payload
- Re-linking a closed edge opens a NEW window (default valid_from) or fails
  loud (explicit valid_from) instead of a silent no-op
- `policy release` re-activates suspended edges whose evidence survives -
  suspension is no longer a one-way door
- `entity merge` folds colliding edges' evidence sets and subject roles into
  the survivor instead of cascade-deleting them
- `purge` follows merge-tombstone chains to a fixpoint in both directions
  and clears intra-set merged_into FKs before deleting
- `reify` closes the original edge (kept as history) instead of deleting it
- `summarise` rejects clusters spanning two named scopes (global mixes with
  one named scope); v_edges_named exposes the provenance columns
  (migration 20); sqlite-vec extension loading wrapped in try/finally;
  suggestion fuzzy-scan skipped on the capture hot path

### Changed
- Migration engine accepts callable entries for guarded table rebuilds
- Export/import round-trips every new table and sanitises invented trust
  (origins and edge sources)

## v0.6.10 - 2026-08-28

### Fixed
- `promote` now copies the parent's entity mentions onto the distilled row. Only memo capture writes mentions and distillation output carries no `entities:` line, so every promoted semantic or procedural fact silently left the entity graph. Measured on the live store before the fix: 296 of 395 memories had no entity link, 292 of them semantic or procedural. Inheritance copies, it never moves, so the parent keeps its own mentions

## v0.6.9 - 2026-08-26

### Added
- Colour-by modes (#63): memory type (default), entity kind (etype palette, memories muted), scope, or a stable distinct colour per node (golden-angle hue hashing); live legend; quarantine red and cluster grey always override


## v0.6.8 - 2026-08-26

### Added
- Family view (#62): generational LHS-to-RHS layout - generation levels solved by constraint propagation (parent_of +1, grandparent_of +2, married_to/sibling_of equal), eldest generation in column 0, barycenter ordering per generation, married pairs placed adjacent on equal footing; filters to the family subgraph and composes with labels, arrows, and the detail panel


## v0.6.7 - 2026-08-26

### Fixed
- Embeddings are now consulted on every retrieval surface (#59): search, pack task-matching, turn recall, and eval blend cosine with bm25 via Reciprocal Rank Fusion (tunable `hybrid_semantic_weight`, in the tune grid); model task prefixes (`embed_query_prefix`/`embed_doc_prefix`) applied at query and index time with `embed-index --force` for re-embedding; fail-soft to pure bm25 preserved. Live eval: hit_rate 0.625 to 0.8125, MRR 0.45 to 0.575, both named semantic-gap misses flipped, zero avoid regressions (one avoid improvement)
- Scope relevance in search (#60): without an explicit `--scope`, rows outside the preferred scope (CLI derives it from cwd) and global are down-weighted by `foreign_scope_penalty`, never hard-filtered; explicit scope behaviour unchanged; global rows never penalised
- SQLite connections gain a 15s busy timeout (embed-index hit "database is locked" against live hook writers)


## v0.6.6 - 2026-08-26

### Added
- Directional arrowheads on every link (edges src to dst, mentions memory to entity, associative links, supersession old to new) (#58)
- Flow-from/flow-to toggles: ego traversal and the flow layout walk outgoing only, incoming only, both, or neither, so who-X-connects-to and who-connects-to-X are separable questions (#58)


## v0.6.5 - 2026-08-26

### Added
- Reified role nodes (#57): `entity role <holder> <title> [--at <org>]` models roles as first-class entities (holder -holds-> role -at-> org); `entity reify <src> <rel> <dst>` converts a mis-modelled edge into a per-instance role node; viewer renders roles in their own colour with a filter checkbox; skill documents the convention (roles are nodes, plain verbs stay edges)


## v0.6.4 - 2026-08-26

### Fixed
- Flow view lays out strict primary/secondary/tertiary columns and minimises edge crossings via deterministic Sugiyama barycenter sweeps (three alternating passes) instead of alphabetical column order (#56)


## v0.6.3 - 2026-08-26

### Added (viewer P3, #55)
- Collapsible clusters: connected components listed with representative labels; collapse any into an aggregated meta-node (external links merged with counts), click to expand, collapse-all/expand-all
- Flow view: with a node selected, a toggle lays the ego neighbourhood out in columns, primary node left, each hop one column right; force layout restored on exit
- Label toggles: short node labels and humanised edge labels (married_to renders as married to), canvas-drawn and scale-aware so zoomed-out views stay readable


## v0.6.2 - 2026-08-26

### Fixed
- CLI usable from any directory: `pip install -e .` gives an `ai-memory` entry point (install docs updated; on Windows system Pythons use `--user`, the Scripts dir needs no admin); sessions no longer Set-Location into the repo (#54)
- Shell-quoting immunity: `remember`, `intend add`, and `handoff add` read content from stdin when the positional is omitted (or `-`), so quotes, apostrophes and leading hyphens in content can no longer produce argparse usage errors; empty stdin fails loud (#54)


## v0.6.1 - 2026-08-26

Entity coverage: the graph now populates itself from what memos already declare.

### Added
- Deterministic entity mentions: capture parses the memo `entities:` line into screened mentions (names length-capped and instruction-screened, graph lines are an injection surface); quarantined memos create nothing; `entity backfill` sweeps existing memories idempotently (#52)
- Engine support for model-driven extraction: autoconsolidate accepts an entity_extractor callable, validates every proposal, and upserts mentions only for non-quarantined promotions (#53)


## v0.6.0 - 2026-08-26

The self-learning release, plus the visual graph. Third cold review before tagging: one blocker (distiller output unscreened) and seven findings fixed pre-release.

### Added (self-learning, issues #41-#45)
- `tune`: grid-search the ranking knobs against the eval set; adopt only non-degrading configs, `.prev` revert; grid contains only knobs an eval surface actually measures (FR-SL6)
- Eval growth: not-useful traces become avoid-questions, the re-explanation detector (new memo near-duplicating an old memory = recall failure) becomes expect-questions + a `re_explained` lint finding; eval gains pack-surface and avoid scoring
- `autoconsolidate`: gated hygiene (VACUUM INTO snapshot, pinned-preferring dedupe, stale triage, decay) + optional distiller whose output passes the deterministic instruction screen regardless of the model's certainty claim; regression on hit rate OR mrr restores the snapshot (WAL sidecars cleared)
- Policy learning (schema v11): quarantine outcome labels (`policy release|hostile`), corpus validation compiled exactly as production screens compile, human-approved `policy adopt` into config-extendable instruction patterns
- `observe`: health surfaces to issue drafts (draft-for-review default; direct posting needs config observer_post + observer_repo + the flag; gh failures fall back to drafts without duplication)

### Added (visual graph, issues #46-#47)
- `graph`: single self-contained offline HTML over both layers joined by mentions; vendored force-graph, zero network; ego focus with per-hop expansion; filters, search-and-centre, why detail panel; encodings: size by recall, thickness by time-decayed weight, dashed near prune floor, red halo past verify_by
- `graph --serve`: localhost-only live mode; supersession-chain overlay; decay time scrubber sharing the engine formula (parity-tested); `--scope` export filter + embedded-scopes warning for sharing safety
- All untrusted content escaped; embedded JSON <-escaped so content cannot break out of the script block


## v0.5.1 - 2026-08-26

Premortem hardening: the four most probable deaths defused.

### Added
- Numeric exit criteria pre-committed in plan.md (value, precision, validity, cost bands; decision date 2026-10-07); renegotiating them is the named failure mode (#48)
- Scorecard telemetry: days_since_last_capture, injected token estimate, recall latency (read-only probe); lint flags capture silence over 7 days, fail-soft can no longer hide a dead pipeline invisibly (#48)
- Funnel-coverage architectural test: any new INSERT path into an injectable table outside the approved redact+screen funnels fails CI (#49)
- Pre-migration auto-snapshot (`<db>.v<from>.bak`) + `backup` command (timestamped JSON export) (#50)
- `exclude_paths`: hooks no-op entirely under configured prefixes; the workspace boundary decision is enforceable config, not convention (#51)

## v0.5.0 - 2026-08-26

The proof release: measure whether Claude Code is actually better with this memory.

### Added
- A/B benchmark (`bench/`): behaviour-tagged probe battery (facts, rules, corrections, intentions, handoffs, controls) run headless against a seeded and an empty store under identical hooks; per-probe fresh store copies; per-behaviour accuracy deltas + token overhead; CI-testable via injectable runner (#37)
- `scorecard [--days]`: read-only weekly dogfood aggregate - injections, trace precision per surface, growth, backlog, quarantine, open handoffs, due intentions (#38)

### Fixed
- Export now carries handoffs (open handoffs previously lost on machine migration); deliberate exclusions (injection_log, recall_trace) documented (#39)

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
