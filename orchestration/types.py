# orchestration/types.py
from __future__ import annotations
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime


class InternalEvent(BaseModel):
    """
    Canonical internal event used by orchestrator / agents.
    Designed to be minimal, stable and extensible.
    """
    # Core metadata
    event_type: str = Field(..., description="Raw GitHub event type (e.g. push, pull_request)")
    action: Optional[str] = Field(None, description="Action (if applicable), e.g. opened, closed")
    repo_full_name: Optional[str] = Field(None, description="repository full name owner/repo")
    sender: Optional[str] = Field(None, description="actor login")
    delivery_id: Optional[str] = Field(None, description="GitHub X-GitHub-Delivery header")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Domain-specific IDs
    pr_number: Optional[int] = Field(None, description="Pull request number if applicable")
    commit_sha: Optional[str] = Field(None, description="Commit SHA / head sha for push or PR")

    # Payloads
    raw_payload: Dict[str, Any] = Field(default_factory=dict, description="Original raw payload")
    normalized: Dict[str, Any] = Field(default_factory=dict, description="Small normalized payload used by agents")

    # trace propagation
    trace_id: Optional[str] = Field(None, description="trace_id (W3C compatible)")
    span_id: Optional[str] = Field(None, description="span id")
    parent_span_id: Optional[str] = Field(None, description="parent span id")

    # routing / priority hints
    priority: Literal["low", "normal", "high"] = Field("normal", description="Processing priority hint")
    tags: Optional[Dict[str, Any]] = Field(default_factory=dict)

    model_config = ConfigDict(
        arbitrary_types_allowed=True
    )