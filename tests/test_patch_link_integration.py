# tests/test_patch_link_integration.py
from services.patch_selector_pro import select_patches_for_review
from services.patch_linker import link_patches
from agents.results import SynthesizedReview, AgentFinding


def test_linking_after_selection():
    f = AgentFinding(
        id="f123",
        severity="low",
        message="Line too long",
        category="style",
        metadata={"rule": "long_line"},
        confidence=0.5,
    )
    review = SynthesizedReview(
        event_id="evt-1",
        repo="x",
        findings=[f],
        suggested_patches={
            "f123": "# wrap long line\nwrap()"
        },
        metadata={"fingerprint_index": {}},
    )

    # selector keeps patch
    select_patches_for_review(review)
    assert "selected_patches" in review.metadata

    # link patches
    linked = link_patches(review)
    assert linked.findings[0].linked_patches # type: ignore[arg-type]