# agents/base_agent.py
from __future__ import annotations
from abc import ABC, abstractmethod
import asyncio
from typing import Any, Dict
from core.logging import get_logger
from core.tracing import start_span, get_trace_id, get_span_id
from agents.results import AgentResult, AgentFinding

logger = get_logger("agents.base")


class BaseAgent(ABC):
    """
    Base agent interface.
    Subclass this to implement domain-specific agents.
    """

    name: str

    def __init__(self, name: str | None = None):
        self.name = name or self.__class__.__name__
        self.logger = get_logger(f"agent.{self.name}")


    @abstractmethod
    async def analyze(self, hunk: Dict[str, Any]) -> AgentResult:
        """Analyze one hunk and return AgentResult."""
        raise NotImplementedError