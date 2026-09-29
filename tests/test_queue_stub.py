import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
from orchestration.queue import enqueue_event, dequeue_event, queue_size
from orchestration.types import InternalEvent


def test_queue_enqueue_dequeue():
    # create dummy event
    evt = InternalEvent(
        event_type="test",
        delivery_id="123",
        repo_full_name="me/repo",
        pr_number=None,
        commit_sha=None,
        payload={},
        normalized={"foo": "bar"},
        priority="low",
    )

    # queue should start empty
    assert queue_size() == 0

    enqueue_event(evt)
    assert queue_size() == 1

    out = dequeue_event()
    assert out.delivery_id == "123"
    assert out.event_type == "test"
    assert out.normalized["foo"] == "bar"

    assert queue_size() == 0