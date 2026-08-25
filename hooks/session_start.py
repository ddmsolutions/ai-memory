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
    session_id = cwd = None
    try:
        payload = json.load(sys.stdin)
        session_id = payload.get("session_id")
        cwd = payload.get("cwd")
    except Exception:
        pass
    try:
        from ai_memory import config, db, store

        cfg = config.load()
        conn = db.connect()
        pack = store.recall_pack(
            conn, scope=config.resolve_scope(cwd, cfg), cfg=cfg, session_id=session_id
        )
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
