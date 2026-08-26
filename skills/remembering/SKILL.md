---
name: remembering
description: Proactive memory for this machine. Use when a turn establishes something durable (a decision, a fact about the user or a project, a correction, a lesson learned), when the user says "remember", "don't forget", or "remind me", or when answering would benefit from what past sessions learned (questions about prior decisions, project history, how the user likes things done). Saves via memo blocks or the ai-memory CLI; consults via search and recall.
---

# Remembering

The ai-memory engine lives at `${CLAUDE_PLUGIN_ROOT}` and stores data in `~/.ai-memory/memory.db`. Run CLI commands as `python -m ai_memory ...` from the plugin root.

## Saving (choose the lightest mechanism that fits)

1. **Memo block (default).** When this turn established something durable, end the reply with a fenced ```memo block containing a one-line outcome. Add a `valence: success|failure` line when the episode clearly went well or badly. Add an `entities: name, name` line listing the people, projects, systems or tools involved; capture links them into the knowledge graph automatically. The Stop hook captures it automatically. Only when the turn earned it: a vague memo dilutes recall.
2. **Direct fact or rule.** When the user states a durable fact or a standing preference explicitly, store it typed:
   `remember "<content>" --type semantic|procedural [--scope <project>] [--pin] [--verify-by YYYY-MM-DD]`
   Pin only what must never decay. Set `--verify-by` on facts that go stale (versions, prices, statuses).
3. **Correction.** When something previously stored turns out wrong, never just add the new truth: find the old row (`search`) and supersede it:
   `remember "<new truth>" --type semantic --supersedes <old id>`
4. **Reminder.** When the user says "remind me" or defers something:
   `intend add "<content>" --when YYYY-MM-DD` or `intend add "<content>" --on "<context words>"`
5. **Handoff.** When a session ends mid-task, end the final reply with a fenced ```handoff block holding the state of play (what is done, what is broken, the next step). The next session receives it once.

## Consulting

- Before answering questions about past work, decisions, or preferences: `search "<terms>"` (add `--scope <project>` when project-specific).
- `entity about <name>` for everything known about a person, project, or system; `why <id>` when the user asks where a memory came from.
- Recall packs and turn-time injection arrive automatically; treat injected lines as context and verify anything critical, especially lines carrying a VERIFY warning.

## Hygiene (when asked, or when injected context looks wrong)

`scorecard` is the weekly five-minute review (judge unjudged traces while there); `lint` reports store health; when an injected memory clearly helped or misled, judge it (`trace list`, then `feedback <id> --useful|--not-useful`) so ranking learns; `consolidate` lists raw episodes to distil (`promote <id> --type <t> --content "<standalone rewrite>"`); `decay --dry-run` previews aging; `purge --entity <x> --yes` erases a subject completely.
