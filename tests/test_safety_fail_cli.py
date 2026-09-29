# tests/test_safety_fail_cli.py
import json
import subprocess
import sys
import os
import tempfile
import shutil
from pathlib import Path

def test_safety_fail_blocks_apply(tmp_path):
    """
    Ensure --safety-fail prevents patch application when safety warnings exist.
    """
    # --- Prepare demo repo ---
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "danger.py").write_text("print('hello')\n")

    # --- Create a malicious patch with dangerous code ---
    review = {
        "findings": [],
        "suggested_patches": {
            "danger.py": (
                "--- a/danger.py\n"
                "+++ b/danger.py\n"
                "@@ -1,1 +1,2 @@\n"
                " print('hello')\n"
                "+os.system('rm -rf /')\n"
            )
        }
    }

    review_json = tmp_path / "review.json"
    review_json.write_text(json.dumps(review))

    # --- Run CLI with --safety-fail ---
    cmd = [
        sys.executable, "scripts/demo_orchestrator.py",
        "--event-id", "evt-safety",
        "--repo-root", str(repo),
        "--review-json", str(review_json),
        "--safety-fail"
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)

    # --- Assertions ---
    assert proc.returncode == 0  # graceful exit
    assert "blocked_by_safety" in proc.stdout
    assert "Safety-fail active" in proc.stdout

    # patch must NOT be applied
    assert (repo / "danger.py").read_text() == "print('hello')\n"