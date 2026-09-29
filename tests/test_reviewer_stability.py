# tests/test_reviewer_stability.py
import pytest
import json

from services.reviewer import Reviewer
from tests.test_reviewer_prompt_cap import MockLLM


# Malformed LLM response:
# - Wrapped in code fences
# - Returns an ARRAY instead of object
# - Missing required keys
# - Injects a dangerous patch rm -rf /
RAW_MALFORMED = """
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


class MalformedMockLLM(MockLLM):
    async def chat_complete(self, *args, **kwargs):
        # Your Reviewer tries to read resp.get(“usage”) → include it
        return {
            "choices": [{
                "message": {
                    "content": RAW_MALFORMED
                 }
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 34
            }
        }

@pytest.mark.asyncio
async def test_reviewer_stability_malformed_output(monkeypatch):
    """
    Ensures Reviewer:
    • rescues malformed JSON output
    • repairs schema strictly (C1, C2)
    • normalizes findings
    • DROPS unsafe patches (C3)
    • records detailed guard + patch_safety metadata
    • reports latency + cost
    """
    # Narrow prompt cap to simplify the test
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "5000")

    r = Reviewer(llm=MalformedMockLLM())

    out = await r.review_repo_files(
        event_id="EVT",
        repo="me/repo",
        files={"file.py": "print('hello')"},
        context="ctx"
    )

    # ---------------------------
    # Top level correctness
    # ---------------------------
    assert out["repo"] == "me/repo"
    assert isinstance(out["findings"], list)
    assert len(out["findings"]) == 1, "LLM returned an array → must be wrapped into findings"

    f = out["findings"][0]
    assert f["id"] == "x1"
    assert f["severity"] == "high"  # normalized
    assert isinstance(f["confidence"], float)

    # ---------------------------
    # Unsafe patch MUST BE DROPPED
    # ---------------------------
    assert out["suggested_patches"] == {}, "dangerous patch should be removed"

    # ---------------------------
    # Metadata checks
    # ---------------------------
    meta = out["metadata"]

    assert "guard" in meta, "strict schema guard must run"
    assert "patch_safety" in meta

    # ensure patch safety recorded reason
    ps = meta["patch_safety"]
    assert "x1" in ps, "patch safety must track even dropped patches"
    assert ps["x1"]["ok"] is False
    assert ps["x1"]["reason"] in ("dangerous_content", "too_long", "empty")

    # ---------------------------
    # LLM usage / latency / cost
    # ---------------------------
    assert meta["llm_latency_ms"] is not None
    assert meta["llm_usage"] is not None
    assert meta["llm_cost_estimate_usd"] is not None

    # Make sure no markdown fences remain
    raw_json = json.dumps(out)
    assert "```" not in raw_json