# tests/test_safety_logs.py
import json
import os
import asyncio
import uuid
from orchestration.orchestrator import DemoOrchestrator

def test_safety_logs_written(tmp_path, monkeypatch):
    log_path = tmp_path / "orch.log"
    monkeypatch.setenv("MACRS_ORCH_LOG_PATH", str(log_path))

    orch = DemoOrchestrator(artifact_dir=str(tmp_path / "artifacts"))

    # dangerous patch
    patch = (
        "--- a/z.py\n"
        "+++ b/z.py\n"
        "@@ -1,1 +1,2 @@\n"
        " print('z')\n"
        "+exec('boom')\n"
    )

    # run summarize -> triggers safety logging
    review = {"findings": [], "suggested_patches": {"z.py": patch}}
    orch._summarize_patches(review)

    # log must exist
    assert log_path.exists()

    # verify content
    logs = log_path.read_text().strip().splitlines()
    found = False
    for ln in logs:
        rec = json.loads(ln)
        if rec.get("message", "").find("patch_safety_warnings") != -1:
            found = True
            assert "dangerous_code" in rec["message"]
            assert "session_id" in rec["message"]
    assert found, "No patch_safety_warnings log found"