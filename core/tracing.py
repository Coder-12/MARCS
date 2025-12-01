# core/tracing.py
from __future__ import annotations
import contextvars
import secrets
import typing as t
from contextlib import contextmanager
from datetime import datetime

# Context vars for propagation
_ctx_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
_ctx_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("span_id", default=None)
_ctx_parent_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("parent_span_id", default=None)

def _make_trace_id() -> str:
    # 16 bytes -> 32 hex chars
    return secrets.token_hex(16)

def _make_span_id() -> str:
    # 8 bytes -> 16 hex chars
    return secrets.token_hex(8)

def get_trace_id() -> str | None:
    return _ctx_trace_id.get()

def get_span_id() -> str | None:
    return _ctx_span_id.get()

def get_parent_span_id() -> str | None:
    return _ctx_parent_span_id.get()

def with_new_trace(trace_id: str | None = None) -> None:
    """Create (or set) a trace id in the current context."""
    if trace_id is None:
        trace_id = _make_trace_id()
    _ctx_trace_id.set(trace_id)
    # also reset span
    _ctx_span_id.set(_make_span_id())
    _ctx_parent_span_id.set(None)

@contextmanager
def start_span(name: str | None = None, parent_span_id: str | None = None):
    """
    Context manager to create a new span id (child).
    Use like:
        with start_span("normalize"):
            ...
    """
    trace_id = get_trace_id()
    if trace_id is None:
        trace_id = _make_trace_id()
        _ctx_trace_id.set(trace_id)

    parent = get_span_id() or parent_span_id
    new_span = _make_span_id()
    token_span = _ctx_span_id.set(new_span)
    token_parent = _ctx_parent_span_id.set(parent)
    start_ts = datetime.utcnow()
    try:
        yield {"trace_id": trace_id, "span_id": new_span, "parent_span_id": parent, "start_ts": start_ts}
    finally:
        # restore previous
        _ctx_span_id.reset(token_span)
        _ctx_parent_span_id.reset(token_parent)