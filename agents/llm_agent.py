# agents/llm_agent.py

from __future__ import annotations
from typing import Any, Dict
from agents.base_agent import BaseAgent
from agents.results import AgentResult, AgentFinding
from services.fake_llm import FakeLLM
from core.logging import get_logger
import time

logger = get_logger("agent.llm")


class LLMAgent(BaseAgent):
    """
    General-purpose LLM-backed agent.
    Phase-0: uses FakeLLM.
    Phase-1: will call actual LLM endpoints.
    """

    def __init__(self, purpose: str):
        super().__init__(name=f"llm:{purpose}")
        self.purpose = purpose
        self.llm = FakeLLM()

    async def analyze(self, hunk: Dict[str, Any]) -> AgentResult:
        start = time.time()
        prompt_context = {
            "purpose": self.purpose,
            "hunk": hunk,
        }
        prompt = f"{self.purpose}\n\nContext:\n{prompt_context}"
        out = await self.llm.run(prompt)

        logger.info("llm_agent_start", purpose=self.purpose)

        findings = []
        # convert fake LLM findings -> AgentFinding
        for f in (out.findings or []):
            findings.append(
                AgentFinding(
                    id=f.get("id"),
                    severity=f.get("severity", "medium"),
                    message=f.get("msg", ""),
                    suggested_patch=f.get("suggested_patch"),
                    confidence=f.get("confidence", 0.5),
                )
            )

        # if llm returned a patch field (top-level)
        if out.patch:
            # create a finding representing patch suggestion
            findings.append(
                AgentFinding(
                    id=f"patch-{hash(out.patch) % (10 ** 6)}",
                    severity="info",
                    message="Suggested patch from LLMAgent",
                    suggested_patch=out.patch,
                    confidence=0.5
                )
            )

        ar = AgentResult(
            agent_name=self.name,
            findings=findings,
            raw=out.model_dump(),
            time_ms=int((time.time() - start) * 1000)
        )

        logger.info("llm_agent_complete", agent=self.name, findings=len(findings))

        return ar