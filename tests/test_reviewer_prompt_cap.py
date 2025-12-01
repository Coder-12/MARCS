# tests/test_reviewer_prompt_cap.py
import os
import pytest
import asyncio

from services.reviewer import Reviewer
from sanitizers.prompt_cap import PROMPT_TRUNCATION_KEY, DEFAULT_TRUNCATION_NOTE

# ---------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------
class MockLLM:
    model = "mock-model"

    async def chat_complete(self, messages, temperature=0.0, max_tokens=1024):
        # Echo back minimal JSON so parsing always succeeds
        return {
            "choices": [
                {
                    "message": {
                        "content": """{
                            "findings": [],
                            "suggested_patches": {},
                            "summary": "ok"
                        }"""
                    }
                }
            ]
        }


@pytest.mark.asyncio
async def test_prompt_cap_drops_large_files(monkeypatch):
    """
    If total chars exceed prompt cap, large files must be dropped.
    """
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "100")  # very small cap

    files = {
        "small1.py": "a" * 20,
        "small2.py": "b" * 30,
        "big1.py": "x" * 200,  # must be dropped
        "big2.py": "y" * 300,  # must be dropped
    }

    r = Reviewer(llm=MockLLM())
    out = await r.review_repo_files(
        event_id="E1",
        repo="me/repo",
        files=files,
    )

    cap_meta = out["metadata"]["prompt_cap"]

    # at least some dropped files expected
    assert "big1.py" in cap_meta["dropped_files"]
    assert "big2.py" in cap_meta["dropped_files"]

    # included must be SIZE-ASCENDING
    included = cap_meta["included_files"]
    assert included[0] == "small1.py"
    assert included[1] == "small2.py"

    # total chars ≤ 100 + note
    assert cap_meta["total_chars"] <= 100 + len(DEFAULT_TRUNCATION_NOTE)


@pytest.mark.asyncio
async def test_prompt_cap_truncates_single_large_file(monkeypatch):
    """
    If only one file exists, and it's bigger than the cap, it must be truncated in-place.
    """
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "50")

    files = {
        "main.py": "Z" * 200,
    }

    r = Reviewer(llm=MockLLM())
    out = await r.review_repo_files(
        event_id="E2",
        repo="me/repo",
        files=files,
    )

    cap_meta = out["metadata"]["prompt_cap"]

    assert cap_meta["truncated_in_place"] is True
    expected_truncated = 200 - (50 - len(DEFAULT_TRUNCATION_NOTE))
    assert cap_meta["truncated_bytes"] == expected_truncated

    included = cap_meta["included_files"]
    assert included == ["main.py", PROMPT_TRUNCATION_KEY]

    # main.py is present in review files passed through prompt
    assert "main.py" in cap_meta["included_files"]

    # The final included content length must respect cap
    total = cap_meta["total_chars"]
    assert total <= 50 + len(DEFAULT_TRUNCATION_NOTE)


@pytest.mark.asyncio
async def test_prompt_cap_appends_note(monkeypatch):
    """
    Ensure the sentinel note is included when truncation or dropping occurs.
    """
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "30")

    files = {
        "a.txt": "hello world",
        "b.txt": "x" * 500,
    }

    r = Reviewer(llm=MockLLM())
    out = await r.review_repo_files(event_id="E3", repo="me/repo", files=files)

    cap_meta = out["metadata"]["prompt_cap"]

    assert PROMPT_TRUNCATION_KEY in cap_meta["included_files"]
    assert cap_meta["note_added"] is True


@pytest.mark.asyncio
async def test_prompt_cap_no_change_when_under_limit(monkeypatch):
    """
    If total chars <= cap, prompt_cap must not modify anything.
    """
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "10000")

    files = {
        "a.py": "print('ok')" * 3,
        "b.py": "hello",
    }

    r = Reviewer(llm=MockLLM())
    out = await r.review_repo_files(event_id="E4", repo="me/repo", files=files)

    cap_meta = out["metadata"]["prompt_cap"]

    assert cap_meta["dropped_files"] == []
    assert cap_meta["truncated_in_place"] is False
    assert cap_meta["note_added"] is False

    # included files should be unchanged
    included = cap_meta["included_files"]
    assert "a.py" in included
    assert "b.py" in included
    assert PROMPT_TRUNCATION_KEY not in included


@pytest.mark.asyncio
async def test_ordering_is_deterministic(monkeypatch):
    """
    The smallest-first ordering must be deterministic even for equal-sized files.
    """
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "50")

    files = {
        "c.py": "aaa",
        "b.py": "aaa",
        "a.py": "aaa",
        "d.py": "x" * 200,
    }

    r = Reviewer(llm=MockLLM())
    out = await r.review_repo_files(event_id="E5", repo="me/repo", files=files)

    cap_meta = out["metadata"]["prompt_cap"]

    included = cap_meta["included_files"]

    # Lexicographic ordering for same-size files
    assert included[0] == "a.py"
    assert included[1] == "b.py"
    assert included[2] == "c.py"
    assert PROMPT_TRUNCATION_KEY in included


@pytest.mark.asyncio
async def test_metadata_forwarding(monkeypatch):
    """
    Reviewer must forward cap_meta inside metadata → prompt_cap.
    """
    monkeypatch.setenv("MACRS_PROMPT_MAX_CHARS", "40")

    files = {
        "foo.py": "a" * 100,
        "bar.py": "b" * 20,
    }

    r = Reviewer(llm=MockLLM())
    out = await r.review_repo_files(event_id="E6", repo="me/repo", files=files)

    meta = out["metadata"]
    assert "prompt_cap" in meta
    assert isinstance(meta["prompt_cap"], dict)