"""SessionStart hook: inject the compiled recall pack as additional context.

Reads the hook payload from stdin (unused for now beyond validation) and
emits the recall pack via hookSpecificOutput.additionalContext.
Fails soft: any error means no injection, never a blocked session.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    try:
        json.load(sys.stdin)
    except Exception:
        pass
    try:
        from ai_memory import db, store

        conn = db.connect()
        pack = store.recall_pack(conn)
    except Exception:
        return 0
    if pack:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": pack,
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
