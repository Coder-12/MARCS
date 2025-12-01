# tests/test_finding_normalizer.py
import pytest
from agents.results import AgentFinding, Evidence, SynthesizedReview
from services.finding_normalizer import normalize_review, compute_fingerprint

def test_normalize_fingerprint_and_evidence():
    f = AgentFinding(
        id="f1",
        severity="low",
        message="Line too long in file",
        confidence=0.5,
        category="Style",
        location="file.py:10",
        evidence=[Evidence(source="file.py", line=10, snippet="very long line xxxxx", rule="long_line", score=0.8)]
    )
    r = SynthesizedReview(event_id="ev1", repo="me/repo", findings=[f])
    r2 = normalize_review(r)
    assert r2.findings[0].fingerprint is not None
    assert isinstance(r2.findings[0].fingerprint, str)
    assert r2.metadata.get("fingerprint_index")
    assert r2.findings[0].evidence_score == pytest.approx(0.8)