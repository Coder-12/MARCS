import pytest
from agents.results import SynthesizedReview, AgentFinding
from services.patch_selector import apply_patch_ranking, score_patch, dedupe_similar_patches


def make_review_with_patches():
    f1 = AgentFinding(id="f1", severity="low", message="line too long", category="style", confidence=0.5)
    patches = {
        "p1": "# Suggestion: shorten long line by wrapping string\n# PATCH_STUB",
        "p2": "# Suggestion: add docstring and wrap the long line\n# PATCH_STUB",
        "p3": "# unrelated tiny note\n// todo",
    }
    r = SynthesizedReview(event_id="e1", repo="me/repo", findings=[f1], suggested_patches=patches)
    return r


def test_score_patch_basic():
    f = AgentFinding(id="f1", severity="low", message="line too long", category="style", confidence=0.5)
    sc1 = score_patch(f, "shorten the long line by wrapping")
    sc2 = score_patch(f, "move secret to vault and rotate token")
    assert sc1 > 0.0
    assert sc2 >= 0.0


def test_dedupe_similar():
    patches = {"a": "one two three four", "b": "one two three 4", "c": "completely different"}
    out = dedupe_similar_patches(patches, threshold=0.6)
    assert "c" in out
    # either a or b removed
    assert len(out) <= 3


def test_apply_patch_ranking_selects_top():
    r = make_review_with_patches()
    selected = apply_patch_ranking(r)
    # selected should contain at least one patch id
    assert isinstance(selected, dict)
    assert any(pid in selected for pid in ("p1", "p2"))
    # metadata must contain scores
    assert "patch_scores" in r.metadata
    assert "selected_patch" in r.metadata