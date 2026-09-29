import os
import json
import asyncio
import uuid

from orchestration.orchestrator import DemoOrchestrator

HELLO_ORIG = "print('hello')\n"
HELLO_PATCH = """--- a/hello.py
+++ b/hello.py
@@ -1,1 +1,2 @@
 print('hello')
+print('world')
"""


def read_log_lines(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh.read().splitlines() if line.strip()]


# -----------------------------------------------------------------------------------
# 1. Test: Minimal telemetry events are emitted
# -----------------------------------------------------------------------------------

def test_telemetry_basic_flow(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_path = logs_dir / "orch.log"
    monkeypatch.setenv("MACRS_ORCH_LOG_PATH", str(log_path))

    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.py").write_text(HELLO_ORIG)

    orch = DemoOrchestrator(artifact_dir=str(art_dir))

    review = {
        "findings": [],
        "suggested_patches": {"p1": HELLO_PATCH}
    }

    event = {
        "event_id": "evt-tele",
        "repo_root": str(repo),
        "review": review
    }

    async def do_run():
        return await orch.run_review(event)

    asyncio.run(do_run())

    # ensure log file exists
    assert log_path.exists()

    logs = read_log_lines(log_path)

    # required events exist
    events = {entry["message"]["event"] if isinstance(entry["message"], dict)
              else json.loads(entry["message"])["event"]
              for entry in logs}

    expected = {
        "demo_start",
        "review_requested",
        "review_phase_started",
        "review_loaded_from_event",
        "review_phase_finished",
    }

    assert expected.issubset(events)


# -----------------------------------------------------------------------------------
# 2. Test: apply_success / conflict / error telemetry fires
# -----------------------------------------------------------------------------------

def test_apply_success_logged(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_path = logs_dir / "orch.log"
    monkeypatch.setenv("MACRS_ORCH_LOG_PATH", str(log_path))

    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.py").write_text(HELLO_ORIG)

    orch = DemoOrchestrator(artifact_dir=str(art_dir))

    # apply a valid patch
    res = orch.apply_selected(
        "evt-app",
        str(repo),
        {"hello.py": HELLO_PATCH},
        dry_run=False
    )

    logs = read_log_lines(log_path)

    # find apply_success
    events = [json.loads(e["message"]) if isinstance(e["message"], str) else e["message"]
              for e in logs]

    success = [e for e in events if e["event"] == "apply_success"]
    assert len(success) == 1
    assert success[0]["event_id"] == "evt-app"
    assert "hello.py" in success[0]["applied"]


# -----------------------------------------------------------------------------------
# 3. Test: session_id is the same for all events in a run
# -----------------------------------------------------------------------------------

def test_session_id_constant_across_events(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_path = logs_dir / "orch.log"
    monkeypatch.setenv("MACRS_ORCH_LOG_PATH", str(log_path))

    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.py").write_text(HELLO_ORIG)

    orch = DemoOrchestrator(artifact_dir=str(art_dir))

    review = {"findings": [], "suggested_patches": {"p1": HELLO_PATCH}}
    event = {"event_id": "evt-sid", "repo_root": str(repo), "review": review}

    async def do_run():
        return await orch.run_review(event)

    asyncio.run(do_run())

    logs = read_log_lines(log_path)

    # extract session ids
    ids = set(
        json.loads(entry["message"])["session_id"]
        if isinstance(entry["message"], str)
        else entry["message"]["session_id"]
        for entry in logs
    )

    # all events must share identical session_id
    assert len(ids) == 1


# -----------------------------------------------------------------------------------
# 4. Test: session_summary is emitted
# -----------------------------------------------------------------------------------

def test_session_summary_logged(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_path = logs_dir / "orch.log"
    monkeypatch.setenv("MACRS_ORCH_LOG_PATH", str(log_path))

    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.py").write_text(HELLO_ORIG)

    orch = DemoOrchestrator(artifact_dir=str(art_dir))

    # fake apply + summary
    orch.log_event("session_summary", event_id="evt-x", patch_count=1)

    logs = read_log_lines(log_path)

    events = [json.loads(e["message"]) if isinstance(e["message"], str) else e["message"]
              for e in logs]

    summaries = [e for e in events if e["event"] == "session_summary"]
    assert len(summaries) == 1
    assert summaries[0]["event_id"] == "evt-x"
    assert summaries[0]["patch_count"] == 1