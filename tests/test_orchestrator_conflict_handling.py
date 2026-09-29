import os
import json
import asyncio
import subprocess
import sys

def test_orchestrator_conflict_handling(tmp_path):
    # --- Prepare repo ---
    repo = tmp_path / "repo"
    repo.mkdir()
    file_path = repo / "a.py"
    original_content = "print('hello')\n"
    file_path.write_text(original_content)

    # Prepare conflicting patch
    patch = (
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-print('nope')\n"
        "+print('world')\n"
    )

    review = {
        "findings": [],
        "suggested_patches": {"a.py": patch}
    }

    review_json = tmp_path / "review.json"
    review_json.write_text(json.dumps(review))

    # --- NEW: Set artifacts directory via env ---
    env = os.environ.copy()
    env["MACRS_ARTIFACT_DIR"] = str(tmp_path / "artifacts")

    # --- Run orchestrator CLI ---
    cmd = [
        sys.executable, "scripts/demo_orchestrator.py",
        "--event-id", "evt-conflict",
        "--repo-root", str(repo),
        "--review-json", str(review_json),
        "--yes"
    ]

    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)

    assert proc.returncode == 0
    assert file_path.read_text() == original_content

    artifacts_dir = tmp_path / "artifacts" / "evt-conflict"
    assert artifacts_dir.exists()

    applied_json = artifacts_dir / "apply_result.json"
    assert applied_json.exists()

    with applied_json.open() as fh:
        apply_result = json.load(fh)

    assert "dropped" in apply_result
    assert "a.py" in apply_result["dropped"]
    assert apply_result["dropped"]["a.py"] == "conflict"