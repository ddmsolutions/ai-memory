"""Secret redaction at capture (FR-C6/C7).

Credential-shaped content is masked with a visible placeholder BEFORE any
insert, so no plaintext secret ever reaches the database file. Patterns are
extendable via config `secret_patterns`; invalid user regexes are skipped
(fail-soft), never fatal.
"""

from __future__ import annotations

import math
import re

BUILTIN_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("pem-block", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("api-key", re.compile(
        r"\b(?:sk|pk|rk|api|key|tok|secret|xox[abps])[-_][A-Za-z0-9_\-]{16,}\b", re.I)),
    ("bearer-token", re.compile(r"\bbearer\s+[A-Za-z0-9\-._~+/]{20,}=*", re.I)),
]

_ENTROPY_CANDIDATE = re.compile(r"\b[A-Za-z0-9+/=_\-]{32,}\b")
_HEX_CHARS = set("0123456789abcdef")


def _shannon(token: str) -> float:
    counts: dict[str, int] = {}
    for ch in token:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(token)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _entropy_threshold(token: str) -> float:
    """Hex secrets max out at log2(16)=4.0, so a flat 4.2 threshold could never
    catch them. Hex-only tokens get a lower bar, but only at length 48+ so
    40-char git SHAs in memos survive."""
    if set(token.lower()) <= _HEX_CHARS:
        return 3.4 if len(token) >= 48 else float("inf")
    return 4.2


def _compile_extra(extra: list | None) -> list[tuple[str, re.Pattern]]:
    out = []
    for item in extra or []:
        try:
            out.append((str(item["label"]), re.compile(item["regex"])))
        except Exception:
            continue
    return out


INSTRUCTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("instruction-override", re.compile(
        r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)?"
        r"(?:previous|prior|above|earlier)\s+(?:instructions?|context|rules?|prompts?)", re.I)),
    ("behaviour-hijack", re.compile(
        r"\byou\s+(?:must|should|will)\s+now\b|\bfrom\s+now\s+on,?\s+(?:you|always|respond|reply)"
        r"|\balways\s+respond\s+with\b|\bnew\s+instructions?\s*:", re.I)),
    ("concealment", re.compile(
        r"\bdo\s+not\s+(?:tell|inform|reveal\s+(?:this\s+)?to|mention\s+(?:this\s+)?to)\s+the\s+user\b", re.I)),
    ("system-prompt-probe", re.compile(r"\bsystem\s+prompt\b", re.I)),
]


def screen_instructions(text: str) -> str | None:
    """FR-C8: return the matched label when memo content is instruction-shaped
    (aimed at steering the model), else None. Callers quarantine, never drop."""
    for label, rx in INSTRUCTION_PATTERNS:
        if rx.search(text):
            return label
    return None


def redact(text: str, extra_patterns: list | None = None) -> tuple[str, int]:
    """Return (clean_text, redaction_count)."""
    count = 0
    for label, rx in BUILTIN_PATTERNS + _compile_extra(extra_patterns):
        text, n = rx.subn(f"[REDACTED:{label}]", text)
        count += n

    def _entropy_sub(match: re.Match) -> str:
        nonlocal count
        token = match.group(0)
        if _shannon(token) > _entropy_threshold(token):
            count += 1
            return "[REDACTED:high-entropy]"
        return token

    text = _ENTROPY_CANDIDATE.sub(_entropy_sub, text)
    return text, count
