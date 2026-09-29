# agents/symbolic_agent.py
from __future__ import annotations
from typing import Dict, Any
from agents.base_agent import BaseAgent
from agents.results import AgentResult
from services.symbolic_eval import analyze_hunk
from core.logging import get_logger

logger = get_logger("agent.symbolic")

class SymbolicAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="symbolic")
    async def analyze(self, hunk: Dict[str, Any]) -> AgentResult:
        # synchronous analyze_hunk wrapped in coroutine for uniform interface
        logger.info("symbolic_analyze_start", delivery_id=str(hunk.get("delivery_id")))
        result = analyze_hunk(hunk)
        logger.info("symbolic_analyze_complete", delivery_id=str(hunk.get("delivery_id")), findings=len(result.findings))
        return result