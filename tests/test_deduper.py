# tests/test_deduper.py
from agents.results import AgentFinding, SynthesizedReview
from services.normalizer import normalize_review
from services.deduper import dedupe_findings
import pytest

def make_f(message, loc, conf=0.5, metadata=None):
    return AgentFinding(id=None, message=message, location=loc, category="style", metadata=metadata or {}, confidence=conf)

def test_dedupe_merges_similar():
    f1 = make_f("same", "file:1", conf=0.2, metadata={"rule":"r"})
    f2 = make_f("same", "file:1", conf=0.9, metadata={"rule":"r"})
    r = SynthesizedReview(event_id="e1", repo="me/repo", findings=[f1, f2], suggested_patches={})
    r = normalize_review(r)
    r, mapping = dedupe_findings(r)
    assert len(r.findings) == 1
    # resulting confidence should be the max (0.9)
    assert r.findings[0].confidence == pytest.approx(0.9)
    # mapping should contain fingerprint -> [ids]
    assert isinstance(mapping, dict)
    assert len(next(iter(mapping.values()))) == 2