# core/llm.py
from __future__ import annotations
import os
import json
import time
import asyncio
import hashlib
import random
from typing import Optional, Dict, Any, List, Union

from core.logging import get_logger

logger = get_logger("core.llm")

# Environment knobs
LLM_BACKEND = os.environ.get("MACRS_LLM_BACKEND", "fake").lower().strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
LLM_HTTP_URL = os.environ.get("MACRS_LLM_HTTP_URL", "")  # For simple http API (optional)
LLM_MAX_RETRIES = int(os.environ.get("MACRS_LLM_RETRIES", "3"))

# Simple type aliases
Message = Dict[str, Any]
Response = Dict[str, Any]


# -------------------------
# Helpers
# -------------------------
def _prompt_hash(*parts: Union[str, Message, List[Message]]) -> str:
    m = hashlib.sha1()
    for p in parts:
        if p is None:
            continue
        if isinstance(p, (list, dict)):
            m.update(json.dumps(p, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        else:
            m.update(str(p).encode("utf-8"))
    return m.hexdigest()


async def _sleep_backoff(attempt: int) -> None:
    base = 0.05 * (2 ** attempt)
    jitter = random.random() * 0.05
    await asyncio.sleep(base + jitter)


# -------------------------
# Adapter
# -------------------------
class LLMAdapter:
    """
    Pluggable LLM adapter with backends:
      - fake  : deterministic test responses (default)
      - openai: calls OpenAI (if openai package and API key are present)
      - http  : POSTs to a configured LLM_HTTP_URL (simple JSON)
    """

    def __init__(self, backend: Optional[str] = None):
        self.backend = (backend or LLM_BACKEND).lower().strip()
        logger.info("llm_init", backend=self.backend)

        # lazy imports
        self._openai = None
        self._aiohttp = None

    # public API
    async def generate(
        self,
        prompt: Optional[str] = None,
        *,
        messages: Optional[List[Message]] = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        stop: Optional[List[str]] = None,
        model: Optional[str] = None,
        timeout: int = 30,
    ) -> Response:
        """
        Return a standard response dict: { "id": "...", "choices": [ { "text": "...", "message": {...} } ], "usage": {...} }
        This is intentionally simple and modeled after OpenAI's chat/completion for easy adaptation.
        """
        attempt = 0
        last_exc = None
        while attempt < LLM_MAX_RETRIES:
            try:
                if self.backend == "fake":
                    return self._fake_response(prompt=prompt, messages=messages, temperature=temperature, model=model, max_tokens=max_tokens)
                elif self.backend == "openai":
                    return await asyncio.to_thread(self._call_openai_sync, prompt, messages, max_tokens, temperature, stop, model, timeout)
                elif self.backend == "http":
                    return await self._call_http(prompt, messages, max_tokens, temperature, stop, model, timeout)
                else:
                    logger.warning("llm_unknown_backend", backend=self.backend)
                    # fallback: fake
                    return self._fake_response(prompt=prompt, messages=messages, temperature=temperature, model=model, max_tokens=max_tokens)
            except Exception as e:
                last_exc = e
                logger.warning("llm_call_failed", backend=self.backend, attempt=attempt, error=str(e))
                attempt += 1
                if attempt < LLM_MAX_RETRIES:
                    await _sleep_backoff(attempt)
                else:
                    break
        logger.error("llm_all_retries_failed", backend=self.backend, error=str(last_exc))
        raise last_exc or RuntimeError("LLM call failed")

    async def generate_text(self, *args, **kwargs) -> str:
        """
        Convenience: returns the top text output.
        """
        resp = await self.generate(*args, **kwargs)
        # Support different shapes: choices[].text or choices[].message.content
        choices = resp.get("choices", [])
        if not choices:
            return ""
        c0 = choices[0]
        if "text" in c0:
            return c0["text"]
        if "message" in c0:
            content = c0["message"].get("content") if isinstance(c0["message"], dict) else None
            return content or ""
        return ""

    # -------------------------
    # Fake deterministic backend (for tests / unit)
    # -------------------------
    def _fake_response(self, prompt: Optional[str], messages: Optional[List[Message]], temperature: float, model: Optional[str], max_tokens: int) -> Response:
        # deterministic seed from prompt/messages; but if temperature > 0, include randomness
        h = _prompt_hash(prompt, messages, model)
        # produce deterministic pseudo-random text based on the hash + temperature
        seed = int(h[:8], 16)
        if temperature and temperature > 0.0:
            seed ^= int(time.time())  # introduce time-based noise when temperature > 0
        rnd = random.Random(seed)
        # create a short reply based on tokens
        words = ["ok", "found", "issue", "patch", "suggestion", "line", "replace", "remove", "add"]
        nwords = max(6, min(200, int(max_tokens / 10)))
        text = " ".join(rnd.choice(words) for _ in range(min(nwords, 50)))
        # craft an OpenAI-like response
        return {
            "id": f"fake-{h[:10]}",
            "object": "text_completion",
            "model": model or "fake-model",
            "choices": [
                {
                    "text": text,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop"
                }
            ],
            "usage": {"prompt_tokens": len((prompt or "").split()), "completion_tokens": len(text.split()), "total_tokens": len((prompt or "").split()) + len(text.split())}
        }

    # -------------------------
    # OpenAI sync helper (called in executor)
    # -------------------------
    def _call_openai_sync(self, prompt, messages, max_tokens, temperature, stop, model, timeout):
        try:
            import openai
        except Exception as e:
            raise RuntimeError("openai package not installed") from e

        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not configured")

        openai.api_key = OPENAI_API_KEY
        # support chat-style or completion-style
        if messages:
            # prefer chat completion
            resp = openai.ChatCompletion.create(model=model or "gpt-4o-mini", messages=messages, max_tokens=max_tokens, temperature=temperature, stop=stop, request_timeout=timeout)
            # normalize
            return {"id": resp.get("id"), "choices": [{"message": {"role": resp["choices"][0]["message"]["role"], "content": resp["choices"][0]["message"]["content"]}}], "usage": getattr(resp, "usage", {})}
        else:
            resp = openai.Completion.create(model=model or "text-davinci-003", prompt=prompt or "", max_tokens=max_tokens, temperature=temperature, stop=stop, request_timeout=timeout)
            return {"id": resp.get("id"), "choices": [{"text": resp["choices"][0]["text"]}], "usage": getattr(resp, "usage", {})}

    # -------------------------
    # Simple HTTP backend (POST JSON)
    # -------------------------
    async def _call_http(self, prompt, messages, max_tokens, temperature, stop, model, timeout) -> Response:
        try:
            import aiohttp
        except Exception as e:
            raise RuntimeError("aiohttp not installed for http LLM backend") from e

        if not LLM_HTTP_URL:
            raise RuntimeError("MACRS_LLM_HTTP_URL not configured")

        payload = {"model": model, "prompt": prompt, "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stop": stop}
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.post(LLM_HTTP_URL, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"LLM HTTP backend returned {resp.status}: {text}")
                return await resp.json()


# -------------------------
# Singleton factory
# -------------------------
_llm_instance: Optional[LLMAdapter] = None


def get_llm() -> LLMAdapter:
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = LLMAdapter()
    return _llm_instance