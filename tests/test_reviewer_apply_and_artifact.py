# tests/test_reviewer_apply_and_artifact.py
import os
import json
import pytest
from services.reviewer import Reviewer
from tests.test_reviewer_prompt_cap import MockLLM

# A real unified diff patch (as LLM would generate)
GOOD_PATCH = """--- a/code.py
+++ b/code.py
@@ -1,1 +1,2 @@
 print("hello")
+print("patched")
"""

# ---------------------------------------------------------------------
# BEST PRACTICE: LLM RETURNS json.dumps(payload)
# ---------------------------------------------------------------------
# This is EXACTLY how real LLM JSON output should be represented:
# - proper escaping for newlines
# - no indentation ambiguity
# - always parsable by rescue_json() -> json.loads()
# ---------------------------------------------------------------------
class PatchLLM(MockLLM):
    async def chat_complete(self, *a, **kw):

        payload = {
            "findings": [
                {
                    "id": "f1",
                    "severity": "low",
                    "message": "test",
                    "confidence": 0.8
                }
            ],
            "suggested_patches": {
                "code.py": GOOD_PATCH
            },
            "summary": "Auto-patch test"
        }

        # This produces valid JSON text exactly as a real model would
        content = json.dumps(payload)

        return {
            "choices": [
                {
                    "message": {
                        "content": content
                    }
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5}
        }


# ---------------------------------------------------------------------
# MAIN TEST
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reviewer_apply_and_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "5000")

    r = Reviewer(llm=PatchLLM())
    files = {"code.py": 'print("hello")\n'}

    out = await r.review_repo_files(
        event_id="evt-apply-1",
        repo="me/repo",
        files=files,
        context="ctx",
        apply_patches_flag=True,
    )

    # -------------------------
    # ARTIFACT CHECKS
    # -------------------------
    assert "artifact_path" in out
    assert os.path.exists(out["artifact_path"])

    data = json.loads(open(out["artifact_path"]).read())
    assert data["event_id"] == "evt-apply-1"
    assert "applied_patches" in data
    assert "code.py" in data["applied_patches"]

    # -------------------------
    # DIFF CHECK
    # -------------------------
    print(f"_DATA: {data}")
    assert "code.py" in data["applied_patches"]
    patched_text = data["applied_patches"]["code.py"]
    assert 'print("patched")' in patched_text

    # -------------------------
    # REVIEWER OUTPUT DIFF CHECK
    # -------------------------
    assert "applied_diffs" in out
    assert "code.py" in out["applied_diffs"]
    assert '+print("patched")' in out["applied_diffs"]["code.py"]