from dataclasses import FrozenInstanceError

import pytest

from core.model_providers import ModelError
from core.prompt_spec import PromptSpec


def spec(version="v1", user="Task: {task}\nSource: {source}"):
    return PromptSpec("synthetic",version,"Fixed system instructions",user,("task","source"))


def test_deterministic_identity_rendering_literal_data_and_role_separation():
    prompt=spec();values={"task":"Fix α", "source":"{system_prompt} {task.__class__} {{braces}}"}
    before=values.copy()
    a,b=prompt.render(values),prompt.render(dict(reversed(list(values.items()))))
    assert a==b and values==before
    assert a.system_prompt=="Fixed system instructions"
    assert a.user_message=="Task: Fix α\nSource: {system_prompt} {task.__class__} {{braces}}"
    assert len(a.identity_hash)==64 and len(prompt.specification_hash)==64
    assert spec("v2").render(values).identity_hash!=a.identity_hash
    assert spec("v2").specification_hash!=prompt.specification_hash
    assert prompt.render({**values,"task":"Changed"}).identity_hash!=a.identity_hash
    with pytest.raises(FrozenInstanceError): prompt.version="v3"
    assert "Fix α" not in repr(a)


@pytest.mark.parametrize("values", [{"task":"x"},{"task":"x","source":"y","extra":"z"},
    {"task":1,"source":"x"},{"task":"\ud800","source":"x"},None,"not a mapping"])
def test_missing_extra_nonstring_and_invalid_utf8_variables_fail(values):
    with pytest.raises(ModelError):spec().render(values)


@pytest.mark.parametrize("template", ["{task.name}","{task[0]}","{task!r}","{task:10}","{task:}","{}","{0}","{task", "{task:{source}}"])
def test_no_executable_formatting_or_alternate_placeholder_grammar(template):
    with pytest.raises(ModelError):PromptSpec("unit","v1","System",template,("task",))


def test_escaped_template_braces_are_literal_and_system_is_not_interpolated():
    prompt=PromptSpec("unit","v1","Literal system {source}",'{{"example": true}} {source}',("source",))
    result=prompt.render({"source":"Untrusted instructions {source}"})
    assert result.system_prompt=="Literal system {source}"
    assert result.user_message=='{"example": true} Untrusted instructions {source}'


@pytest.mark.parametrize("variables", [("unused",), ("source","source"), ["source"]])
def test_variable_declaration_matches_exact_template(variables):
    with pytest.raises(ModelError):PromptSpec("unit","v1","System","{source}",variables)


def test_template_variable_and_expanded_render_bounds_without_cropping():
    with pytest.raises(ModelError,match="input_budget"):PromptSpec("unit","v1","x"*16385,"User",())
    with pytest.raises(ModelError,match="input_budget"):spec().render({"task":"x","source":"α"*32769})
    prompt=PromptSpec("unit","v1","System","{source}"*4,("source",))
    with pytest.raises(ModelError,match="input_budget"):prompt.render({"source":"x"*65536})


def test_invalid_utf8_templates_fail():
    with pytest.raises(ModelError,match="invalid_encoding"):PromptSpec("unit","v1","\ud800","User",())
