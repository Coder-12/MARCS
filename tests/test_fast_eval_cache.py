# tests/test_fast_eval_cache.py
import pytest
import asyncio
from services.fast_eval_cache import FastEvalCache


@pytest.mark.asyncio
async def test_fast_eval_cache_basic_set_get():
    cache = FastEvalCache()

    key = "event123"
    val = {"tp": 2, "fp": 0}

    await cache.set(key, val)
    res = await cache.get(key)

    assert res is not None
    assert res["tp"] == 2
    assert "cached_at" in res
    assert isinstance(res["cached_at"], float)


@pytest.mark.asyncio
async def test_fast_eval_cache_miss():
    cache = FastEvalCache()
    res = await cache.get("does_not_exist")
    assert res is None


@pytest.mark.asyncio
async def test_fast_eval_cache_invalidate_event():
    cache = FastEvalCache()

    await cache.set("ev1:caseA", {"x": 1})
    await cache.set("ev1:caseB", {"x": 2})
    await cache.set("ev2:caseC", {"x": 3})

    await cache.invalidate_event("ev1")

    assert await cache.get("ev1:caseA") is None
    assert await cache.get("ev1:caseB") is None
    assert await cache.get("ev2:caseC") is not None


@pytest.mark.asyncio
async def test_fast_eval_cache_clear():
    cache = FastEvalCache()

    await cache.set("k1", {"a": 10})
    await cache.set("k2", {"a": 20})

    await cache.clear()

    assert await cache.get("k1") is None
    assert await cache.get("k2") is None
    stats = await cache.stats()
    assert stats["size"] == 0