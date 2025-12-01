# tests/test_evaluator.py

import os
import sys
import json
import pytest
import asyncio

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT) # type: ignore[arg-type]

from eval.schema import GoldenCase, ExpectedResult
from services.evaluator import (
    evaluate_goldencase_result,
    evaluate_review_against_golden,
)
from agents.results import SynthesizedReview, AgentFinding
from services.review_store import save_review, get_review
from services.metrics_store import metrics


@pytest.mark.asyncio
async def test_golden_case_basic():
    """
    Test GoldenCase → review comparison based on basic constraints.
    """
    # 1) Create fake finding
    f = AgentFinding(
        id="style-1",
        category="style",
        severity="low",
        message="line too long",
        explanation="unit test",
        confidence=0.5,
    )

    # 2) Fake review stored
    review = SynthesizedReview(
        event_id="case1",
        repo="me/repo",
        findings=[f],
        suggested_patches={},
        summary="test summary"
    )
    await save_review(review)

    # 3) GoldenCase definition
    golden = GoldenCase(
        id="case1",
        description="simple style",
        headers={"X-GitHub-Delivery": "case1"},
        payload={"dummy": True},
        expected=ExpectedResult(
            min_findings=1,
            max_findings=5,
            categories=["style"],
            min_patches=0,
            allow_extra_findings=True,
        )
    )

    # 4) Evaluate
    review_dict = review.model_dump()
    ok, errs = await evaluate_goldencase_result(review_dict, golden)

    assert ok is True
    assert errs == []
    snap = await metrics.snapshot()
    print(f"_SNAP: {snap}")
    assert "case1" in snap["cases"]


@pytest.mark.asyncio
async def test_event_matching_evaluator():
    """
    Test evaluate_review_against_golden:
    - store a review
    - create a temporary golden dataset entry
    - ensure TP/FP/FN and precision/recall/f1 calculated correctly
    """
    # 1) Store fake review
    f1 = AgentFinding(
        id="sec-99",
        category="security",
        severity="high",
        message="hardcoded secret",
        explanation="bad",
        confidence=0.9,
    )
    review = SynthesizedReview(
        event_id="evt123",
        repo="me/repo",
        findings=[f1],
        suggested_patches={"sec-99": "# fakepatch"},
        summary="test"
    )
    await save_review(review)

    # 2) Create temporary golden json file
    golden_map = {
        "evt123": {
            "expected_findings": [
                {
                    "id": "sec-99",
                    "category": "security",
                    "message": "hardcoded secret",
                    "suggested_patch": None
                }
            ]
        }
    }

    # Write file to data/golden/evt123.json
    tmp_dir = os.path.join(PROJECT_ROOT, "data", "golden")
    os.makedirs(tmp_dir, exist_ok=True)

    golden_path = os.path.join(tmp_dir, "evt123.json")
    with open(golden_path, "w", encoding="utf-8") as fh:
        json.dump({"id": "evt123", **golden_map["evt123"]}, fh, indent=2)

    # 3) Evaluate
    res = await evaluate_review_against_golden(review, golden_dir=tmp_dir)

    assert res["tp"] == 1
    assert res["fp"] == 0
    assert res["fn"] == 0
    assert res["precision"] == 1.0
    assert res["recall"] == 1.0
    assert res["f1"] == 1.0


@pytest.mark.asyncio
async def test_missing_golden_event():
    """
    If golden entry missing, evaluator should report "golden_missing".
    """
    f = AgentFinding(
        id="x1",
        category="style",
        severity="low",
        message="x",
    )
    review = SynthesizedReview(
        event_id="no_golden_999",
        repo="me/repo",
        findings=[f],
    )
    await save_review(review)

    res = await evaluate_review_against_golden(review, golden_dir="nonexistent-dir")
    assert res["error"] == "golden_missing"