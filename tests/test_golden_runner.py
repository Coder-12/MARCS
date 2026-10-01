# tests/test_golden_runner.py
import pytest
from services.golden_runner import run_case, load_golden_cases, run_all
from services import golden_runner
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

@pytest.mark.asyncio
async def test_run_all_returns_list():
    results = await run_all("data/golden/v1/test_event.json", dry_run=True)
    assert isinstance(results, list)
    assert len(results) >= 1


@pytest.mark.parametrize("ok", [True, False])
def test_cli_runs_without_current_event_loop(monkeypatch, capsys, ok):
    # Closing a prior asyncio.run loop leaves no current loop on Python 3.12.
    asyncio.run(asyncio.sleep(0))
    calls = []

    async def fake_run_all(path_glob, dry_run):
        calls.append((path_glob, dry_run))
        return [{"id": "case", "ok": ok}]

    monkeypatch.setattr(golden_runner, "run_all", fake_run_all)
    assert golden_runner.cli("cases/*.json", dry_run=True) == (0 if ok else 1)
    assert calls == [("cases/*.json", True)]
    output = capsys.readouterr().out
    assert f"Golden runner: {int(ok)}/1 passed" in output
    assert '"id": "case"' in output
