import pytest
from types import SimpleNamespace

from worker.worker import process_event
from services.review_store import get_review

@pytest.mark.asyncio
async def test_event_id_alignment_step030():
    evt = SimpleNamespace(
        delivery_id="evt030",
        event_type="push",
        repo_full_name="me/repo",
        payload={},
        trace_id="t030",
        span_id="s030",
        priority=1,
    )

    await process_event(evt)
    stored = await get_review("evt030")

    assert stored is not None
    assert stored.event_id == "evt030"