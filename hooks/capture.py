"""Stop hook: capture session outcomes into episodic memory.

Scans the session transcript for fenced ```memo blocks in assistant
messages (the model is asked, via the /memory command and README, to end
high-value turns with one) and stores each un-captured memo as an
episodic memory tagged with the session id.

Fails soft: any error exits 0 so the session is never blocked.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MEMO_RE = re.compile(r"```memo\s*\n(.*?)```", re.DOTALL)


def extract_memos(transcript_path: str) -> list[str]:
    memos: list[str] = []
    path = Path(transcript_path)
    if not path.exists():
        return memos
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        message = event.get("message") or {}
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        texts = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        for text in texts:
            memos.extend(m.strip() for m in MEMO_RE.findall(text))
    return memos


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    transcript = payload.get("transcript_path", "")
    session_id = payload.get("session_id", "unknown")
    try:
        memos = extract_memos(transcript)
    except Exception:
        return 0
    if not memos:
        return 0
    try:
        from ai_memory import config, db, redact, store

        cfg = config.load()
        # Redact here so the dedup comparison below sees the stored form;
        # store.remember redacts again as the backstop for every other path.
        memos = [redact.redact(m, cfg["secret_patterns"])[0] for m in memos]
        scope = config.resolve_scope(payload.get("cwd"), cfg)
        conn = db.connect()
        already = {
            row["content"]
            for row in conn.execute(
                "SELECT content FROM memories WHERE origin_session = ?", (session_id,)
            ).fetchall()
        }
        for memo in memos:
            if memo not in already:
                store.remember(
                    conn, memo, mtype="episodic", origin_session=session_id, scope=scope
                )
                already.add(memo)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
