"""#66: LongMemEval adapter runs the real pipeline on synthetic instances."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "bench"))
import longmemeval  # noqa: E402


def _instance(qid: str, question: str, evidence: str, distractors: list[str],
              qtype: str = "single-session-user") -> dict:
    return {
        "question_id": qid,
        "question_type": qtype,
        "question": question,
        "answer": "42",
        "haystack_session_ids": ["sess1", "sess2"],
        "haystack_sessions": [
            [{"role": "user", "content": evidence, "has_answer": True}],
            [{"role": "user", "content": d} for d in distractors],
        ],
    }


def test_adapter_scores_retrievable_evidence(tmp_path):
    data = tmp_path / "lme.json"
    data.write_text(json.dumps([
        _instance("q1", "what database does staging run",
                  "our staging environment runs postgres sixteen",
                  ["the weather was nice on tuesday", "lunch was a sandwich"]),
        _instance("q2", "what colour is the bikeshed",
                  "we painted the bikeshed turquoise last spring",
                  ["the deploy pipeline uses github actions"]),
    ]), encoding="utf-8")
    report = longmemeval.run(data, limit=0, k=5)
    assert report["questions"] == 2
    assert report["evidence_recall_at_k"] == 1.0
    assert report["mrr"] > 0.4
    assert "caveat" in report
    assert "single-session-user" in report["by_type"]


def test_adapter_skips_instances_without_evidence(tmp_path):
    data = tmp_path / "lme.json"
    no_evidence = {
        "question_id": "abs1", "question_type": "abstention",
        "question": "what is unknowable",
        "haystack_session_ids": ["s"],
        "haystack_sessions": [[{"role": "user", "content": "nothing relevant here"}]],
    }
    data.write_text(json.dumps([
        no_evidence,
        _instance("q1", "what database does staging run",
                  "our staging environment runs postgres sixteen", ["filler row"]),
    ]), encoding="utf-8")
    report = longmemeval.run(data, limit=0, k=5)
    assert report["questions"] == 1 and report["skipped_no_evidence"] == 1


def test_adapter_reports_misses_honestly(tmp_path):
    data = tmp_path / "lme.json"
    data.write_text(json.dumps([
        _instance("q1", "zzz qqq xxx unfindable cue",
                  "completely unrelated evidence sentence",
                  ["distractor one", "distractor two"]),
    ]), encoding="utf-8")
    report = longmemeval.run(data, limit=0, k=5)
    assert report["evidence_recall_at_k"] == 0.0
    assert report["mrr"] == 0.0
