"""Architectural test (#49): the redact+screen funnel rule as a mechanism.

The rule failed twice in one day (intend, handoff shipped without the funnel,
caught only by cold review). This test enumerates every INSERT path into the
injectable tables and fails CI when a new one appears outside the approved
funnel set, or when a funnel stops calling its guards.
"""
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_memory import portability, store  # noqa: E402

INJECTABLE = r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(memories|intentions|handoffs)\b"

# The complete approved set: (file, table) pairs allowed to insert.
APPROVED = {
    ("ai_memory/store.py", "memories"),      # remember() - redacts
    ("ai_memory/store.py", "intentions"),    # intend() - redacts + screens
    ("ai_memory/store.py", "handoffs"),      # handoff_write() - redacts + screens
    ("ai_memory/portability.py", "memories"),   # import_store() - screens to quarantine
    ("ai_memory/portability.py", "intentions"), # import_store()
    ("ai_memory/portability.py", "handoffs"),   # import_store()
}


def _scan() -> set[tuple[str, str]]:
    found = set()
    for folder in ("ai_memory", "hooks"):
        for py in (ROOT / folder).glob("*.py"):
            text = py.read_text(encoding="utf-8")
            for m in re.finditer(INJECTABLE, text, re.I):
                found.add((f"{folder}/{py.name}", m.group(1).lower()))
    return found


def test_no_insert_path_outside_the_funnel():
    found = _scan()
    rogue = found - APPROVED
    assert not rogue, (
        f"New INSERT path(s) into injectable tables outside the approved funnel: {rogue}. "
        "Route through store.remember/intend/handoff_write or add screening and "
        "update APPROVED with a review."
    )


def test_approved_funnels_still_exist():
    # If a funnel moves, APPROVED must be consciously updated, not silently drift.
    assert _scan() >= APPROVED


def test_funnels_reference_their_guards():
    assert "redact" in inspect.getsource(store.remember)
    for fn in (store.intend, store.handoff_write):
        src = inspect.getsource(fn)
        assert "redact" in src and "screen_instructions" in src, fn.__name__
    assert "screen_instructions" in inspect.getsource(portability.import_store)
    assert "screen_instructions" in inspect.getsource(portability.seed_from_markdown)


def test_scanner_would_catch_a_violation(tmp_path, monkeypatch):
    # Prove the detector detects: plant a synthetic rogue file and rescan.
    rogue_dir = tmp_path / "ai_memory"
    rogue_dir.mkdir()
    (rogue_dir / "rogue.py").write_text(
        'conn.execute("INSERT INTO handoffs (content) VALUES (?)", (x,))',
        encoding="utf-8")
    (tmp_path / "hooks").mkdir()
    import test_funnel_coverage as me
    monkeypatch.setattr(me, "ROOT", tmp_path)
    assert ("ai_memory/rogue.py", "handoffs") in me._scan()
