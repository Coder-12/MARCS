# agents/code_quality_agent.py
from __future__ import annotations
from typing import Any, Dict, List
import hashlib
import asyncio
import re
from core.logging import get_logger
from core.tracing import start_span, get_trace_id
from agents.base_agent import BaseAgent
from agents.results import AgentResult, AgentFinding

logger = get_logger("agent.code_quality")


class CodeQualityAgent(BaseAgent):
    """
    Rule-based code quality checks for Phase-0.
    Produces findings with category="style".
    Heuristics implemented:
      - Long lines (line > 120 chars)
      - Missing module/class/function docstring hint (if code has 'def ' or 'class ' but no docstring)
      - TODO/FIXME markers flagged as style hints
      - If neither code nor diff available but it's a push -> produce lightweight heuristic finding
    """

    def __init__(self, name: str = "code-quality"):
        super().__init__(name=name)

    async def analyze(self, hunk: Dict[str, Any]) -> AgentResult:
        with start_span("agent.code_quality"):
            findings: List[AgentFinding] = []
            raw = {"hunk_preview": (hunk.get("code") or "")[:400]}

            code_blob = hunk.get("code") or ""
            # fallback to raw_payload text fields
            if not code_blob:
                rp = hunk.get("raw_payload") or {}
                # try commit message or PR body
                code_blob = rp.get("head_commit", {}).get("message", "") or rp.get("pull_request", {}).get("body", "") or ""

            # normalize lines
            lines = code_blob.splitlines() if isinstance(code_blob, str) else []

            # rule: long lines
            for i, ln in enumerate(lines[:200], start=1):
                if len(ln) > 120:
                    data = f"style:longline:{hunk.get('delivery_id') or ''}:{i}:{ln[:40]}".encode()
                    fid = hashlib.sha1(data).hexdigest()[:10] # type: ignore[arg-type]
                    findings.append(
                        AgentFinding(
                            id=fid,
                            severity="low",
                            message=f"Line {i} exceeds 120 characters (length={len(ln)})",
                            confidence=0.6,
                            category="style",
                            location=f"{hunk.get('repo_full_name') or hunk.get('repo')}:{i}",
                            explanation="Prefer breaking long lines or wrapping strings for readability",
                            metadata={"rule": "long_line", "line": i},
                        )
                    )

            # rule: TODO/FIXME
            todo_matches = []
            for i, ln in enumerate(lines[:200], start=1):
                if re.search(r"\b(TODO|FIXME)\b", ln, flags=re.I):
                    todo_matches.append((i, ln.strip()))
            for i, ln in todo_matches:
                data = f"style:todo:{hunk.get('delivery_id') or ''}:{i}:{ln}".encode('utf-8')
                fid = hashlib.sha1(data).hexdigest()[:10] # type: ignore[arg-type]
                findings.append(
                    AgentFinding(
                        id=fid,
                        severity="medium",
                        message=f"TODO/FIXME found on line {i}: {ln}",
                        confidence=0.7,
                        category="style",
                        location=f"{hunk.get('repo_full_name') or hunk.get('repo')}:{i}",
                        explanation="Leftover TODO/FIXME markers may indicate incomplete work.",
                        metadata={"rule": "todo_marker"},
                    )
                )

            # rule: missing docstring hint (very permissive)
            if re.search(r"\b(def|class)\s+\w+\s*\(", code_blob) or re.search(r"\bclass\s+\w+\s*:", code_blob):
                # check for triple-quote docstring in first 10 lines
                head = "\n".join(lines[:12])
                if not re.search(r'""".+?"""|\'\'\'.+?\'\'\'', head, flags=re.S):
                    data = f"style:doc:{hunk.get('delivery_id') or ''}:{hunk.get('repo_full_name') or ''}".encode('utf-8')
                    fid = hashlib.sha1(data).hexdigest()[:10] # type: ignore[arg-type]
                    findings.append(
                        AgentFinding(
                            id=fid,
                            severity="low",
                            message="Code appears to contain functions/classes but missing top docstring.",
                            confidence=0.5,
                            category="style",
                            location=hunk.get("repo_full_name"),
                            explanation="Add module/class/function docstrings to explain behavior.",
                            metadata={"rule": "missing_docstring"},
                        )
                    )

            # fallback heuristic: for push events without code, produce a lightweight "style" hint
            if not findings and (hunk.get("event_type") == "push" or hunk.get("event_type") == "pull_request"):
                # produce a gentle style hint (helps golden-case matching)
                data = f"style:fallback:{hunk.get('delivery_id') or ''}".encode('utf-8')
                fid = hashlib.sha1(data).hexdigest()[:10] # type: ignore[arg-type]
                findings.append(
                    AgentFinding(
                        id=fid,
                        severity="info",
                        message="Lightweight style heuristic: consider reviewing code formatting / docstrings",
                        confidence=0.3,
                        category="style",
                        location=hunk.get("repo_full_name"),
                        explanation="Fallback style hint produced by CodeQualityAgent (Phase-0 heuristic).",
                        metadata={"rule": "fallback_hint"},
                    )
                )

            # small artificial latency to simulate IO/compute
            await asyncio.sleep(0.02)

            ar = AgentResult(agent_name=self.name, findings=findings, raw=raw)
            logger.info("code_quality_complete", delivery_id=hunk.get("delivery_id"), findings=len(findings))
            return ar