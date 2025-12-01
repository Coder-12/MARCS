# core/llm_client.py
from __future__ import annotations
import os
import asyncio
import json
import time
import logging
from typing import Optional, Dict, Any
import httpx

logger = logging.getLogger("core.llm_client")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")  # override if needed
OPENAI_TIMEOUT = int(os.environ.get("OPENAI_TIMEOUT", "30"))
# concurrency: maximum concurrent LLM calls per process (None => unlimited)
LLM_CONCURRENCY = int(os.environ.get("LLM_CONCURRENCY", "4"))

# retry/backoff params
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "4"))
LLM_BACKOFF_BASE = float(os.environ.get("LLM_BACKOFF_BASE", "0.5"))

# simple prompt length guard
MAX_PROMPT_TOKENS = int(os.environ.get("MAX_PROMPT_TOKENS", "12000"))

class LLMClient:
    """
    Minimal async LLM client using OpenAI chat completions HTTP endpoint.
    Uses httpx async client, supports retries and optional global concurrency semaphore.
    """

    _client: Optional[httpx.AsyncClient] = None
    _semaphore: Optional[asyncio.Semaphore] = None

    def __init__(self, api_key: str = OPENAI_API_KEY, base: str = OPENAI_API_BASE, model: str = OPENAI_MODEL):
        if not api_key:
            logger.warning("OPENAI_API_KEY not set — LLM calls will fail if attempted")
        self.api_key = api_key
        self.base = base.rstrip("/")
        self.model = model
        if LLM_CONCURRENCY and LLM_CONCURRENCY > 0:
            self.__class__._semaphore = self.__class__._semaphore or asyncio.Semaphore(LLM_CONCURRENCY)
        if self.__class__._client is None:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            self.__class__._client = httpx.AsyncClient(base_url=self.base, headers=headers, timeout=OPENAI_TIMEOUT)

    async def close(self):
        if self.__class__._client:
            await self.__class__._client.aclose()
            self.__class__._client = None

    def _sanitize_prompt(self, prompt: str) -> str:
        # placeholder: remove huge blobs, optional additional sanitization
        if len(prompt) > MAX_PROMPT_TOKENS * 4:  # rough char -> token heuristic
            logger.warning("prompt_too_long; truncating")
            return prompt[-(MAX_PROMPT_TOKENS * 4):]  # keep tail (context)
        return prompt

    async def _call_once(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        assert self.__class__._client is not None, "httpx client missing"
        resp = await self.__class__._client.post("/v1/chat/completions", json=payload)
        text = resp.text
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM HTTP error {resp.status_code}: {text}")
        return resp.json()

    async def chat_complete(
        self,
        messages: list,
        temperature: float = 0.0,
        max_tokens: int = 512,
        top_p: float = 1.0,
        stop: Optional[list] = None,
        stream: bool = False,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Call chat completion endpoint (sync-ish). Returns parsed JSON response from backend.
        Does not implement streaming mode here (placeholder).
        """
        # basic payload
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "top_p": float(top_p),
        }
        if stop:
            payload["stop"] = stop
        if seed is not None:
            # some backends support 'seed' or 'seed' via 'temperature' seeds param — include for later usage
            payload["seed"] = seed

        # Prompt sanitization/truncation - for safety
        # convert messages to string length estimate and maybe trim system/user content
        # (simple approach; can be replaced with tokenizer-based truncation)
        for m in payload["messages"]:
            if "content" in m and isinstance(m["content"], str):
                m["content"] = self._sanitize_prompt(m["content"])

        # retries with exponential backoff
        last_exc = None
        for attempt in range(1, LLM_MAX_RETRIES + 1):
            try:
                if self.__class__._semaphore is not None:
                    async with self.__class__._semaphore:
                        logger.debug("llm_call attempt=%d", attempt)
                        return await self._call_once(payload)
                else:
                    logger.debug("llm_call attempt=%d (no semaphore)", attempt)
                    return await self._call_once(payload)
            except (httpx.RequestError, RuntimeError) as exc:
                last_exc = exc
                backoff = LLM_BACKOFF_BASE * (2 ** (attempt - 1))
                jitter = backoff * 0.1
                wait = backoff + (jitter * (0.5 - time.time() % 1))
                logger.warning("llm_call_failed attempt=%d err=%s; backoff=%.2fs", attempt, str(exc), wait)
                await asyncio.sleep(wait)
        # exhausted retries
        logger.error("llm_call_exhausted; last_exc=%s", str(last_exc))
        raise last_exc or RuntimeError("LLM call failed after retries")