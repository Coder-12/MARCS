# tests/test_llm_adapter.py
import os
import asyncio
import pytest
from core.llm import get_llm, LLMAdapter

@pytest.mark.asyncio
async def test_fake_deterministic_response():
    os.environ["MACRS_LLM_BACKEND"] = "fake"
    llm = get_llm()
    # two identical queries -> identical outputs
    resp1 = await llm.generate(prompt="find issues in this code", max_tokens=50, temperature=0.0)
    resp2 = await llm.generate(prompt="find issues in this code", max_tokens=50, temperature=0.0)
    assert resp1["id"] == resp2["id"]
    txt1 = resp1["choices"][0]["message"]["content"]
    txt2 = resp2["choices"][0]["message"]["content"]
    assert txt1 == txt2
    # different temperature -> likely different output id
    resp3 = await llm.generate(prompt="find issues in this code", max_tokens=50, temperature=0.9)
    assert resp3["id"] != resp1["id"] or resp3["choices"][0]["message"]["content"] != txt1

@pytest.mark.asyncio
async def test_generate_text_helper():
    os.environ["MACRS_LLM_BACKEND"] = "fake"
    llm = get_llm()
    t = await llm.generate_text(prompt="short test", max_tokens=20)
    assert isinstance(t, str)
    assert len(t) > 0