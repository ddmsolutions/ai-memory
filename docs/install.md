# Install

Requires Python 3.10+ (sqlite3 with FTS5, which every standard CPython build includes).

## 1. Get the code and initialise the store

```bash
git clone https://github.com/ddmsolutions/ai-memory
cd ai-memory
python -m ai_memory init
```

The database lives at `~/.ai-memory/memory.db`. Point `AI_MEMORY_DB` at another path to relocate it (for example, one store per machine profile or per project).

## 2. Wire it into Claude Code

### As a plugin

```
/plugin marketplace add ddmsolutions/ai-memory
/plugin install ai-memory
```

Or add the repo as a local-path plugin per the Claude Code plugin docs. The plugin ships:

- `hooks/hooks.json`: SessionStart recall injection + Stop capture
- `commands/memory.md`: the `/memory` command (status, search, remember, consolidate, forget, pin, entity)

### Manual hook wiring (no plugin)

Add to `.claude/settings.json` (project) or `~/.claude/settings.json` (user), replacing `<path>` with the clone location:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "python \"<path>/hooks/session_start.py\"" }] }
    ],
    "Stop": [
      { "hooks": [{ "type": "command", "command": "python \"<path>/hooks/capture.py\"" }] }
    ]
  }
}
```

## 3. Configure (optional)

All tunables live in `~/.ai-memory/config.json` (override the path with `AI_MEMORY_CONFIG`). Missing file or invalid keys fall back to defaults per key, silently.

| Key | Default | Meaning |
|-----|---------|---------|
| `pack_limit` | 12 | Recall pack total row budget |
| `recency_half_life_days` | 30.0 | Eviction score recency decay |
| `usage_saturation` | 3.0 | Eviction score hit-count saturation |
| `turn_recall_cap` | 3 | Max rows injected per user prompt |
| `turn_recall_min_score` | 0.0 | bm25 relevance floor for turn recall; 0 = off |
| `decay_window_days` | 30 | Episodics older than this may decay |
| `reinforce_step` | 0.05 | Confidence bump per recall (cap 1.0) |
| `scope_map` | `{}` | Absolute path prefix to scope slug |
| `secret_patterns` | `[]` | Extra redaction patterns `{label, regex}` |

Note on the hook commands: they invoke `python`. On systems where only `python3` exists on PATH (stock macOS, many Linux distributions), edit the commands in `hooks/hooks.json` (or your settings.json wiring) accordingly; a missing interpreter fails soft but the plugin is silently inactive.

## 4. Teach the session to write memos

Capture works by scanning the transcript for fenced memo blocks. Add a line like this to your `CLAUDE.md`:

> When a turn establishes something worth remembering (a decision, a fact, a correction, a lesson), end the reply with a fenced ` ```memo ` block containing a one-line outcome. Only when the turn earned it.

## 5. Seed from what you already have (optional)

```bash
python -m ai_memory seed path/to/CLAUDE.md [--scope my-project]
```

Bullet lines become memories: rule-shaped lines procedural, the rest semantic; re-running skips existing rows.

## 6. Verify

```bash
python -m ai_memory remember --type semantic "ai-memory installed on this machine"
python -m ai_memory recall
python -m ai_memory status
```

Start a new Claude Code session: the recall pack should appear as injected context.
