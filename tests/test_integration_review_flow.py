# tests/test_integration_review_flow.py
import pytest
import asyncio
from services.reviewer import Reviewer
from services.file_extractor import extract_files_from_event

from tests.test_reviewer_prompt_cap import MockLLM


class DummyEvent:
    def __init__(self, repo, payload):
        self.repo_full_name = repo
        self.payload = payload


def _sorted_files(d):
    return {k: d[k] for k in sorted(d.keys())}


# ---------------------------------------------------------------------
# 1) Direct payload → extractor → reviewer
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_integration_direct_payload_happy_path(monkeypatch):

    monkeypatch.setenv("MACRS_LOCAL_REPO_ROOT", "")
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "32000")

    evt = DummyEvent(
        repo="me/repo",
        payload={
            "files": {
                "a.py": "print('hello')",
                "b.py": "x = 1",
            }
        }
    )

    out = await extract_files_from_event(evt, return_findings=True)

    assert _sorted_files(out["files"]) == {
        "a.py": "print('hello')",
        "b.py": "x = 1",
    }
    assert out["findings"] == {}

    # fix: metadata is always present, but should show no truncation/skip
    assert all(
        (not m["skipped"] and not m["truncated"])
        for m in out["large_files"].values()
    )

    r = Reviewer(llm=MockLLM())
    review = await r.review_repo_files(
        event_id="E-DIR",
        repo="me/repo",
        files=out["files"],
    )

    cap = review["metadata"]["prompt_cap"]
    assert cap["dropped_files"] == []
    assert cap["truncated_in_place"] is False


# ---------------------------------------------------------------------
# 2) Large file → extractor filters → reviewer respects prompt cap
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_integration_large_file_prompt_cap(monkeypatch):

    monkeypatch.setenv("MACRS_LOCAL_REPO_ROOT", "")
    monkeypatch.setenv("MACRS_MAX_FILE_CHARS", "10000")
    monkeypatch.setenv("MACRS_LARGE_FILE_TRUNCATE_TO", "1000")
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "500")

    big = "Z" * 6000

    evt = DummyEvent("me/repo", {"files": {"large.txt": big}})
    out = await extract_files_from_event(evt, return_findings=True)

    meta = out["large_files"]["large.txt"]

    # FIX: file size (6000) < max_chars (10000) → NOT truncated in extractor
    assert meta["truncated"] is False
    assert meta["final_len"] == 6000

    # reviewer WILL truncate because prompt cap = 500 chars
    r = Reviewer(llm=MockLLM())
    review = await r.review_repo_files(
        event_id="E-BIG",
        repo="me/repo",
        files=out["files"],
    )

    cap = review["metadata"]["prompt_cap"]
    assert cap["cap"] == 500
    assert cap["truncated_in_place"] is True
    assert cap["truncated_bytes"] > 0
    assert "__PROMPT_TRUNCATED_NOTE__" in cap["included_files"]


# ---------------------------------------------------------------------
# 3) Binary file
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_integration_binary_file(monkeypatch):

    monkeypatch.setenv("MACRS_LOCAL_REPO_ROOT", "")
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "32000")

    data = "".join(chr(i % 32) for i in range(5000))

    evt = DummyEvent("me/repo", {"files": {"bin.dat": data}})
    out = await extract_files_from_event(evt, return_findings=True)

    assert out["files"] == {}
    assert out["large_files"]["bin.dat"]["skipped"] is True
    assert out["large_files"]["bin.dat"]["reason"] == "binary_content"

    r = Reviewer(llm=MockLLM())
    review = await r.review_repo_files(
        event_id="E-BIN",
        repo="me/repo",
        files=out["files"],
    )

    cap = review["metadata"]["prompt_cap"]
    assert cap["dropped_files"] == []
    assert cap["included_files"] in ([], ["__PROMPT_TRUNCATED_NOTE__"])


# ---------------------------------------------------------------------
# 4) Github-style event with modified files
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_integration_github_event(monkeypatch):

    monkeypatch.setenv("MACRS_LOCAL_REPO_ROOT", "")
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "32000")

    evt = DummyEvent(
        repo="me/repo",
        payload={
            "commits": [{"added": [], "modified": ["src/x.py"]}],
            "ref": "refs/heads/main",
        }
    )

    out = await extract_files_from_event(evt, return_findings=True)

    assert out["files"] == {}

    r = Reviewer(llm=MockLLM())
    review = await r.review_repo_files(
        event_id="E-GH",
        repo="me/repo",
        files=out["files"],
    )

    cap = review["metadata"]["prompt_cap"]
    assert cap["included_files"] in ([], ["__PROMPT_TRUNCATED_NOTE__"])