"""PreToolUse hook on Task: subagent spawn-time injection (FR-D2).

A spawned agent starts blind; this appends a small scoped recall pack,
compiled against the subagent's own prompt as the task, to that prompt via
updatedInput. Set config spawn_pack_limit to 0 to disable. Fails soft.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Task":
        return 0
    tool_input = payload.get("tool_input") or {}
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return 0
    try:
        from ai_memory import config, db, store

        cfg = config.load()
        limit = int(cfg["spawn_pack_limit"])
        if limit <= 0:
            return 0
        conn = db.connect()
        pack = store.recall_pack(
            conn,
            task=" ".join(prompt.split()[:64]),
            scope=config.resolve_scope(payload.get("cwd"), cfg),
            limit=limit,
            cfg=cfg,
        )
    except Exception:
        return 0
    if not pack:
        return 0
    updated = dict(tool_input)
    updated["prompt"] = f"{prompt}\n\n{pack}"
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": updated,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
