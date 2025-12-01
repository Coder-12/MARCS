# tests/test_patch_generator.py
import os
import sys
from pyexpat.errors import messages

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio
import pytest

from agents.patch_generator import PatchGenerator
from agents.results import AgentFinding, SynthesizedReview

@pytest.mark.asyncio
async def test_generate_patch_style():
    pg = PatchGenerator()
    f = AgentFinding(
        id="style-1", category="style", severity="low",
        confidence=0.5, location="file.py:1-3",
        explanation="line too long",
        message="test agent"
    )
    print("_hello")
    review = SynthesizedReview(event_id="t1", repo="me/repo", findings=[f], summary="s")
    ctx = {"span_id": None}
    patches = await pg.generate_patches_for_review(review, ctx)
    print(f"PATCH = {patches}")
    assert "style-1" in patches
    assert "PATCH_STUB" in patches["style-1"]

@pytest.mark.asyncio
async def test_generate_patch_security():
    pg = PatchGenerator()
    f = AgentFinding(
        id="sec-1", category="security", severity="high",
        confidence=0.9, location="file.py:10", explanation="possible secret",
        message="test agent"
    )
    review = SynthesizedReview(event_id="t2", repo="me/repo", findings=[f], summary="s")
    patches = await pg.generate_patches_for_review(review, {"span_id": None})
    assert "sec-1" in patches
    assert "vault" in patches["sec-1"] or "SOME_SECRET" in patches["sec-1"]