# tests/test_reviewer_live.py
import os
import pytest
import asyncio
from services.reviewer import Reviewer

@pytest.mark.skipif(os.environ.get("OPENAI_API_KEY") is None, reason="no OPENAI_API_KEY")
@pytest.mark.asyncio
async def test_reviewer_smoke_live():
    r = Reviewer()
    files = {"small.py": "def add(a,b):\n    return a+b\n"}
    out = await r.review_repo_files(event_id="live-evt-1", repo="me/repo", files=files, max_tokens=512)
    assert "event_id" in out
    assert isinstance(out.get("findings"), list)