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
_ENTROPY_THRESHOLD = 4.2


def _shannon(token: str) -> float:
    counts: dict[str, int] = {}
    for ch in token:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(token)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _compile_extra(extra: list | None) -> list[tuple[str, re.Pattern]]:
    out = []
    for item in extra or []:
        try:
            out.append((str(item["label"]), re.compile(item["regex"])))
        except Exception:
            continue
    return out


def redact(text: str, extra_patterns: list | None = None) -> tuple[str, int]:
    """Return (clean_text, redaction_count)."""
    count = 0
    for label, rx in BUILTIN_PATTERNS + _compile_extra(extra_patterns):
        text, n = rx.subn(f"[REDACTED:{label}]", text)
        count += n

    def _entropy_sub(match: re.Match) -> str:
        nonlocal count
        token = match.group(0)
        if _shannon(token) > _ENTROPY_THRESHOLD:
            count += 1
            return "[REDACTED:high-entropy]"
        return token

    text = _ENTROPY_CANDIDATE.sub(_entropy_sub, text)
    return text, count
