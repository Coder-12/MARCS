"""Canonical async model I/O for the new workflow; no agents or legacy migration.

Conservative strict schema profile: object/primitive/enum/array/$defs/$ref/anyOf.
Defaults become required without becoming nullable. Length/pattern/format/array
and numeric bounds remain local-only and are reported in SchemaProjection.
oneOf/discriminator, arbitrary mappings, recursive schemas and other structures
are rejected. Original strict Pydantic models remain authoritative.

Retries can duplicate billable provider executions after ambiguous failures;
there is no exactly-once guarantee, JSON repair, persistence or workflow retry.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import json
import re
import time
from typing import Callable, get_args

from pydantic import BaseModel, ValidationError
from pydantic.errors import PydanticUserError

from core.model_providers import (
    GatewayLimits, ModelConfig, ModelError, ModelProvider, ModelRequest, ProviderReply, TokenUsage,
    canonical_json, digest, strict_json, text_bytes,
)

_LOCAL = frozenset(("minLength", "maxLength", "pattern", "format", "minItems", "maxItems",
                    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"))
_ANNOTATIONS = frozenset(("title", "description", "examples", "default"))
_STRUCTURAL = frozenset(("type", "properties", "required", "additionalProperties", "enum", "const", "items", "anyOf", "$ref", "$defs"))


@dataclass(frozen=True)
class SchemaProjection:
    schema_json: str
    local_only_keywords: tuple[str, ...]


def project_schema(model: type[BaseModel], limits: GatewayLimits | None = None) -> SchemaProjection:
    limits = limits or GatewayLimits()
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise ModelError("unsupported_schema")
    checked = set()
    def strict_models(annotation):
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation in checked:
                return
            checked.add(annotation)
            if annotation.model_config.get("strict") is not True or annotation.model_config.get("extra") != "forbid":
                raise ModelError("unsupported_schema")
            if annotation.model_config.get("json_schema_extra") is not None or any(
                "__get_pydantic_json_schema__" in parent.__dict__ for parent in annotation.__mro__ if parent is not BaseModel):
                raise ModelError("unsupported_schema")
            for field in annotation.model_fields.values():
                if field.json_schema_extra is not None:
                    raise ModelError("unsupported_schema")
                strict_models(field.annotation)
        else:
            for child in get_args(annotation):
                strict_models(child)
    strict_models(model)
    try:
        raw = model.model_json_schema(mode="validation")
        encoded = canonical_json(raw)
    except (ValueError, TypeError, RecursionError, PydanticUserError):
        raise ModelError("unsupported_schema") from None
    if len(text_bytes(encoded)) > limits.max_schema_bytes or raw.get("type") != "object":
        raise ModelError("unsupported_schema")
    defs = raw.get("$defs", {})
    removed, visited = set(), 0

    def project(node: dict, depth: int, active: tuple[str, ...] = ()) -> dict:
        nonlocal visited
        visited += 1
        if depth > limits.max_schema_depth or visited > limits.max_schema_nodes or not isinstance(node, dict):
            raise ModelError("unsupported_schema")
        if set(node) - (_STRUCTURAL | _ANNOTATIONS | _LOCAL):
            raise ModelError("unsupported_schema")
        removed.update(set(node) & _LOCAL)
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not re.fullmatch(r"#/\$defs/[A-Za-z0-9_]+", ref):
                raise ModelError("unsupported_schema")
            name = ref.split("/")[-1]
            if name not in defs or name in active or set(node) - (_ANNOTATIONS | {"$ref"}):
                raise ModelError("unsupported_schema")
            # Validate expanded complexity/depth too; preserve the supported reference.
            project(defs[name], depth, (*active, name))
            return {"$ref": ref}
        result = {}
        if "anyOf" in node:
            if set(node) - (_ANNOTATIONS | {"anyOf"}) or not isinstance(node["anyOf"], list) or not 1 <= len(node["anyOf"]) <= 8:
                raise ModelError("unsupported_schema")
            return {"anyOf": [project(child, depth + 1, active) for child in node["anyOf"]]}
        kind = node.get("type")
        if kind not in ("object", "array", "string", "integer", "number", "boolean", "null"):
            raise ModelError("unsupported_schema")
        result["type"] = kind
        applicable = {"type", "$defs", *_ANNOTATIONS, *_LOCAL}
        applicable |= {"properties", "required", "additionalProperties"} if kind == "object" else ({"items"} if kind == "array" else {"enum", "const"})
        if set(node) - applicable:
            raise ModelError("unsupported_schema")
        if kind == "object":
            properties = node.get("properties")
            if node.get("additionalProperties") is not False or not isinstance(properties, dict) or len(properties) > 256:
                raise ModelError("unsupported_schema")
            result.update(properties={key: project(value, depth + 1, active) for key, value in properties.items()},
                          required=list(properties), additionalProperties=False)
        elif kind == "array":
            result["items"] = project(node.get("items"), depth + 1, active)
        elif "enum" in node or "const" in node:
            values = node.get("enum", [node.get("const")])
            if not isinstance(values, list) or not values or len(values) > 256:
                raise ModelError("unsupported_schema")
            result["enum"] = values
        if "$defs" in node:
            if depth != 1 or not isinstance(node["$defs"], dict):
                raise ModelError("unsupported_schema")
            result["$defs"] = {name: project(value, depth + 1, (name,)) for name, value in node["$defs"].items()}
        return result
    projected = canonical_json(project(raw, 1))
    if len(text_bytes(projected)) > limits.max_schema_bytes:
        raise ModelError("unsupported_schema")
    return SchemaProjection(projected, tuple(sorted(removed)))


@dataclass(frozen=True)
class CallMetadata:
    provider: str
    requested_model: str
    actual_model: str | None
    agent_name: str | None
    prompt_version: str | None
    input_hash: str | None
    output_hash: str | None
    attempts: int
    duration_ms: float
    outcome: str
    request_id: str | None
    usage: TokenUsage | None


class ModelGateway:
    """Explicit provider/config. Optional synchronous observer must be nonblocking.

    No last_call state. Gateway borrows its provider unless owns_provider=True;
    closing either gateway mode forbids reuse. Provider ownership is independent
    of the provider's own borrowed/owned HTTP client policy.
    """
    def __init__(self, provider: ModelProvider, config: ModelConfig | dict, *,
                 limits: GatewayLimits | dict | None = None, observer: Callable[[CallMetadata], None] | None = None,
                 sleep: Callable = asyncio.sleep, owns_provider: bool = False):
        if isinstance(config, dict) and (("provider" in config and config["provider"] not in ("mock", "openai")) or
                ("profile" in config and config["profile"] != "chat_completions_v1")):
            raise ModelError("unsupported_capability")
        try:
            self._config = ModelConfig.model_validate(config)
            self._limits = GatewayLimits.model_validate(limits if limits is not None else {})
        except ValidationError:
            raise ModelError("invalid_config") from None
        if (getattr(provider, "name", None) != self.config.provider or
            any(not callable(getattr(provider, name, None)) for name in ("complete", "validate_request", "aclose"))):
            raise ModelError("unsupported_capability")
        if type(owns_provider) is not bool or not callable(sleep) or (observer is not None and (
            not callable(observer) or inspect.iscoroutinefunction(observer) or inspect.iscoroutinefunction(getattr(observer, "__call__", None)))):
            raise ModelError("invalid_config")
        self._provider, self._observer, self._sleep = provider, observer, sleep
        self._owns_provider, self._closed = owns_provider, False

    @property
    def config(self) -> ModelConfig:
        return self._config

    @property
    def limits(self) -> GatewayLimits:
        return self._limits

    async def generate_structured(self, *, system_prompt: str, user_message: str,
                                  output_schema: type[BaseModel], agent_name: str, prompt_version: str) -> BaseModel:
        return await self._generate(system_prompt, user_message, agent_name, prompt_version, output_schema)

    async def generate_text(self, *, system_prompt: str, user_message: str,
                            agent_name: str, prompt_version: str) -> str:
        return await self._generate(system_prompt, user_message, agent_name, prompt_version, None)

    async def _generate(self, system: str, user: str, agent: str, version: str, schema):
        started = time.monotonic()
        deadline = started + self.limits.total_timeout_s
        attempts, input_hash, output_hash, reply = 0, None, None, None
        error_metadata = None
        outcome = "provider_failure"
        safe_agent = safe_version = None
        try:
            if self._closed:
                raise ModelError("closed")
            for value in (agent, version):
                if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", value):
                    raise ModelError("invalid_input")
            safe_agent, safe_version = agent, version
            input_size = len(text_bytes(system, nonempty=True)) + len(text_bytes(user, nonempty=True))
            if input_size > self.limits.max_input_bytes:
                raise ModelError("input_budget")
            projection = None
            if schema is not None:
                if not self.config.supports_structured:
                    raise ModelError("unsupported_capability")
                projection = project_schema(schema, self.limits)
                input_size += len(text_bytes(projection.schema_json))
                if input_size > self.limits.max_input_bytes:
                    raise ModelError("input_budget")
            input_hash = digest({"provider": self.config.provider, "model": self.config.model,
                "agent_name": agent, "prompt_version": version, "system_prompt": system, "user_message": user,
                "schema": json.loads(projection.schema_json) if projection else None})
            request_args = dict(model=self.config.model, system_prompt=system, user_message=user,
                agent_name=agent, prompt_version=version, input_hash=input_hash,
                max_completion_tokens=self.config.max_completion_tokens, max_response_bytes=self.limits.max_response_bytes,
                max_content_bytes=self.limits.max_content_bytes, schema_json=projection.schema_json if projection else None)
            self._provider.validate_request(ModelRequest(**request_args, timeout_s=self.limits.attempt_timeout_s))
            while attempts < self.limits.max_attempts:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModelError("request_timeout")
                attempts += 1
                try:
                    timeout = min(remaining, self.limits.attempt_timeout_s)
                    async with asyncio.timeout(timeout):
                        reply = await self._provider.complete(ModelRequest(**request_args, timeout_s=timeout))
                    if not isinstance(reply, ProviderReply):
                        raise ModelError("provider_protocol")
                    if len(text_bytes(reply.content, nonempty=True, code="provider_protocol")) > self.limits.max_content_bytes:
                        raise ModelError("response_size")
                    if schema is None:
                        result = reply.content
                    else:
                        data = strict_json(reply.content)
                        try:
                            result = schema.model_validate(data)
                        except (ValidationError, ValueError, TypeError):
                            raise ModelError("schema_validation") from None
                        if type(result) is not schema:
                            raise ModelError("schema_validation")
                    if time.monotonic() >= deadline:
                        raise ModelError("request_timeout")
                    validated_hash = digest(result.model_dump(mode="json") if schema is not None else result)
                    if time.monotonic() >= deadline:
                        raise ModelError("request_timeout")
                    output_hash = validated_hash
                    outcome = "success"
                    return result
                except TimeoutError:
                    error = ModelError("request_timeout")
                except ModelError as exc:
                    error = exc
                    error_metadata = exc
                if error.code not in ("rate_limited", "transient_provider", "request_timeout"):
                    raise error
                if time.monotonic() >= deadline:
                    raise ModelError("request_timeout")
                if attempts >= self.limits.max_attempts:
                    raise ModelError("retry_exhausted", cause_code=error.code)
                wait = min(self.limits.backoff_cap_s,
                           max(self.limits.backoff_base_s * 2**(attempts - 1), error.retry_after or 0))
                remaining = deadline - time.monotonic()
                if wait >= remaining:
                    raise ModelError("request_timeout")
                try:
                    async with asyncio.timeout(remaining):
                        await self._sleep(wait)
                except TimeoutError:
                    raise ModelError("request_timeout") from None
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except ModelError as exc:
            outcome = exc.code
            raise
        except Exception:
            raise ModelError("provider_failure") from None
        finally:
            metadata = CallMetadata(self.config.provider, self.config.model,
                reply.actual_model if reply else error_metadata.actual_model if error_metadata else None,
                safe_agent, safe_version, input_hash, output_hash,
                attempts, (time.monotonic() - started) * 1000, outcome,
                reply.request_id if reply else error_metadata.request_id if error_metadata else None,
                reply.usage if reply else error_metadata.usage if error_metadata else None)
            if self._observer is not None:
                try:
                    delivered = self._observer(metadata)
                    if inspect.iscoroutine(delivered):
                        delivered.close()  # Misdeclared async observer: no abandoned coroutine.
                except (Exception, asyncio.CancelledError):
                    pass  # Optional metadata delivery cannot change generation results.

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            if self._owns_provider:
                await self._provider.aclose()

    async def __aenter__(self):
        if self._closed:
            raise ModelError("closed")
        return self

    async def __aexit__(self, *exc):
        await self.aclose()
