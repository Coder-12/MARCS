# services/fake_llm.py

import asyncio
import hashlib
from typing import Dict, Any, Optional, List
from core.logging import get_logger

logger = get_logger("fake_llm")


class FakeLLMResponse:
    """Structured wrapper for simulated LLM output."""

    def __init__(self, text: str, findings: Optional[List[Dict]] = None, patch: Optional[str] = None):
        self.text = text
        self.findings = findings or []
        self.patch = patch

    def model_dump(self):
        return {
            "text": self.text,
            "findings": self.findings,
            "patch": self.patch
        }


class FakeLLM:
    """
    Deterministic simulation of LLM behavior.
    Used for Phase-0. Replaced in Phase-1 with real OpenAI / Anthropic.
    """

    async def run(self, prompt: str, temperature: float = 0.2) -> FakeLLMResponse:
        # small delay to mimic network/compute latency
        await asyncio.sleep(0.05)

        # deterministically hash the prompt to simulate "reasoning"
        h = hashlib.sha256(prompt.encode()).hexdigest()[:16]

        logger.info("fake_llm_run", prompt_hash=h)

        # heuristic behavior simulation
        text = f"[FAKE-LLM-{h}] Response generated."

        # pattern: if prompt mentions 'security'
        if "security" in prompt.lower():
            findings = [{"id": f"sec-{h[:6]}", "severity": "high", "msg": "Potential security issue"}]
            return FakeLLMResponse(text=text, findings=findings)

        # pattern: if prompt mentions 'style'
        if "style" in prompt.lower():
            findings = [{"id": f"sty-{h[:6]}", "severity": "low", "msg": "Minor style issue"}]
            return FakeLLMResponse(text=text, findings=findings)

        # pattern: if prompt mentions 'patch'
        if "patch" in prompt.lower():
            patch = f"// PATCH-{h[:6]}\n// Suggested fix block"
            return FakeLLMResponse(text=text, patch=patch)

        # default
        return FakeLLMResponse(text=text)