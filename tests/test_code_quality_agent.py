# tests/test_code_quality_agent.py
import os
import sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
import asyncio
from agents.code_quality_agent import CodeQualityAgent
from agents.results import AgentFinding

@pytest.mark.asyncio
async def test_style_long_line_detected():
    code = "a = 'short'\n" + ("x" * 200) + "\n"
    hunk = {
        "event_type": "push",
        "delivery_id": "t-longline",
        "repo_full_name": "me/repo",
        "code": code,
        "raw_payload": {},
    }
    ag = CodeQualityAgent()
    res = await ag.analyze(hunk)
    assert isinstance(res, type(res))
    assert any(f.category == "style" for f in res.findings)
    # at least one long line finding
    assert any("exceeds 120 characters" in f.message for f in res.findings)

@pytest.mark.asyncio
async def test_style_fallback_hint():
    hunk = {"event_type": "push", "delivery_id": "t-fb", "repo_full_name": "me/repo", "code": "", "raw_payload": {}}
    ag = CodeQualityAgent()
    res = await ag.analyze(hunk)
    assert any(f.category == "style" for f in res.findings)