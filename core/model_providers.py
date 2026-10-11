"""New-workflow model protocol, offline script, and OpenAI Chat Completions only.

No legacy client migration, ambient credentials, routing, SDK, or adapter retries.
Injected clients are trusted host dependencies, never repository-selected clients.
The production destination is fixed. Output/body diagnostics are never logged.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Literal, Mapping, Protocol, Sequence

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

ENDPOINT = "https://api.openai.com/v1/chat/completions"
_ERRORS = {
    "invalid_config": "invalid gateway configuration", "unsupported_capability": "unsupported provider capability",
    "unsupported_schema": "schema is outside the supported strict projection", "input_budget": "input exceeds byte budget",
    "invalid_input": "invalid model input", "missing_credentials": "OpenAI credentials are required",
    "authorization": "provider rejected authorization", "rate_limited": "provider rate limit",
    "transient_provider": "transient provider failure", "request_timeout": "model call deadline exceeded",
    "provider_protocol": "invalid provider response", "response_size": "provider response exceeds byte budget",
    "invalid_encoding": "invalid UTF-8 evidence", "refusal": "provider explicitly refused generation",
    "incomplete_generation": "generation did not complete", "malformed_json": "expected one strict JSON object",
    "content_filtered": "provider content filter interrupted generation", "unexpected_tool_calls": "unexpected tool-call output",
    "empty_content": "provider returned no completed text",
    "schema_validation": "output violates the original local model", "retry_exhausted": "transient retry budget exhausted",
    "closed": "model boundary is closed", "provider_failure": "unexpected provider infrastructure failure",
}


class ModelError(Exception):
    """Stable bounded diagnostics; no prompt, credential, response, or HTTP body."""

    def __init__(self, code: str, *, retry_after: float | None = None, cause_code: str | None = None):
        if code not in _ERRORS:
            code = "provider_failure"
        self.code = code
        self.retry_after = retry_after if type(retry_after) in (int, float) and math.isfinite(retry_after) and retry_after >= 0 else None
        self.cause_code = cause_code if cause_code in _ERRORS else None
        self.actual_model = self.request_id = self.usage = None
        super().__init__(f"{code}: {_ERRORS[code]}")


def text_bytes(value: object, *, nonempty: bool = False, code: str = "invalid_input") -> bytes:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ModelError(code)
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ModelError("invalid_encoding") from None


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def strict_json(text: str, *, code: str = "malformed_json", max_depth: int = 32) -> dict:
    """Bound nesting before parsing; reject duplicate keys, nonfinite numbers, surrogates."""
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                raise ModelError(code)
        elif char in "]}":
            depth -= 1
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError("nonfinite constant")
    try:
        data = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
        if not isinstance(data, dict):
            raise ValueError("object required")
        def check(value):
            if isinstance(value, str):
                text_bytes(value)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("nonfinite number")
            elif isinstance(value, (dict, list)):
                for child in (list(value.keys()) + list(value.values()) if isinstance(value, dict) else value):
                    check(child)
        check(data)
        return data
    except (ValueError, TypeError, RecursionError):
        raise ModelError(code) from None


class _FrozenModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False, revalidate_instances="always")


class ModelConfig(_FrozenModel):
    provider: Literal["openai", "mock"]
    model: str
    # Caller asserts this fixed API capability profile; no claim about arbitrary models.
    profile: Literal["chat_completions_v1"] = "chat_completions_v1"
    supports_structured: bool = True
    max_completion_tokens: int = Field(default=1024, ge=1, le=16384)

    @field_validator("model")
    @classmethod
    def model_identity(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value):
            raise ValueError("bounded explicit model identity required")
        return value


class GatewayLimits(_FrozenModel):
    max_input_bytes: int = Field(default=65536, ge=1, le=1048576)
    max_response_bytes: int = Field(default=1048576, ge=1, le=4194304)
    max_content_bytes: int = Field(default=262144, ge=1, le=1048576)
    max_schema_bytes: int = Field(default=32768, ge=1, le=65536)
    max_schema_depth: int = Field(default=8, ge=1, le=12)
    max_schema_nodes: int = Field(default=512, ge=1, le=2048)
    attempt_timeout_s: float = Field(default=30.0, gt=0, le=120)
    total_timeout_s: float = Field(default=60.0, gt=0, le=300)
    max_attempts: int = Field(default=3, ge=1, le=5)
    backoff_base_s: float = Field(default=0.25, ge=0, le=5)
    backoff_cap_s: float = Field(default=5.0, ge=0, le=10)


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ModelRequest:
    model: str
    system_prompt: str = field(repr=False)
    user_message: str = field(repr=False)
    agent_name: str
    prompt_version: str
    input_hash: str
    max_completion_tokens: int
    timeout_s: float
    max_response_bytes: int
    max_content_bytes: int
    schema_json: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ProviderReply:
    content: str = field(repr=False)
    actual_model: str | None = None
    request_id: str | None = None
    usage: TokenUsage | None = None


def _identifier(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", value):
        raise ModelError("provider_protocol")
    return value


def _assistant_content(data: dict, max_content_bytes: int) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ModelError("provider_protocol")
    choice = choices[0]
    if "index" in choice and (type(choice["index"]) is not int or choice["index"] != 0):
        raise ModelError("provider_protocol")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ModelError("provider_protocol")
    if message.get("refusal") is not None:
        if not isinstance(message["refusal"], str):
            raise ModelError("provider_protocol")
        if message["refusal"]:
            raise ModelError("refusal")
    if message.get("tool_calls") is not None and not isinstance(message["tool_calls"], list):
        raise ModelError("provider_protocol")
    if message.get("function_call") is not None or message.get("tool_calls"):
        raise ModelError("unexpected_tool_calls")
    reason = choice.get("finish_reason")
    if reason == "content_filter":
        raise ModelError("content_filtered")
    if reason == "length":
        raise ModelError("incomplete_generation")
    if reason != "stop":
        raise ModelError("provider_protocol")
    content = message.get("content")
    if content is None or isinstance(content, str) and not content.strip():
        raise ModelError("empty_content")
    if len(text_bytes(content, nonempty=True, code="provider_protocol")) > max_content_bytes:
        raise ModelError("response_size")
    return content


def parse_reply(data: dict, max_content_bytes: int, request_id: str | None = None) -> ProviderReply:
    actual_model, request_id = _identifier(data.get("model")), _identifier(request_id)
    usage = None
    if data.get("usage") is not None:
        if not isinstance(data["usage"], dict):
            raise ModelError("provider_protocol")
        tokens = [data["usage"].get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")]
        if any(value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1) for value in tokens):
            raise ModelError("provider_protocol")
        usage = TokenUsage(*tokens)
    try:
        return ProviderReply(_assistant_content(data, max_content_bytes), actual_model, request_id, usage)
    except ModelError as exc:
        exc.actual_model, exc.request_id, exc.usage = actual_model, request_id, usage
        raise


class ModelProvider(Protocol):
    name: str
    def validate_request(self, request: ModelRequest) -> None: ...
    async def complete(self, request: ModelRequest) -> ProviderReply: ...
    async def aclose(self) -> None: ...


class OpenAIProvider:
    """Own a fresh client by default; an injected client is borrowed and not closed.

    Injected clients/transports are trusted host/test dependencies. The adapter
    bounds its own body retention; an injected transport controls its own allocations.
    """
    name = "openai"

    def __init__(self, *, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        if api_key is not None and (not isinstance(api_key, str) or any(ord(c) < 33 or ord(c) > 126 for c in api_key)):
            raise ModelError("invalid_config")
        self._key = api_key
        self._owned = client is None
        self._client = client if client is not None else httpx.AsyncClient(trust_env=False, follow_redirects=False)
        self._closed = False

    def validate_request(self, request: ModelRequest) -> None:
        if self._closed or self._client.is_closed:
            raise ModelError("closed")
        if not self._key:
            raise ModelError("missing_credentials")

    async def complete(self, request: ModelRequest) -> ProviderReply:
        self.validate_request(request)
        payload = {"model": request.model, "messages": [
            {"role": "system", "content": request.system_prompt}, {"role": "user", "content": request.user_message}],
            "n": 1, "stream": False, "max_completion_tokens": request.max_completion_tokens}
        if request.schema_json is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "marcs_response", "strict": True, "schema": json.loads(request.schema_json)}}
        try:
            async with self._client.stream("POST", ENDPOINT, json=payload, auth=None,
                    headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json", "Accept-Encoding": "identity"},
                    follow_redirects=False, timeout=request.timeout_s) as response:
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ModelError("provider_protocol")  # No implicit unbounded decompression.
                body = bytearray()
                if response.is_stream_consumed:
                    # Mock/borrowed transports may have already supplied a buffered body.
                    if len(response.content) > request.max_response_bytes:
                        raise ModelError("response_size")
                    body.extend(response.content)
                else:
                    async for chunk in response.aiter_raw():
                        if len(body) + len(chunk) > request.max_response_bytes:
                            raise ModelError("response_size")
                        body.extend(chunk)
                try:
                    text = body.decode("utf-8", errors="strict")
                except UnicodeError:
                    raise ModelError("invalid_encoding") from None
                data = strict_json(text, code="provider_protocol")
                status = response.status_code
                if status != 200:
                    def problem(code, **kwargs):
                        error = ModelError(code, **kwargs)
                        error.request_id = _identifier(response.headers.get("x-request-id"))
                        return error
                    if 300 <= status < 400:
                        raise ModelError("provider_protocol")
                    if not isinstance(data.get("error"), dict):
                        raise ModelError("provider_protocol")
                    if status in (401, 403):
                        raise problem("authorization")
                    if status == 429:
                        try:
                            wait = float(response.headers.get("retry-after", "nan"))
                        except ValueError:
                            wait = None
                        raise problem("rate_limited", retry_after=wait)
                    if status in (500, 502, 503, 504):
                        raise problem("transient_provider")
                    raise problem("unsupported_capability")
                return parse_reply(data, request.max_content_bytes, response.headers.get("x-request-id"))
        except httpx.TimeoutException:
            raise ModelError("request_timeout") from None
        except (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError):
            raise ModelError("transient_provider") from None
        except httpx.HTTPError:
            raise ModelError("provider_protocol") from None

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            if self._owned:
                await self._client.aclose()

    async def __aenter__(self):
        if self._closed:
            raise ModelError("closed")
        return self

    async def __aexit__(self, *exc):
        await self.aclose()


@dataclass(frozen=True)
class MockStep:
    content: str = field(default="", repr=False)
    finish_reason: str = "stop"
    refusal: str | None = field(default=None, repr=False)
    error_code: str | None = None
    retry_after: float | None = None
    delay_s: float = 0.0
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class MockInvocation:
    agent_name: str
    prompt_version: str
    model: str
    input_hash: str


class ScriptedMockProvider:
    """Per-caller FIFO scripts; reserve each step before the first await.

    Invocations retain identity/hashes only. No prompts, outputs, credentials,
    environment, or prevalidated Pydantic objects are recorded.
    """
    name = "mock"

    def __init__(self, scripts: Mapping[str, Sequence[MockStep]]):
        self._scripts = {key: deque(steps) for key, steps in scripts.items()}
        if any(not isinstance(step, MockStep) or type(step.delay_s) not in (int, float) or not math.isfinite(step.delay_s)
               or not 0 <= step.delay_s <= 1 for steps in self._scripts.values() for step in steps):
            raise ModelError("invalid_config")
        self._invocations: list[MockInvocation] = []
        self._closed = False

    @property
    def invocations(self) -> tuple[MockInvocation, ...]:
        return tuple(self._invocations)

    def validate_request(self, request: ModelRequest) -> None:
        if self._closed:
            raise ModelError("closed")

    async def complete(self, request: ModelRequest) -> ProviderReply:
        self.validate_request(request)
        steps = self._scripts.get(request.agent_name)
        if not steps:
            raise ModelError("provider_protocol")
        step = steps.popleft()
        self._invocations.append(MockInvocation(request.agent_name, request.prompt_version, request.model, request.input_hash))
        if step.delay_s:
            await asyncio.sleep(step.delay_s)
        if step.error_code:
            raise ModelError(step.error_code, retry_after=step.retry_after)
        data = {"id": "mock-request", "model": request.model, "choices": [{"finish_reason": step.finish_reason,
                "message": {"role": "assistant", "content": step.content, "refusal": step.refusal}}]}
        if step.usage is not None:
            data["usage"] = {key: getattr(step.usage, key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        if len(text_bytes(canonical_json(data))) > request.max_response_bytes:
            raise ModelError("response_size")
        return parse_reply(data, request.max_content_bytes, "mock-request")

    async def aclose(self) -> None:
        self._closed = True
