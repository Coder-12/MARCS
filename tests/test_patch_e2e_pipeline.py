# tests/test_patch_e2e_pipeline.py
from services.patch_eval import preview_selector_pipeline_v2
from services.patch_scorer import score_all_patches
from services.patch_selector_pro import select_patches_for_review
from agents.results import SynthesizedReview, AgentFinding


def make_review():
    f1 = AgentFinding(
        id="fA",
        severity="low",
        message="Line too long",
        category="style",
        metadata={"rule": "long_line"},
        confidence=0.4,
    )
    f2 = AgentFinding(
        id="fB",
        severity="high",
        message="Possible secret",
        category="security",
        metadata={"rule": "secret"},
        confidence=0.9,
    )
    return SynthesizedReview(
        event_id="evt-test",
        repo="me/repo",
        findings=[f1, f2],
        suggested_patches={
            "p1": "# wrap the long line\nline = wrap(long_line)",
            "p2": "vault.get_secret('X')",
            "p3": "print(secret)",
        },
        metadata={"fingerprint_index": {}},
    )


def test_e2e_scoring_selector_preview():
    review = make_review()

    # Step 1: scoring
    scores = score_all_patches(review)
    assert len(scores) == 3
    assert all(s.score >= 0.0 for s in scores)

    # Step 2: selection
    selected = select_patches_for_review(review, top_k=2)
    assert isinstance(selected, dict)
    assert "fA" in selected or "fB" in selected

    # Step 3: preview
    preview = preview_selector_pipeline_v2(review)
    assert "all_scores" in preview
    assert "selected" in preview
    assert "selected_map" in preview

    # Main assertion: output is consistent and deterministic
    assert len(preview["all_scores"]) == 3
    for fid, items in preview["selected"].items():
        for it in items:
            assert "patch_id" in it
            assert "score" in it