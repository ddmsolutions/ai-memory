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
HANDOFF_RE = re.compile(r"```handoff\s*\n(.*?)```", re.DOTALL)


def extract_handoffs(transcript_path: str) -> list[str]:
    """Fenced ```handoff blocks: state of play for the NEXT session (UC-35)."""
    handoffs: list[str] = []
    path = Path(transcript_path)
    if not path.exists():
        return handoffs
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
            handoffs.extend(h.strip() for h in HANDOFF_RE.findall(text) if h.strip())
    return handoffs
VALENCE_RE = re.compile(r"^valence:\s*(success|failure|neutral)\s*$", re.I | re.M)
ORIGIN_RE = re.compile(r"^origin:\s*(\S+)\s*$", re.I | re.M)


def memo_valence(memo: str) -> str | None:
    """FR-A1 memo syntax: a `valence: success|failure|neutral` line in the memo."""
    m = VALENCE_RE.search(memo)
    return m.group(1).lower() if m else None


def memo_origin(memo: str) -> str:
    """#64 memo syntax: an `origin: external` line marks content derived from
    untrusted input (a fetched page, another agent's output). Memos are
    model-written, so the default is 'agent'; a memo claiming 'owner' is
    exactly the laundering path and is ignored - only downgrades are honoured."""
    m = ORIGIN_RE.search(memo)
    return "external" if m and m.group(1).lower() == "external" else "agent"


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
        handoff_blocks = extract_handoffs(transcript)
    except Exception:
        return 0
    if not memos and not handoff_blocks:
        return 0
    try:
        from ai_memory import config, db, graph, redact, store

        cfg = config.load()
        if config.is_excluded(payload.get("cwd"), cfg):
            return 0
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
        import hashlib

        for memo in memos:
            if memo not in already:
                # FR-C8: instruction-shaped memos are quarantined, not stored
                # into any recallable scope and not silently dropped.
                flag = redact.screen_instructions(memo, cfg.get("instruction_patterns"))
                # #74: session-bound content hash. Replaying the same transcript
                # is a no-op; the same memo from a DIFFERENT session is a new
                # row (legitimate corroboration, not a duplicate).
                digest = hashlib.sha256(f"{session_id}|{memo}".encode("utf-8")).hexdigest()
                mid = store.remember(
                    conn, memo, mtype="episodic", origin_session=session_id,
                    scope="quarantine" if flag else scope,
                    valence=memo_valence(memo),
                    line_hash=digest,
                    origin=memo_origin(memo),
                )
                if not flag:
                    store.link_co_session(conn, mid, session_id)
                    # FR-N4: the memo's own entities: line joins the graph.
                    graph.mention_from_content(conn, mid, memo)
                already.add(memo)
        for block in handoff_blocks:
            try:
                store.handoff_write(conn, block, scope=scope, origin_session=session_id)
            except ValueError:
                continue  # instruction-shaped handoff refused; skip, never store
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
