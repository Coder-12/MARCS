# api/schemas/github.py

from typing import Optional, Literal
from pydantic import BaseModel, Field


# ---------- Common Base ----------
class GitHubBaseEvent(BaseModel):
    delivery_id: str = Field(..., description="GitHub X-GitHub-Delivery header")
    event: str = Field(..., description="GitHub X-GitHub-Event header")
    action: Optional[str] = Field(None, description="Action field if present")


# ---------- Ping Event ----------
class GitHubPingPayload(BaseModel):
    zen: Optional[str] = None
    hook_id: Optional[int] = None


class GitHubPingEvent(GitHubBaseEvent):
    event: Literal["ping"]
    payload: GitHubPingPayload


# ---------- Pull Request Event ----------
class PullRequestInfo(BaseModel):
    number: int
    state: str
    title: str
    merged: Optional[bool] = None


class GitHubPullRequestPayload(BaseModel):
    action: str
    pull_request: PullRequestInfo


class GitHubPullRequestEvent(GitHubBaseEvent):
    event: Literal["pull_request"]
    payload: GitHubPullRequestPayload


# ---------- Push Event ----------
class GitHubPushPayload(BaseModel):
    ref: str
    before: str
    after: str
    repository: dict


class GitHubPushEvent(GitHubBaseEvent):
    event: Literal["push"]
    payload: GitHubPushPayload


# ---------- Fallback (unknown event) ----------
class GitHubUnknownEvent(GitHubBaseEvent):
    payload: dict