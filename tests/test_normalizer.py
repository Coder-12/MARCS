import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT) # type: ignore[arg-type]

import pytest
from orchestration.normalizer import normalize_github_event
from orchestration.types import InternalEvent
from agents.results import AgentFinding, SynthesizedReview
from services.normalizer import normalize_review, compute_fingerprint


def test_normalize_ping():
    payload = {"zen": "Hello", "hook_id": 77}
    evt = normalize_github_event("ping", "1", payload)

    assert evt.event_type == "ping"
    assert evt.priority == "low"
    assert evt.delivery_id == "1"
    assert evt.normalized == {"zen": "Hello", "hook_id": 77}


def test_normalize_push():
    payload = {
        "ref": "refs/heads/main",
        "before": "abc",
        "after": "def",
        "commits": [],
        "head_commit": {"id": "def", "message": "hi"},
        "repository": {"full_name": "test/repo"},
    }

    evt = normalize_github_event("push", "xyz", payload)

    assert evt.event_type == "push"
    assert evt.priority == "normal"
    assert evt.commit_sha == "def"
    assert evt.repo_full_name == "test/repo"
    assert evt.normalized["head_commit_message"] == "hi"
    assert evt.normalized["commit_count"] == 0


def test_normalize_pull_request():
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 7,
            "state": "open",
            "title": "Fix",
            "merged": False,
            "head": {"sha": "abcd", "ref": "branch"},
        },
        "repository": {"full_name": "me/repo"},
    }

    evt = normalize_github_event("pull_request", "456", payload)

    assert evt.event_type == "pull_request"
    assert evt.priority == "high"
    assert evt.pr_number == 7
    assert evt.repo_full_name == "me/repo"
    assert evt.commit_sha == "abcd"
    assert evt.normalized["pr_title"] == "Fix"
    assert evt.normalized["merged"] is False


def make_f(message="x", location="file:1", category="style", metadata=None, confidence=0.5):
    return AgentFinding(
        id=None,
        message=message,
        location=location,
        category=category,
        metadata=metadata or {},
        confidence=confidence,
        evidence=None,
        suggested_patch=None,
    )

def test_fingerprint_stable_for_same_content():
    f1 = make_f(message="line too long", location="a.py:10", metadata={"rule":"long_line"})
    f2 = make_f(message="line too long", location="a.py:10", metadata={"rule":"long_line"})
    r = SynthesizedReview(event_id="r1", repo="me/repo", findings=[f1, f2], suggested_patches={})
    r = normalize_review(r)
    # both should have fingerprint and same fingerprint
    fps = [f.fingerprint for f in r.findings]
    assert fps[0] == fps[1] # type: ignore[arg-type]
    assert len(fps[0]) > 0 # type: ignore[arg-type]

def test_normalize_evidence_and_confidence():
    f = make_f(message="m", location="b", confidence="0.7") # type: ignore[arg-type]
    r = SynthesizedReview(event_id="r2", repo="me/repo", findings=[f], suggested_patches={})
    r = normalize_review(r)
    assert isinstance(r.findings[0].evidence, list) # type: ignore[arg-type]
    assert isinstance(r.findings[0].confidence, float) # type: ignore[arg-type]