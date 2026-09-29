# agents/results.py
from __future__ import annotations
from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field
from datetime import datetime, timezone


class Evidence(BaseModel):
    """
    Structured evidence attached to a finding.
    - source: e.g., "commit_message", "diff_hunk", "file.py"
    - line: optional line number
    - snippet: small excerpt
    - rule: optional rule id (e.g. "long_line")
    - score: optional float confidence or relevance
    """
    source: str
    line: Optional[int] = None
    snippet: Optional[str] = None
    rule: Optional[str] = None
    score: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentFinding(BaseModel):
    id: Optional[str] = Field(default=None)
    severity: Optional[Literal["low", "medium", "high", "info"]] = None
    message: str
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    category: Optional[str] = None # e.g., "security", "style", "performance"
    location: Optional[str] = None  # "file.py:12-20" or hunk id
    explanation: Optional[str] = None
    evidence: Optional[List[Any]] = None
    evidence_links: Optional[Dict[str, Any]] = None  # free-form links map
    fingerprint: Optional[str] = None  # canonical fingerprint for the finding
    linked_patches: Optional[List[str]] = None  # list of patch ids or patch stubs that address this finding
    evidence_score: Optional[float] = None
    suggested_patch: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    tests_to_run: Optional[List[str]] = None


class AgentResult(BaseModel):
    agent_name: str
    findings: List[AgentFinding] = Field(default_factory=list)
    raw: Dict[str, Any] = Field(default_factory=dict)  # raw LLM output or tool output
    time_ms: Optional[int] = None


class SynthesizedReview(BaseModel):
    event_id: str
    repo: Optional[str] = None
    summary: Optional[str] = None
    findings: List[AgentFinding] = Field(default_factory=list)
    suggested_patches: Dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)


    @classmethod
    def from_results(cls, event_id: str, repo: str, results: List[AgentResult], trace_id: str = None):
        findings = []
        patches = {}
        for r in results:
            for f in r.findings:
                findings.append(f)
                if f.suggested_patch:
                    patches[f.id] = f.suggested_patch
        return cls(
            event_id=event_id,
            repo=repo,
            summary=f"Aggregated {len(findings)} findings from {len(results)} agent results",
            findings=findings,
            suggested_patches=patches,
            metadata={"trace_id": trace_id},
        )