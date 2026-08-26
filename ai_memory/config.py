"""Configuration: one JSON file, documented defaults, per-key fail-soft.

A missing, unreadable, or partially invalid file must never break anything:
every key falls back to its default independently (FR-O2, FR-O3).
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

DEFAULTS: dict = {
    "pack_limit": 12,              # recall pack total row budget
    "recency_half_life_days": 30.0,  # eviction score recency decay
    "usage_saturation": 3.0,       # eviction score hit-count saturation constant
    "turn_recall_cap": 3,          # max rows injected per user prompt
    "turn_recall_min_score": 0.0,  # bm25 relevance floor; 0 = off (tune via eval harness)
    "decay_window_days": 30,       # episodics older than this may decay
    "reinforce_step": 0.05,        # confidence bump per recall, capped at 1.0
    "spawn_pack_limit": 6,         # rows injected into a spawned subagent's prompt; 0 disables
    "feedback_penalty": 0.05,      # confidence cut on rows judged not useful
    "trace_retention_days": 30,    # recall traces older than this purge at decay
    "handoff_ttl_days": 7,         # unconsumed handoffs older than this purge at decay
    "embed_enabled": False,        # optional semantic search layer (local model, fail-soft)
    "embed_model": "nomic-embed-text",
    "embed_url": "http://localhost:11434",  # Ollama-compatible /api/embeddings endpoint
    "link_half_life_days": 45.0,   # associative link weight decay
    "link_reinforce_factor": 0.15, # Hebbian: weight += (1 - weight) * factor
    "link_prune_floor": 0.02,      # effective weight below this is pruned at decay
    "ambiguity_margin": 0.15,      # candidates within this of top score are flagged ambiguous
    "scope_map": {},               # {"<absolute path prefix>": "<scope slug>"}
    "exclude_paths": [],           # absolute path prefixes where all hooks no-op entirely
    "secret_patterns": [],         # [{"label": str, "regex": str}] extra redactions
    "instruction_patterns": [],    # [{"label": str, "regex": str}] extra injection screens
    "observer_post": "draft",      # self-maintenance output: draft | direct
    "observer_repo": "",           # gh repo for direct posting; empty forces draft mode
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
        # Finite and non-negative: NaN/Infinity would break SQL interpolation,
        # and a negative reinforce_step would erode confidence on every recall.
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        )
    return isinstance(value, type(default))


def is_excluded(cwd: str | None, cfg: dict) -> bool:
    """Boundary rule: directories under an excluded prefix are served by some
    other memory system; every hook no-ops there (no capture, no injection)."""
    excludes = cfg.get("exclude_paths") or []
    if not cwd or not excludes:
        return False
    try:
        current = os.path.normcase(str(Path(cwd).resolve()))
    except Exception:
        return False
    for prefix in excludes:
        try:
            root = os.path.normcase(str(Path(prefix).resolve()))
        except Exception:
            continue
        if current == root or current.startswith(root + os.sep):
            return True
    return False


def resolve_scope(cwd: str | None, cfg: dict) -> str:
    """FR-R9: map a working directory to a scope slug via config scope_map.

    Longest matching path prefix wins (nested directories inherit the nearest
    mapped ancestor); unmapped or unresolvable directories are `global`.
    """
    scope_map = cfg.get("scope_map") or {}
    if not cwd or not scope_map:
        return "global"
    try:
        current = os.path.normcase(str(Path(cwd).resolve()))
    except Exception:
        return "global"
    best_slug, best_len = "global", -1
    for prefix, slug in scope_map.items():
        try:
            root = os.path.normcase(str(Path(prefix).resolve()))
        except Exception:
            continue
        if current == root or current.startswith(root + os.sep):
            if len(root) > best_len:
                best_slug, best_len = str(slug), len(root)
    return best_slug


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
