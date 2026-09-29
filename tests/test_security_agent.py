# tests/test_security_agent.py
import os
import sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
from agents.security_agent import SecurityAgent

@pytest.mark.asyncio
async def test_secret_detection_in_commit_message():
    hunk = {
        "event_type": "push",
        "delivery_id": "g2",
        "repo_full_name": "me/repo",
        "code": "",
        "raw_payload": {"head_commit": {"message": "Add secret token = abc123"}},
    }
    ag = SecurityAgent()
    res = await ag.analyze(hunk)
    assert any(f.category == "security" for f in res.findings)
    assert any("secret" in f.message.lower() or "token" in f.message.lower() for f in res.findings)