import os
import json
import asyncio
import tempfile
from services.reviewer import Reviewer
from core.llm_client import LLMClient

# ---------------------------------------------------------
# A simple deterministic mock LLM response for Step-6.3
# ---------------------------------------------------------
class MockLLM(LLMClient):
    async def chat_complete(self, messages, temperature=0, max_tokens=1024):
        # Always return 1 finding + 1 patch
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "findings": [{
                            "id": "f1",
                            "category": "style",
                            "severity": "low",
                            "message": "Add print",
                            "explanation": "",
                            "confidence": 0.9
                        }],
                        # IMPORTANT: patch key is findingID, not filename
                        "suggested_patches": {
                            "f1": (
                                "--- a/code.py\n"
                                "+++ b/code.py\n"
                                "@@ -1,1 +1,2 @@\n"
                                " print('hello')\n"
                                "+print('patched')\n"
                            )
                        },
                        "summary": "ok"
                    })
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}
        }

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
def run(coro):
    """Run async test helpers without requiring pytest-asyncio."""
    return asyncio.run(coro)


# =========================================================
# STEP-6.3 — ARTIFACT, PATCH APPLY, DIFF VALIDATION
# =========================================================

def test_step6_3_full_pipeline(tmp_path):
    """
    Full Step-6.3 integration test:
      - mock LLM output (findingID → patch)
      - reviewer maps → filename
      - dry-run passes
      - real apply modifies file
      - unified diff generated
      - artifact written with correct unified diffs
      - reviewer output includes return_fields
    """
    # -----------------------------------------------------
    # Create temp repo containing a single file
    # -----------------------------------------------------
    repo = tmp_path / "repo"
    repo.mkdir()

    orig_code = "print('hello')\n"
    (repo / "code.py").write_text(orig_code)

    files = {"code.py": orig_code}

    reviewer = Reviewer(llm=MockLLM())

    # -----------------------------------------------------
    # Run review + apply
    # -----------------------------------------------------
    result = run(reviewer.review_repo_files(
        event_id="evt-step6_3",
        repo="me/repo",
        files=files,
        context="",
        apply_patches_flag=True,
    ))

    # -----------------------------------------------------
    # Basic shape checks
    # -----------------------------------------------------
    assert "applied_patches" in result
    assert "applied_diffs" in result
    assert "artifact_path" in result
    assert "metadata" in result

    # Should contain patched file content
    applied_patches = result["applied_patches"]
    print(f"_applied_patches: {applied_patches}")
    assert "code.py" in applied_patches
    assert "print('patched')" in applied_patches["code.py"]

    # Should contain unified diff
    applied_diffs = result["applied_diffs"]
    assert "code.py" in applied_diffs
    diff_text = applied_diffs["code.py"]

    # Canonical diff header checks
    assert diff_text.startswith("--- a/code.py")
    assert "+++ b/code.py" in diff_text
    assert "+print('patched')" in diff_text

    # -----------------------------------------------------
    # Check artifact file exists
    # -----------------------------------------------------
    artifact_path = result["artifact_path"]
    assert os.path.exists(artifact_path)

    artifact = json.loads(open(artifact_path, "r", encoding="utf-8").read())

    # Artifact schema checks
    assert artifact["event_id"] == "evt-step6_3"
    assert artifact["repo"] == "me/repo"
    assert artifact["summary"] == "Patches applied successfully."

    # artifact["applied_patches"] stores unified diffs
    assert "code.py" in artifact["applied_patches"]
    assert artifact["applied_patches"]["code.py"].startswith("--- a/code.py")

    # artifact["applied_diffs"] should match
    assert artifact["applied_patches"]["code.py"] == artifact["applied_diffs"]["code.py"]

    # findings present
    assert isinstance(artifact["findings"], list)
    assert artifact["findings"][0]["id"] == "f1"

    # Metadata correctness
    meta = result["metadata"]
    assert "journal_path" in meta or "journal_path" in result["metadata"]
    assert "patch_safety" in meta["guard"]
    assert "file_hashes" in meta


# =========================================================
# SECOND TEST: When ALL patches drop in DRY RUN
# =========================================================

class MockLLM_EmptyPatch(LLMClient):
    async def chat_complete(self, messages, temperature=0, max_tokens=1024):
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "findings": [],
                        "suggested_patches": {},
                        "summary": "nothing"
                    })
                }
            }]
        }

def test_step6_3_no_valid_patches(tmp_path):
    reviewer = Reviewer(llm=MockLLM_EmptyPatch())

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text("print('x')\n")

    result = run(reviewer.review_repo_files(
        event_id="evt-novalid",
        repo="r",
        files={"x.py": "print('x')\n"},
        apply_patches_flag=True,
    ))

    assert result["applied_patches"] == {}
    assert result["applied_diffs"] == {}
    assert os.path.exists(result["artifact_path"])

    artifact = json.loads(open(result["artifact_path"]).read())
    assert artifact["summary"] == "No valid patches to apply."


# =========================================================
# THIRD TEST: Mapping findingID → filename (multi-file safe)
# =========================================================

class MockLLM_MultiFile(LLMClient):
    async def chat_complete(self, messages, temperature=0, max_tokens=1024):
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "findings": [{"id": "f123"}],
                        "suggested_patches": {
                            "f123": (
                                "--- a/a.py\n"
                                "+++ b/a.py\n"
                                "@@ -1,1 +1,2 @@\n"
                                " print('A')\n"
                                "+print('patched')\n"
                            )
                        },
                        "summary": "ok"
                    })
                }
            }]
        }

def test_step6_3_patch_mapping_multifile(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    files = {
        "a.py": "print('A')\n",
        "b.py": "print('B')\n",
    }

    reviewer = Reviewer(llm=MockLLM_MultiFile())

    # Should still map f123 → a.py safely
    result = run(reviewer.review_repo_files(
        event_id="evt-map",
        repo="me/repo",
        files=files,
        apply_patches_flag=True,
    ))

    print(f"result: {result}")
    applied = result["applied_patches"]
    assert "a.py" in applied
    assert "print('patched')" in applied["a.py"]

    diffs = result["applied_diffs"]
    assert "a.py" in diffs
    assert diffs["a.py"].startswith("--- a/a.py")