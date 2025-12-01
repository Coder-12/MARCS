# tests/test_file_extractor_hardening.py
import os
import sys
import asyncio
import tempfile
import types
import builtins
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)  # type: ignore[arg-type]

from services.file_extractor import extract_files_from_event, fetch_http, load_local_file, MAX_FILE_CHARS

class DummyEvent:
    def __init__(self, delivery_id="evt", event_type="push", repo_full_name=None, payload=None):
        self.delivery_id = delivery_id
        self.event_type = event_type
        self.repo_full_name = repo_full_name
        self.payload = payload or {}

@pytest.mark.asyncio
async def test_direct_payload_files_returned():
    evt = DummyEvent(repo_full_name="me/repo", payload={"files": {"a.py": "print(1)"}})
    out = await extract_files_from_event(evt)
    assert "a.py" in out and out["a.py"].startswith("print(1)")

@pytest.mark.asyncio
async def test_local_repo_loading(tmp_path, monkeypatch):
    # create a fake local repo
    root = tmp_path / "repos"
    repo = root / "me" / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    file_path = repo / "hello.py"
    file_path.write_text("print('hello')")

    monkeypatch.setenv("MACRS_LOCAL_REPO_ROOT", str(root))

    evt = DummyEvent(repo_full_name="me/repo", payload={})
    out = await extract_files_from_event(evt)
    # because we walk root, expect relative path "hello.py" present
    assert "hello.py" in out
    assert "print('hello')" in out["hello.py"]

@pytest.mark.asyncio
async def test_github_fetch_with_retry(monkeypatch):
    # simulate commits with one changed file; monkeypatch fetch_http to fail twice then succeed
    calls = {"n": 0}
    async def fake_fetch(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            # simulate transient failure
            return None
        return "print('from remote')"

    monkeypatch.setattr("services.file_extractor.fetch_http", fake_fetch)

    evt = DummyEvent(repo_full_name="org/repo", payload={"commits": [{"added": [], "modified": ["src/a.py"]}], "ref": "refs/heads/main"})
    out = await extract_files_from_event(evt)
    assert "src/a.py" in out
    assert out["src/a.py"].startswith("print('from remote')")
    assert calls["n"] >= 3

@pytest.mark.asyncio
async def test_fetch_failure_logged_and_missing(monkeypatch):
    async def always_none(url, headers=None, retries=3, timeout=10):
        return None
    monkeypatch.setattr("services.file_extractor.fetch_http", always_none)

    evt = DummyEvent(repo_full_name="org/repo", payload={"commits": [{"added": [], "modified": ["dont_exist.py"]}], "ref": "refs/heads/main"})
    out = await extract_files_from_event(evt)
    assert out == {}  # missing file should result in empty result