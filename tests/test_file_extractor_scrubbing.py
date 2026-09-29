import pytest
import asyncio
from types import SimpleNamespace
import os

from services.file_extractor import extract_files_from_event

# helper dummy event
class DummyEvent:
    def __init__(self, repo_full_name=None, payload=None, delivery_id="evt"):
        self.delivery_id = delivery_id
        self.repo_full_name = repo_full_name
        self.payload = payload or {}

@pytest.mark.asyncio
async def test_extract_files_sanitizes_fetch(monkeypatch):
    # monkeypatch fetch_http in the module to simulate remote content with secrets
    async def fake_fetch(url, headers=None, retries=3, timeout=10):
        return "print('ok')\nTOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd\n"
    monkeypatch.setattr("services.file_extractor.fetch_http", fake_fetch)

    evt = DummyEvent(repo_full_name="org/repo", payload={"commits": [{"added": [], "modified": ["src/a.py"]}], "ref": "refs/heads/main"})
    out = await extract_files_from_event(evt)
    # returned mapping should contain sanitized file and not raw token
    assert "src/a.py" in out
    assert "ghp_" not in out["src/a.py"]
    assert "<REDACTED:" in out["src/a.py"]

@pytest.mark.asyncio
async def test_direct_payload_files_sanitized(monkeypatch):
    # direct files in payload must be sanitized too
    evt = DummyEvent(repo_full_name="me/repo", payload={"files": {"x.py": "KEY=AKIAABCDEFGHIJKLMNOPQRS\nprint(1)"}})
    out = await extract_files_from_event(evt)
    assert "x.py" in out
    assert "AKIA" not in out["x.py"]
    assert "<REDACTED:" in out["x.py"]