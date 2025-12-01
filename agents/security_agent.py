# agents/security_agent.py
from __future__ import annotations
from typing import Any, Dict, List
import hashlib
import asyncio
import re
from core.logging import get_logger
from core.tracing import start_span
from agents.base_agent import BaseAgent
from agents.results import AgentResult, AgentFinding

logger = get_logger("agent.security")


class SecurityAgent(BaseAgent):
    """
    Simple heuristic security checks:
      - Detect tokens, secrets, 'password', 'apikey', 'secret=' in commit messages or code blobs
      - Regex-based detection for likely secrets (hex tokens, long base64-like tokens)
    """

    SECRET_PATTERNS = [
        r"(?:token|secret|passwd|password|api_key|apikey|access_token)\s*[:=]\s*['\"]?([A-Za-z0-9\-_]{6,})",
        r"ghp_[A-Za-z0-9]{36}",                  # GitHub token pattern (example)
        r"[A-Za-z0-9\-_]{30,}",                  # long tokens
    ]

    def __init__(self, name: str = "security"):
        super().__init__(name=name)

    async def analyze(self, hunk: Dict[str, Any]) -> AgentResult:
        with start_span("agent.security"):
            findings: List[AgentFinding] = []
            raw = {"hunk_preview": (hunk.get("code") or "")[:400]}

            code_blob = hunk.get("code") or ""
            if not code_blob:
                rp = hunk.get("raw_payload") or {}
                code_blob = rp.get("head_commit", {}).get("message", "") or rp.get("pull_request", {}).get("body", "") or ""

            # search for secrets with patterns
            checks = []
            for pat in self.SECRET_PATTERNS:
                for m in re.finditer(pat, code_blob, flags=re.I):
                    matched = m.group(0)
                    # group 1 maybe secret value
                    val = m.group(1) if m.groups() else None
                    checks.append((pat, matched, val))

            # dedupe checks
            seen = set()
            for idx, (pat, matched, val) in enumerate(checks):
                key = f"{matched}"
                if key in seen:
                    continue
                seen.add(key)

                data = f"sec:{hunk.get('delivery_id') or ''}:{idx}:{matched}".encode('utf-8')
                fid = hashlib.sha1(data).hexdigest()[:12] # type: ignore[arg-type]
                findings.append(
                    AgentFinding(
                        id=fid,
                        severity="high",
                        message=f"Possible secret or credential detected: {matched[:120]}",
                        confidence=0.85,
                        category="security",
                        location=hunk.get("repo_full_name"),
                        explanation="Potential credential or secret exposed in commit message or diff. Rotate secrets and use vault.",
                        metadata={"pattern": pat, "match": matched, "value_sample": (val or "")[:40]},
                    )
                )

            # small latency
            await asyncio.sleep(0.02)

            logger.info("security_complete", delivery_id=hunk.get("delivery_id"), findings=len(findings))
            return AgentResult(agent_name=self.name, findings=findings, raw=raw)