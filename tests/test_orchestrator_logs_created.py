# tests/test_orchestrator_logs_created.py
import os
import json
import tempfile
from orchestration.orchestrator import DemoOrchestrator

HELLO_ORIG = "print('hello')\n"
HELLO_PATCH = """--- a/hello.py
+++ b/hello.py
@@ -1,1 +1,2 @@
 print('hello')
+print('world')
"""

def test_orchestrator_writes_log(tmp_path, monkeypatch):
    # set a temp logs path via env to isolate test
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_path = str(logs_dir / "orchestrator.log")
    monkeypatch.setenv("MACRS_ORCH_LOG_PATH", log_path)

    # create artifact dir
    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()

    # prepare repo
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.py").write_text(HELLO_ORIG)

    orch = DemoOrchestrator(artifact_dir=str(art_dir))

    # run review provided by event (so no reviewer necessary)
    review = {"findings": [], "suggested_patches": {"p1": HELLO_PATCH}}
    event = {"event_id": "evt-test-log", "repo_root": str(repo), "review": review}

    # preview-only flow (should log demo_start and preview events)
    got = None
    async def run_preview():
        nonlocal got
        got = await orch.run_review(event)

    import asyncio
    asyncio.run(run_preview())

    # ensure log file exists
    assert os.path.exists(log_path)
    # read and assert at least one JSON line with "event" or message
    found = False
    with open(log_path, "r", encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
                # simple assertion: must have ts and level
                if "ts" in obj and ("message" in obj or "fields" in obj):
                    found = True
                    break
            except Exception:
                continue
    assert found, "No valid JSON log entry found in orchestrator log"