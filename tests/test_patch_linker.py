# tests/test_patch_linker.py
import pytest
from agents.results import AgentFinding, SynthesizedReview
from services.patch_linker import link_patches

def test_patch_linking_by_message_substring():
    f = AgentFinding(id="f1", severity="low", message="line too long", category="style")
    review = SynthesizedReview(event_id="e1", repo="me/repo", findings=[f], suggested_patches={"p1": "# Suggestion: shorten long line\n# PATCH_STUB"})
    review2 = link_patches(review)
    assert "p1" in review2.findings[0].linked_patches

def test_patch_linker_security_keywords():
    f = AgentFinding(id="f2", severity="high", message="possible secret", category="security", location="commit:1")
    review = SynthesizedReview(event_id="e2", repo="me/repo", findings=[f], suggested_patches={"p2": "# PATCH: move secret to vault\nconfig_value = os.getenv('SOME_SECRET')"})
    review2 = link_patches(review)
    assert "p2" in review2.findings[0].linked_patches