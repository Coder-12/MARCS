# tests/test_golden_ingest.py
import os
import json
import pytest
from services.golden_ingest import ingest_one_async
from services.review_store import get_review

DATA_DIR = os.path.join("data", "golden", "v1")
TEST_FILE = os.path.join(DATA_DIR, "test_event.json")

@pytest.mark.asyncio
async def test_ingest_test_event_creates_review():
    # ensure file exists
    assert os.path.exists(TEST_FILE), f"{TEST_FILE} missing"
    res = await ingest_one_async(TEST_FILE)
    assert isinstance(res, dict)
    assert res.get("id") is not None
    # after ingest the review should be present in review_store
    rev = await get_review(str(res["id"]))
    assert rev is not None, "review_store did not persist the review for golden case"