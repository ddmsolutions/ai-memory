# Use cases

The behavioural model of ai-memory. Every use case decomposes into numbered requirements in `requirements.md`; the traceability matrix there maps both onto plan sections and versions. Status: LIVE (v0.1, on main), PLANNED-0.2 (in `plan.md` build order), PLANNED-0.3 (roadmap).

## Actors

| Actor | Description |
|-------|-------------|
| User | The developer who owns the machine and the memory store |
| Claude | The model inside a Claude Code session (main loop) |
| Subagent | A model instance spawned by Claude via the Task tool |
| Stop hook | `hooks/capture.py`, fired by Claude Code at the end of each assistant turn |
| SessionStart hook | `hooks/session_start.py`, fired at session start, resume, and clear |
| CLI | `python -m ai_memory`, the deterministic engine surface |

## Setup

### UC-01 Initialise a store - LIVE
- **Actor:** User. **Trigger:** `init` (or first connect from any surface).
- **Main flow:** resolve DB path (env override, else default), create parent directory, apply schema (tables, FTS, triggers, indexes, views), confirm path.
- **Errors:** unwritable path fails with the OS error; re-running is a no-op (idempotent DDL).
- **Postcondition:** empty store exists and every later surface can connect.
- **Requirements:** FR-S1, FR-S2, FR-S3, NFR-5.

### UC-02 Install into Claude Code - LIVE
- **Actor:** User. **Trigger:** plugin install or manual hook wiring per `install.md`.
- **Main flow:** hooks registered (SessionStart, Stop), `/memory` command available, memo convention added to the user's CLAUDE.md.
- **Postcondition:** capture and recall run without further user action.
- **Requirements:** FR-H1, FR-H2, NFR-1.

## Capture

