import pytest, json
from services.reviewer import Reviewer
from tests.test_reviewer_prompt_cap import MockLLM

MALFORMED = """
Here is your review:
```json
[
  {
    "id": "x1",
    "message": "looks fine",
    "severity": "High",
    "confidence": "0.8"
  }
]
suggested_patches: { "x1": "rm -rf /" }
EXTRA_TEXT
"""

class XMockLLM(MockLLM):
    async def chat_complete(self, *a, **kw):
        return {"choices":[{"message":{"content": MALFORMED}}], "usage": {"prompt_tokens":1,"completion_tokens":1}}

@pytest.mark.asyncio
async def test_cross_validator_flags_dangerous_patch(monkeypatch):
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS","5000")
    r = Reviewer(llm=XMockLLM())
    out = await r.review_repo_files(event_id="evt", repo="me/repo", files={"f.py":"x"}, context="ctx")
    assert out["suggested_patches"] == {}
    assert "patch_safety" in out["metadata"]
    assert any(k for k in out["metadata"]["patch_safety"].keys())
    assert any(not v["ok"] for v in out["metadata"]["patch_safety"].values())