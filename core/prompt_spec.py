"""Immutable named prompts with literal system instructions and user-only variables.

User templates allow named placeholders and escaped literal braces only. Values
are never reinterpreted. Delimiters/hashes do not prevent prompt injection or make
data confidential. Version changes are an authoring responsibility, not a registry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import string
from typing import Mapping

from core.model_providers import ModelError, digest, text_bytes

MAX_TEMPLATE_BYTES = 16384
MAX_VARIABLE_BYTES = 65536
MAX_RENDERED_BYTES = 131072
_FORMATTER = string.Formatter()


def _parts(template: str):
    if re.search(r"(?<!\{)\{[^{}]*[:!\.\[\]][^{}]*\}", template):
        raise ModelError("invalid_input")
    try:
        parts = tuple(_FORMATTER.parse(template))
    except ValueError:
        raise ModelError("invalid_input") from None
    for _, name, spec, conversion in parts:
        if name is not None and (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or spec or conversion):
            raise ModelError("invalid_input")
    return parts


@dataclass(frozen=True)
class RenderedPrompt:
    name: str
    version: str
    system_prompt: str = field(repr=False)
    user_message: str = field(repr=False)
    identity_hash: str


@dataclass(frozen=True)
class PromptSpec:
    name: str
    version: str
    system_template: str = field(repr=False)
    user_template: str = field(repr=False)
    required_variables: tuple[str, ...]

    def __post_init__(self):
        for value in (self.name, self.version):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", value):
                raise ModelError("invalid_input")
        size = len(text_bytes(self.system_template, nonempty=True)) + len(text_bytes(self.user_template, nonempty=True))
        if size > MAX_TEMPLATE_BYTES:
            raise ModelError("input_budget")
        if type(self.required_variables) is not tuple or any(not isinstance(name, str) for name in self.required_variables):
            raise ModelError("invalid_input")
        placeholders = {name for _, name, _, _ in _parts(self.user_template) if name is not None}
        if len(set(self.required_variables)) != len(self.required_variables) or placeholders != set(self.required_variables):
            raise ModelError("invalid_input")

    @property
    def specification_hash(self) -> str:
        return digest({"name": self.name, "version": self.version, "system_template": self.system_template,
                       "user_template": self.user_template, "required_variables": sorted(self.required_variables)})

    def render(self, variables: Mapping[str, str]) -> RenderedPrompt:
        if not isinstance(variables, Mapping) or len(variables) != len(self.required_variables):
            raise ModelError("invalid_input")
        values = dict(variables)
        if set(values) != set(self.required_variables):
            raise ModelError("invalid_input")
        for value in values.values():
            if len(text_bytes(value)) > MAX_VARIABLE_BYTES:
                raise ModelError("input_budget")
        parts = _parts(self.user_template)
        # Compute exact emitted UTF-8 size BEFORE concatenating repeated substitutions.
        size = len(text_bytes(self.system_template)) + sum(len(text_bytes(literal)) +
                    (len(text_bytes(values[name])) if name is not None else 0) for literal, name, _, _ in parts)
        if size > MAX_RENDERED_BYTES:
            raise ModelError("input_budget")
        user = "".join(literal + (values[name] if name is not None else "") for literal, name, _, _ in parts)
        identity = digest({"name": self.name, "version": self.version,
                           "system_prompt": self.system_template, "user_message": user})
        return RenderedPrompt(self.name, self.version, self.system_template, user, identity)
