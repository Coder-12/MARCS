# tests/conftest.py
import os
import sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT) # type: ignore[arg-type]

import pytest
from agents.results import AgentFinding, SynthesizedReview

@pytest.fixture
def finding_style():
    return AgentFinding(
        id="sty101",
        severity="low",
        message="Line too long",
        category="style",
        location="file.py:10",
        metadata={"rule": "long_line"},
        confidence=0.5,
        evidence=[],
    )

@pytest.fixture
def finding_security():
    return AgentFinding(
        id="sec777",
        severity="high",
        message="Hardcoded secret detected",
        category="security",
        location="config.py",
        metadata={"rule": "secret"},
        confidence=0.9,
        evidence=[],
    )

@pytest.fixture
def synthetic_review(finding_style, finding_security):
    return SynthesizedReview(
        event_id="evt_001",
        repo="me/repo",
        findings=[finding_style, finding_security],
        suggested_patches={
            "p1": "# fix long line\nline = wrap(line)",
            "p2": "vault.get_secret('X')",
            "p3": "print(secret)",
            "p4": "this is totally unrelated",
        },
        metadata={"fingerprint_index": {}},
    )