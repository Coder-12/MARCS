# orchestration/queue.py

from core.logging import get_logger
from orchestration.types import InternalEvent
from core.tracing import get_trace_id, get_span_id

logger = get_logger("event.queue")


# In Phase 1, this will be a Redis queue.
# For now, just append to an in-memory list for testing.
# This will not survive process restart (intended for Phase 0).

_local_queue_buffer = []


def enqueue_event(event: InternalEvent) -> None:
    """
    Phase 0: enqueue stub.
    Phase 1: will push to Redis (streams or list).
    """
    logger.info(
        "enqueue_event",
        event_type=event.event_type,
        delivery_id=event.delivery_id,
        priority=event.priority,
        repo=event.repo_full_name,
        trace_id=get_trace_id(),
        span_id=get_span_id(),
    )

    _local_queue_buffer.append(event)


def dequeue_event() -> InternalEvent | None:
    """
    Phase 0: pop from the local buffer.
    Phase 1: will pop from Redis.
    """
    if not _local_queue_buffer:
        return None

    evt = _local_queue_buffer.pop(0)

    logger.info(
        "dequeue_event",
        event_type=evt.event_type,
        delivery_id=evt.delivery_id,
    )

    return evt


def queue_size() -> int:
    """Utility for tests."""
    return len(_local_queue_buffer)