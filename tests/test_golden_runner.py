# tests/test_golden_runner.py
import pytest
from services.golden_runner import run_case, load_golden_cases, run_all
import asyncio
import os

@pytest.mark.asyncio
async def test_run_single_minimal_case():
    cases = load_golden_cases("data/golden/v1/test_event.json")
    assert len(cases) == 1
    res = await run_case(cases[0], dry_run=False)
    # runner will try to process_event and then read saved review; if review missing that's acceptable
    assert isinstance(res, dict)
    assert "id" in res

def test_run_all_returns_list():
    loop = asyncio.get_event_loop()
    results = loop.run_until_complete(run_all("data/golden/v1/test_event.json", dry_run=True))
    assert isinstance(results, list)
    assert len(results) >= 1