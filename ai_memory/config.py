"""Configuration: one JSON file, documented defaults, per-key fail-soft.

A missing, unreadable, or partially invalid file must never break anything:
every key falls back to its default independently (FR-O2, FR-O3).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS: dict = {
    "pack_limit": 12,              # recall pack total row budget
    "recency_half_life_days": 30.0,  # eviction score recency decay
    "usage_saturation": 3.0,       # eviction score hit-count saturation constant
    "turn_recall_cap": 3,          # max rows injected per user prompt
    "decay_window_days": 30,       # episodics older than this may decay
    "reinforce_step": 0.05,        # confidence bump per recall, capped at 1.0
    "scope_map": {},               # {"<absolute path prefix>": "<scope slug>"}
    "secret_patterns": [],         # [{"label": str, "regex": str}] extra redactions
}


def config_path() -> Path:
    env = os.environ.get("AI_MEMORY_CONFIG")
    if env:
        return Path(env)
    return Path.home() / ".ai-memory" / "config.json"


def _compatible(value, default) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(default))


def load(path: Path | None = None) -> dict:
    cfg = dict(DEFAULTS)
    target = path or config_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    if not isinstance(raw, dict):
        return cfg
    for key, default in DEFAULTS.items():
        if key in raw and _compatible(raw[key], default):
            cfg[key] = raw[key]
    return cfg
