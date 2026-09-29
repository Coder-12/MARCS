import os
import pytest
import asyncio

from agents.llm_patch_generator import LLMPatchGenerator
from agents.llm_patch_generator import MAX_PATCH_CHARS

# We will monkeypatch LLMClient.chat_complete to avoid real API calls.


class FakeLLMGood:
    """Returns a minimal valid patch JSON."""
    async def chat_complete(self, messages, temperature=0.0, max_tokens=512):
        return {
            "choices": [{
                "message": {
                    "content": '{"file":"test.py", "patch":"*** PATCH ***"}'
                }
            }]
        }


class FakeLLMBadJSON:
    """Returns garbage content that cannot be parsed."""
    async def chat_complete(self, messages, temperature=0.0, max_tokens=512):
        return {"choices": [{"message": {"content": "NOT_JSON at all"}}]}


class FakeLLMTooLong:
    """Returns a patch exceeding MAX_PATCH_CHARS."""
    async def chat_complete(self, messages, temperature=0.0, max_tokens=512):
        long_patch = "X" * (MAX_PATCH_CHARS + 5000)
        return {"choices": [{"message": {"content": f'{{"file":"test.py", "patch":"{long_patch}"}}'}}]}


@pytest.mark.asyncio
async def test_generate_patch_for_finding_good(monkeypatch):
    gen = LLMPatchGenerator()
    # Inject fake good LLM
    monkeypatch.setattr(gen, "llm", FakeLLMGood())

    finding = {"id": "f1", "message": "msg", "explanation": "exp"}

    patch = await gen.generate_patch_for_finding(
        repo="me/repo",
        event_id="evt1",
        filename="test.py",
        file_text="print('hi')",
        finding=finding
    )

    assert patch == "*** PATCH ***"


@pytest.mark.asyncio
async def test_generate_patch_for_finding_bad_json(monkeypatch):
    gen = LLMPatchGenerator()
    monkeypatch.setattr(gen, "llm", FakeLLMBadJSON())

    finding = {"id": "f1", "message": "msg", "explanation": "exp"}

    patch = await gen.generate_patch_for_finding(
        repo="me/repo",
        event_id="evt1",
        filename="test.py",
        file_text="print('hi')",
        finding=finding
    )

    assert patch is None  # should reject invalid JSON


@pytest.mark.asyncio
async def test_generate_patch_for_finding_too_long(monkeypatch):
    gen = LLMPatchGenerator()
    monkeypatch.setattr(gen, "llm", FakeLLMTooLong())

    finding = {"id": "f1", "message": "msg", "explanation": "exp"}

    patch = await gen.generate_patch_for_finding(
        repo="me/repo",
        event_id="evt1",
        filename="test.py",
        file_text="print('hi')",
        finding=finding
    )

    assert patch is None  # rejects overly long patches


@pytest.mark.asyncio
async def test_generate_patches_for_review_missing_files(monkeypatch):
    gen = LLMPatchGenerator()
    monkeypatch.setattr(gen, "llm", FakeLLMGood())

    review = {
        "event_id": "evt1",
        "repo": "me/repo",
        "findings": [{"id": "f1", "message": "msg"}],
        "metadata": {}  # NO FILES
    }

    patches = await gen.generate_patches_for_review(review, files=None)

    assert patches == {}  # cannot generate patches without files


@pytest.mark.asyncio
async def test_generate_patches_for_review_basic(monkeypatch):
    gen = LLMPatchGenerator()
    monkeypatch.setattr(gen, "llm", FakeLLMGood())

    review = {
        "event_id": "evt1",
        "repo": "me/repo",
        "findings": [{"id": "f1", "message": "msg"}],
        "metadata": {}
    }

    files = {"test.py": "print('hi')"}

    patches = await gen.generate_patches_for_review(review, files=files)

    # Only 1 patch expected
    assert list(patches.values()) == ["*** PATCH ***"]