### UC-03 Capture memos at turn end - LIVE
- **Actor:** Stop hook (system), on behalf of Claude.
- **Trigger:** Stop event with `session_id` and `transcript_path`.
- **Main flow:** parse transcript JSONL, collect fenced ```memo blocks from assistant messages, insert each as an episodic row tagged `origin_session`, skipping content already captured for that session.
- **Errors:** missing transcript, malformed JSON lines, DB failure: swallow and exit 0. A memo containing no text is ignored.
- **Postcondition:** every distinct memo of the session exists exactly once as episodic.
- **Requirements:** FR-C1, FR-C2, FR-C3, FR-C4, NFR-1, NFR-2.

### UC-04 Capture subagent memos - LIVE (v0.2)
- As UC-03, but triggered by SubagentStop so memos written by background agents are not lost. Same dedup, same fail-soft.
- **Requirements:** FR-C5, NFR-1.

### UC-05 Refuse secrets at capture - LIVE (v0.2)
- **Actor:** Stop hook. **Trigger:** a memo containing credential-shaped content (key prefixes, bearer tokens, PEM blocks, high-entropy strings).
- **Main flow:** matched spans are redacted with a placeholder before insert; the rest of the memo survives.
- **Postcondition:** no plaintext secret is ever persisted.
- **Requirements:** FR-C6, FR-C7, NFR-3.

### UC-06 Remember directly - LIVE
- **Actor:** User or Claude via CLI. **Trigger:** `remember "<content>" --type <t> [--scope --session --confidence --pin --supersedes]`.
- **Main flow:** validate type, insert row, optionally mark an older row superseded, report new id.
- **Errors:** unknown type rejected; supersedes id must exist (FK).
- **Requirements:** FR-S4, FR-S5, FR-K1.

## Recall

### UC-07 Session-start recall - LIVE
- **Actor:** SessionStart hook. **Trigger:** session start, resume, or clear.
- **Main flow:** compile the recall pack (pinned, then procedural by confidence, then semantic), bump recall counters, emit as `additionalContext`.
- **Errors:** any failure emits nothing and exits 0; an empty store emits nothing.
- **Postcondition:** the session begins with the pack in context.
- **Requirements:** FR-R1, FR-R2, FR-R3, FR-R4, NFR-1.

### UC-08 Turn-time recall - LIVE (v0.2)
- **Actor:** UserPromptSubmit hook. **Trigger:** each user prompt.
- **Main flow:** FTS-match the prompt against active memories in scope, inject the top N matches (config-capped) not already in the session-start pack.
- **Errors:** fail-soft; zero matches injects nothing.
- **Requirements:** FR-R5, FR-R6, NFR-1, NFR-4.

### UC-09 Search on demand - LIVE
- **Actor:** User or Claude. **Trigger:** `search "<query>" [--type --scope --limit]`.
- **Main flow:** FTS query over active memories (superseded excluded unless explicitly included), ranked by bm25.
- **Requirements:** FR-R7, FR-R8.

### UC-10 Project-scoped recall - LIVE (v0.2)
- **Actor:** hooks. **Trigger:** any recall while working in a mapped project directory.
- **Main flow:** resolve cwd to a project slug via config; pack and turn-time recall filter to that scope plus `global`.
- **Requirements:** FR-R9, FR-R10.

## Curation

### UC-11 Consolidate episodics - LIVE
- **Actor:** Claude (judgement) + CLI (bookkeeping). **Trigger:** `/memory consolidate`.
- **Main flow:** list the backlog; for each row Claude distils and either promotes (`promote <id> --type <t> --content "<distilled>"`, recording `promoted_from` lineage and marking the source consolidated) or leaves it to decay.
- **Errors:** promoting to a non-durable type or a missing id is rejected.
- **Postcondition:** durable stores grow only through distillation or direct remember; backlog shrinks.
- **Requirements:** FR-K2, FR-K3, FR-K4.

### UC-12 Correct a fact - LIVE
- **Actor:** User or Claude. **Trigger:** `remember "<new truth>" --type semantic --supersedes <old id>`.
- **Main flow:** new row inserted, old row marked superseded; every read surface (search, recall, views) sees only the new row from that moment.
- **Requirements:** FR-K1, FR-S5, FR-R8.

### UC-13 Pin and forget - LIVE
- **Actor:** User. **Trigger:** `pin <id> [--off]` / `forget <id>`.
- **Main flow:** pin exempts a row from decay and leads every pack; forget hard-deletes the row (FTS index cleaned by trigger, evidence FKs null out).
- **Requirements:** FR-K5, FR-K6, NFR-3.

### UC-14 Decay old episodics - LIVE (v0.2)
- **Actor:** User or scheduled task. **Trigger:** `decay [--dry-run]`.
- **Main flow:** delete episodic rows older than the configured window that were never promoted, never recalled, and are not pinned; report what went (or would go).
- **Requirements:** FR-K7, FR-K8, NFR-4.

## Entity graph

### UC-15 Maintain the graph - LIVE
- **Actor:** User or Claude. **Trigger:** `entity add --name <n> --etype <t>` / `entity link <src> <dst> --rel <r>` / `entity show <name>`.
- **Main flow:** upsert nodes (case-insensitive lookup, summary kept current), upsert typed weighted edges (auto-creating endpoints), describe a node with its relationships in both directions.
- **Requirements:** FR-G1, FR-G2, FR-G3, FR-G4.

## Operations

### UC-16 Check store health - LIVE
- **Actor:** User. **Trigger:** `status`.
- **Main flow:** report counts per type, pinned, backlog size, entities, edges as JSON.
- **Requirements:** FR-O1.

### UC-17 Configure behaviour - LIVE (v0.2)
- **Actor:** User. **Trigger:** editing the config file.
- **Main flow:** engine reads config once per invocation, missing file or bad values fall back to documented defaults, silently.
- **Requirements:** FR-O2, FR-O3, NFR-1.

### UC-18 Survive a schema change - LIVE (v0.2)
- **Actor:** CLI/hooks (system). **Trigger:** first connect after upgrading the code.
- **Main flow:** compare `PRAGMA user_version` with the code's version, apply ordered migrations, stamp the new version.
- **Errors:** a failed migration leaves the store untouched (transactional) and reports; hooks fail soft, CLI fails loud.
- **Requirements:** FR-O4, FR-O5, NFR-5.

## v0.3 use cases (outline; flows specified when each is picked up)

- **UC-19 Set and trigger an intention** - PLANNED-0.3. Create a prospective memory with a time or context trigger; it surfaces until done or expired. FR-P1..P3.
- **UC-20 Record outcome valence** - LIVE (2026-08-25). Mark an episode success or failure; consolidation weights failures into rules. FR-A1, FR-A3.
- **UC-21 Flag a stale fact** - LIVE (2026-08-25). A semantic row past `verify_by` recalls with a verify warning. FR-A2.
- **UC-22 Screen instruction-shaped memos** - LIVE (2026-08-25). Capture flags or refuses content that reads as instructions to the model. FR-C8.
- **UC-23 Chain memories associatively** - PLANNED-0.3. Typed weighted links (curated + auto co_session), Hebbian reinforce/decay, candidate-set retrieval. FR-L1..L5, NFR-12.
- **UC-24 Bridge memories to entities** - PLANNED-0.3. Mentions join makes "everything about X" one query. FR-N1.
- **UC-25 Purge a subject** - PLANNED-0.3. One confirmed command erases an entity or session everywhere. FR-N2.
- **UC-26 Graph-aware recall** - PLANNED-0.3. The task's entity neighbourhood joins the pack, budget-capped. FR-N3.
- **UC-27 Explain a memory** - PLANNED-0.3. `why <id>` tells the row's full story. FR-M1.
- **UC-28 Measure recall quality** - LIVE (shipped early, 2026-08-25). Labelled eval set via `eval` CLI, hit rate + MRR per run, read-only. FR-M2, NFR-11.
- **UC-29 Lint the store** - PLANNED-0.3. One health pass; dead evidence takes a confidence penalty. FR-M3.
- **UC-30 Export / import** - PLANNED-0.3. Lossless JSON round trip with dedup. FR-X1, FR-X2.
- **UC-31 Proactive skill routing** - PLANNED-0.3. The model saves and consults memory without being told. FR-D1.
- **UC-32 Subagent spawn injection** - PLANNED-0.3. A spawned agent starts with a scoped pack. FR-D2.
- **UC-33 Embedding-backed search** - PLANNED-0.3. Optional, local, fail-soft, hybrid with FTS. FR-V1.

Later (design sketches in `roadmap.md`): UC-34 recall utility feedback (FR-M4), UC-35 handoff memory, UC-36 team tier (multi-user PostgreSQL).
