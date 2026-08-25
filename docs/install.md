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

Add the repo as a plugin (marketplace or local path per the Claude Code plugin docs). The plugin ships:

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

## 3. Teach the session to write memos

Capture works by scanning the transcript for fenced memo blocks. Add a line like this to your `CLAUDE.md`:

> When a turn establishes something worth remembering (a decision, a fact, a correction, a lesson), end the reply with a fenced ` ```memo ` block containing a one-line outcome. Only when the turn earned it.

## 4. Verify

```bash
python -m ai_memory remember --type semantic "ai-memory installed on this machine"
python -m ai_memory recall
python -m ai_memory status
```

Start a new Claude Code session: the recall pack should appear as injected context.
