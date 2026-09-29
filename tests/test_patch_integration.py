import os
import pytest
import asyncio

from services.patch_integration import try_real_llm_patches
from agents.llm_patch_generator import LLMPatchGenerator


class FakeLLMGood:
    async def chat_complete(self, messages, temperature=0.0, max_tokens=512):
        return {
            "choices": [{
                "message": {"content": '{"file":"test.py", "patch":"*** PATCH ***"}'}
            }]
        }


@pytest.mark.asyncio
async def test_try_real_llm_patches_mode_test(monkeypatch):
    monkeypatch.setenv("MACRS_PATCH_MODE", "test")

    review = {"event_id": "evt1", "repo": "me/repo", "findings": [{"id": "f1"}]}
    context = {"files": {"test.py": "print('hi')"}}

    patches = await try_real_llm_patches(review, context=context)

    # Because MACRS_PATCH_MODE=test → MUST return {}
    assert patches == {}


@pytest.mark.asyncio
async def test_try_real_llm_patches_mode_real(monkeypatch):
    monkeypatch.setenv("MACRS_PATCH_MODE", "real")

    # Inject good LLM
    gen = LLMPatchGenerator()
    monkeypatch.setattr(gen, "llm", FakeLLMGood())
    monkeypatch.setattr("services.patch_integration.LLMPatchGenerator", lambda: gen)

    review = {
        "event_id": "evt1",
        "repo": "me/repo",
        "findings": [{"id": "f1", "message": "m"}]
    }

    context = {"files": {"test.py": "print('hi')"}}

    patches = await try_real_llm_patches(review, context=context)

    assert patches != {}
    assert list(patches.values()) == ["*** PATCH ***"]