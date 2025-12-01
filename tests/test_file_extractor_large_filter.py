# tests/test_file_extractor_large_filter.py
import os
import pytest
import asyncio
from services.file_extractor import extract_files_from_event

@pytest.mark.asyncio
async def test_direct_payload_large_file_truncated(monkeypatch, tmp_path):
    # Ensure local repo root empty to avoid local-scan logic
    monkeypatch.setenv("MACRS_LOCAL_REPO_ROOT", "")
    # create big payload string
    big = "X" * 30000
    evt = type("Evt", (), {})()
    evt.repo_full_name = "me/repo"
    evt.payload = {"files": {"big.py": big}}
    out = await extract_files_from_event(evt)
    assert "big.py" in out
    # default truncate_to = 5000
    assert len(out["big.py"]) == int(os.environ.get("MACRS_LARGE_FILE_TRUNCATE_TO", "5000"))

@pytest.mark.asyncio
async def test_direct_payload_large_file_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("MACRS_LOCAL_REPO_ROOT", "")
    big = "Y" * 30000
    evt = type("Evt", (), {})()
    evt.repo_full_name = "me/repo"
    evt.payload = {"files": {"big2.py": big}}
    # call with return_findings to inspect large_files metadata
    out = await extract_files_from_event(evt, return_findings=True)
    # Default truncation is enabled, so ensure metadata exists
    assert "large_files" in out
    # If you'd like to test skip behavior explicitly, set truncate to 0 via env:
    monkeypatch.setenv("MACRS_LARGE_FILE_TRUNCATE_TO", "0")
    out2 = await extract_files_from_event(evt, return_findings=True)
    # Now large_files metadata should show skipped True for big2.py
    assert out2["large_files"]["big2.py"]["skipped"] is True