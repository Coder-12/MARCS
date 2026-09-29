# agents/router_agent.py
from __future__ import annotations
from typing import List, Dict, Any
import asyncio
from agents.base_agent import BaseAgent
from agents.results import AgentResult, AgentFinding
from core.logging import get_logger
from core.tracing import start_span, get_trace_id
from agents.llm_agent import LLMAgent


logger = get_logger("agents.router")


class RouterAgent:
    """
    Router that dispatches hunks to multiple agents concurrently,
    collects AgentResults, and returns aggregated results.
    """

    def __init__(self, agents: List[BaseAgent]):
        # append generic LLM agent for meta reasoning
        self.agents: List[BaseAgent] = agents + [LLMAgent("Meta review and reasoning")]
        self.logger = get_logger("agent.router")

    async def route(self, hunks: List[Dict[str, Any]]) -> List[AgentResult]:
        """
        For each hunk, fan-out to all agents in parallel and collect results.
        Returns list of AgentResult (flattened by agent/hunk).
        """
        outs: List[AgentResult] = []

        # For now, run per-hunk in parallel then per-agent in parallel within that
        async def run_for_hunk(hunk: Dict[str, Any]) -> List[AgentResult]:
            tasks = [agent.analyze(hunk) for agent in self.agents]
            results = await asyncio.gather(*tasks, return_exceptions=False)
            return results

        with start_span("router.route"):
            # run all hunks in sequence or parallel depending on size
            # For modest number of hunks, run them concurrently:
            hunk_tasks = [run_for_hunk(h) for h in hunks]
            nested = await asyncio.gather(*hunk_tasks, return_exceptions=False)
            for group in nested:
                outs.extend(group)

        self.logger.info("router_complete", trace_id=get_trace_id(), total_results=len(outs))
        return outs