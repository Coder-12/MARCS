# tests/test_patch_scorer_v2.py
from services.patch_scorer import (
    score_patch_for_finding,
    score_all_patches,
    PatchScore,
)

def test_basic_scorer_runs(synthetic_review, finding_style):
    ps = score_patch_for_finding(
        synthetic_review,
        finding_style,
        patch_id="p1",
        patch_text="# fix long line\nwrap()"
    )
    assert isinstance(ps, PatchScore)
    assert ps.score > 0.0
    assert "rule_match" in ps.breakdown


def test_security_scorer_boost(synthetic_review, finding_security):
    ps_good = score_patch_for_finding(
        synthetic_review,
        finding_security,
        patch_id="p2",
        patch_text="vault.get_secret('X')"
    )
    ps_bad = score_patch_for_finding(
        synthetic_review,
        finding_security,
        patch_id="p3",
        patch_text="print(secret)"
    )
    assert ps_good.score > ps_bad.score


def test_message_overlap_works(synthetic_review, finding_style):
    ps = score_patch_for_finding(
        synthetic_review,
        finding_style,
        patch_id="p1",
        patch_text="This patch fixes Line too long by wrapping."
    )
    assert ps.breakdown["message_overlap"] > 0.3


def test_score_all_patches(synthetic_review):
    scores = score_all_patches(synthetic_review)
    assert isinstance(scores, list)
    assert len(scores) == 4
    assert all(isinstance(s, PatchScore) for s in scores)