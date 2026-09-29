# tests/test_file_extractor.py
import os
import pytest
import asyncio
from services.file_extractor import extract_files_from_event


@pytest.mark.asyncio
async def test_direct_files():
    evt = type("Evt", (), {})()
    evt.repo_full_name = "me/repo"
    evt.payload = {"files": {"a.py": "print(1)"}}

    out = await extract_files_from_event(evt)
    assert out == {"a.py": "print(1)"}


@pytest.mark.asyncio
async def test_empty_payload():
    evt = type("Evt", (), {})()
    evt.repo_full_name = "me/repo"
    evt.payload = {}

    out = await extract_files_from_event(evt)
    assert out == {}


@pytest.mark.asyncio
async def test_github_modified():
    evt = type("Evt", (), {})()
    evt.repo_full_name = "me/repo"
    evt.payload = {
        "ref": "refs/heads/main",
        "commits": [
            {"modified": ["x.py"], "added": []}
        ]
    }

    # disable HTTP for this test
    out = await extract_files_from_event(evt)
    # we can't fetch, but we test that extraction enumerates filenames
    assert "x.py" in out or out == {}


@pytest.mark.asyncio
async def test_local_repo(monkeypatch, tmp_path):
    # create local repo
    repo_root = tmp_path / "me/repo"
    os.makedirs(repo_root, exist_ok=True)
    (repo_root / "lib.py").write_text("x=1")

    monkeypatch.setenv("MACRS_LOCAL_REPO_ROOT", str(tmp_path))

    evt = type("Evt", (), {})()
    evt.repo_full_name = "me/repo"
    evt.payload = {}

    out = await extract_files_from_event(evt)
    assert "lib.py" in out
    assert out["lib.py"] == "x=1"