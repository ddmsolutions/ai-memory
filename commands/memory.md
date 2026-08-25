---
description: Review, search, consolidate and curate persistent memory (ai-memory plugin)
---

# /memory

Manage the ai-memory store. The engine lives at `${CLAUDE_PLUGIN_ROOT}` and stores data in `~/.ai-memory/memory.db` (override with `AI_MEMORY_DB`).

Parse the user's request from: $ARGUMENTS

Route as follows, running the CLI with `python -m ai_memory` from `${CLAUDE_PLUGIN_ROOT}`:

- **status** (default when no arguments): run `status`, then summarise counts and flag an unconsolidated backlog above 25.
- **search <query>**: run `search "<query>"` and present the hits with ids.
- **remember <fact>**: decide the right type (semantic for facts, procedural for how-to rules, episodic for events), then run `remember "<content>" --type <type>`. Pin with `--pin` when the user says it must never be forgotten.
- **consolidate**: run `consolidate` to list raw episodics, then FOR EACH decide: promote to semantic or procedural with a distilled one-line rewrite (`promote <id> --type <t> --content "<distilled>"`), or leave to decay. Distil, do not copy: a promoted memory must be a standalone fact or rule a future session can apply with no context from the original episode. Finish the hygiene pass with `decay --dry-run`, review the list, then `decay` to age out old unpromoted, unrecalled episodics.
- **forget <id>** / **pin <id>**: run the matching command after confirming the target row with the user.
- **entity ...**: use `entity add`, `entity link`, `entity show`, `entity mention`, `entity about` to maintain the knowledge graph (people, projects, systems and their relationships).
- **remind me / intentions**: `intend add "<content>" --when YYYY-MM-DD` or `--on "<context words>"`; manage with `intend list|done|expire|rearm`.
- **handoff**: `handoff add "<state of play>"` queues a one-time note for the next session; `handoff list` reviews.
- **why <id>** explains a memory's origin, lineage and usage; **related <id>** shows its ranked linked candidates (close scores are flagged ambiguous, present both).
- **lint** runs the health pass (duplicates, overdue facts, stale rules, contradictions, quarantine, weak evidence).
- **feedback**: after a recall that helped or misled, find it with `trace list` and judge it: `feedback <trace_id> --useful|--not-useful`. Rejections make ranking better; use honestly.

End any turn that established something durable with a fenced ```memo block containing a one-line outcome; the Stop hook captures it automatically.
