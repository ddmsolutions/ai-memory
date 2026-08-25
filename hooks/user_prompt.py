"""UserPromptSubmit hook: turn-time recall (FR-R5/R6).

FTS-matches the user's prompt against active memories and injects the top
config-capped rows not already injected this session. Silent on no match;
fails soft on everything.
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
    prompt = payload.get("prompt", "")
    session_id = payload.get("session_id")
    try:
        from ai_memory import db, store

        conn = db.connect()
        context = store.turn_recall(conn, prompt, session_id=session_id)
    except Exception:
        return 0
    if context:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": context,
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
