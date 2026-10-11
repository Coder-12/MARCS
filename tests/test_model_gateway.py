import asyncio
from dataclasses import FrozenInstanceError
import json
import time
from typing import Callable

import pytest
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core import model_gateway as module
from core.model_gateway import ModelGateway, project_schema
from core.model_providers import GatewayLimits, ModelError, MockStep, ScriptedMockProvider, TokenUsage, digest
from orchestration.contracts import PatchProposal, Plan, TaskSpec


class StrictAnswer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    text: str = Field(min_length=2, max_length=8, pattern=r"^[a-z]+$")
    count: int = Field(ge=1, le=5)
    labels: list[str] = Field(default_factory=list, max_length=2)
    note: str | None = None


def gateway(steps, *, limits=None, observer=None, sleep=asyncio.sleep):
    provider = ScriptedMockProvider({"unit": steps})
    client = ModelGateway(provider, {"provider":"mock","model":"offline-model"}, limits=limits, observer=observer, sleep=sleep)
    return client, provider


async def structured(client, model=StrictAnswer, **extra):
    return await client.generate_structured(system_prompt="Return JSON", user_message="Task",
        output_schema=model, agent_name="unit", prompt_version="v1", **extra)


async def text(client, *, user="Task", agent="unit", version="v1"):
    return await client.generate_text(system_prompt="Return text", user_message=user, agent_name=agent, prompt_version=version)


@pytest.mark.parametrize("model", [StrictAnswer, TaskSpec, Plan])
def test_schema_projection_deterministic_complete_objects_and_defaults(model):
    before = model.model_json_schema()
    a, b = project_schema(model), project_schema(model)
    assert a == b and model.model_json_schema() == before
    projected = json.loads(a.schema_json)
    def inspect(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            assert "default" not in node
            for value in node.values(): inspect(value)
        elif isinstance(node, list):
            for value in node: inspect(value)
    inspect(projected)
    if model is StrictAnswer:
        assert projected["properties"]["labels"]["type"] == "array"
        assert "null" not in json.dumps(projected["properties"]["labels"])
        assert {"minLength","maxLength","pattern","minimum","maximum","maxItems"} <= set(a.local_only_keywords)
    if model is TaskSpec:
        assert "unknown" in projected["$defs"]["TaskType"]["enum"]
    if model is Plan:
        assert projected["$defs"]["ValidationCommand"]["properties"]["argv"]["items"] == {"type":"string"}


@pytest.mark.asyncio
@pytest.mark.parametrize("model,data", [
    (StrictAnswer,{"text":"ok","count":2,"labels":[],"note":None}),
    (TaskSpec,{"raw_input":"Fix parser","task_type":"bug_fix","goal":"Fix it","constraints":[],"success_criteria":[]}),
    (Plan,{"steps":["Test"],"files_to_inspect":["src/a.py"],"files_to_modify":[],"validation_commands":[{"argv":["python","-m","pytest"]}],"assumptions":[]}),
])
async def test_original_model_type_and_local_contract_roundtrip(model, data):
    client, provider = gateway([MockStep(json.dumps(data))])
    before = json.dumps(data)
    result = await structured(client, model)
    assert type(result) is model and result == model.model_validate(data)
    assert json.dumps(data) == before and len(provider.invocations) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("content,code", [
    ("not JSON","malformed_json"), ('```json\n{"text":"ok","count":1}\n```',"malformed_json"),
    ('{"text":"ok","count":1}{}',"malformed_json"), ('{"text":"ok","count":1} trailing',"malformed_json"),
    ('[]',"malformed_json"), ('{"text":"ok","text":"no","count":1}',"malformed_json"),
    ('{"text":"ok","count":NaN}',"malformed_json"), ('{"text":"ok","count":1e999}',"malformed_json"),
    ('{"text":"ok","count":"1"}',"schema_validation"), ('{"text":"ok","count":true}',"schema_validation"),
    ('{"text":"ok"}',"schema_validation"), ('{"text":"ok","count":1,"extra":true}',"schema_validation"),
    ('{"text":"X","count":1}',"schema_validation"), ('{"text":"toolongtext","count":1}',"schema_validation"),
    ('{"text":"UPPER","count":1}',"schema_validation"), ('{"text":"ok","count":0}',"schema_validation"),
    ('{"text":"ok","count":1,"labels":["a","b","c"]}',"schema_validation"),
    ('{"text":"ok","count":1,"labels":null}',"schema_validation"),
    ('{"text":"\\ud800","count":1}',"invalid_encoding"),
])
async def test_no_json_rescue_coercion_or_local_constraint_bypass(content, code):
    events = []
    client, provider = gateway([MockStep(content), MockStep('{"text":"ok","count":1}')], observer=events.append)
    with pytest.raises(ModelError) as error: await structured(client)
    assert error.value.code == code and len(provider.invocations) == 1
    assert events[0].attempts == 1 and events[0].outcome == code and events[0].output_hash is None
    assert content not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("model,data", [
    (TaskSpec,{"raw_input":"x","goal":"y","task_type":"invalid"}),
    (Plan,{"files_to_inspect":["../outside"]}), (Plan,{"files_to_modify":["/absolute.py"]}),
    (Plan,{"validation_commands":[{"argv":[]}]}), (Plan,{"validation_commands":[{"argv":["python","x\x00"]}]}),
    (Plan,{"validation_commands":[{"argv":["pytest"],"shell":True}]}),
])
async def test_existing_domain_contracts_stay_authoritative(model, data):
    client, provider = gateway([MockStep(json.dumps(data))])
    with pytest.raises(ModelError, match="schema_validation"): await structured(client, model)
    assert len(provider.invocations) == 1


class MappingAnswer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    values: dict[str,int]


class TupleAnswer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    value: tuple[str,int]


class CustomSchema(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", json_schema_extra={"pattern":"not-a-local-validator"})
    value: str


class CallableAnswer(BaseModel):
    model_config=ConfigDict(strict=True,extra="forbid")
    function:Callable[[],str]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [PatchProposal,MappingAnswer,TupleAnswer,CustomSchema,CallableAnswer,BaseModel,str])
async def test_unsupported_structure_fails_preflight_without_provider_call(model):
    client, provider = gateway([MockStep("never used")])
    with pytest.raises(ModelError, match="unsupported_schema"): await structured(client, model)
    assert provider.invocations == ()


@pytest.mark.parametrize("limits", [{"max_schema_bytes":32},{"max_schema_depth":1},{"max_schema_nodes":2}])
def test_schema_bounds_are_explicit(limits):
    with pytest.raises(ModelError, match="unsupported_schema"): project_schema(Plan, GatewayLimits(**limits))


@pytest.mark.asyncio
@pytest.mark.parametrize("step,code", [
    (MockStep("",refusal="no"),"refusal"), (MockStep("partial",finish_reason="length"),"incomplete_generation"),
    (MockStep("partial",finish_reason="content_filter"),"content_filtered"), (MockStep(""),"empty_content"),
    (MockStep("\ud800"),"invalid_encoding"),
])
async def test_text_response_failure_not_success_or_retry(step,code):
    client, provider = gateway([step,MockStep("unused")])
    with pytest.raises(ModelError) as error: await text(client)
    assert error.value.code == code and len(provider.invocations) == 1


@pytest.mark.asyncio
async def test_retry_metadata_missing_usage_and_observer_failure():
    waits, events = [], []
    async def sleep(delay): waits.append(delay)
    client, provider = gateway([MockStep(error_code="rate_limited",retry_after=1000),
        MockStep(error_code="transient_provider"),MockStep("completed")],
        sleep=sleep, observer=events.append, limits={"backoff_base_s":0.25,"backoff_cap_s":0.5})
    assert await text(client) == "completed"
    assert waits == [0.5,0.5] and len(provider.invocations) == 3
    event = events[0]
    assert event.attempts == 3 and event.outcome == "success" and event.usage is None
    assert event.requested_model == event.actual_model == "offline-model" and event.request_id == "mock-request"
    assert event.output_hash == digest("completed")
    with pytest.raises(FrozenInstanceError): event.attempts = 99
    client, _ = gateway([MockStep("ok")], observer=lambda event: (_ for _ in ()).throw(RuntimeError("private observer error")))
    assert await text(client) == "ok"


@pytest.mark.asyncio
async def test_retry_exhaustion_no_final_sleep_and_permanent_error_no_retry():
    waits = []
    async def sleep(delay): waits.append(delay)
    client, provider = gateway([MockStep(error_code="transient_provider")]*3, sleep=sleep)
    with pytest.raises(ModelError) as error: await text(client)
    assert error.value.code == "retry_exhausted" and error.value.cause_code == "transient_provider"
    assert len(provider.invocations) == 3 and len(waits) == 2
    client, provider = gateway([MockStep(error_code="authorization"),MockStep("unused")])
    with pytest.raises(ModelError, match="authorization"): await text(client)
    assert len(provider.invocations) == 1


@pytest.mark.asyncio
async def test_timeouts_total_deadline_and_cancellation_metadata():
    events = []
    client, provider = gateway([MockStep("late",delay_s=0.1)]*3, observer=events.append,
        limits={"attempt_timeout_s":0.01,"total_timeout_s":0.1,"backoff_base_s":0.0})
    with pytest.raises(ModelError, match="retry_exhausted"): await text(client)
    assert len(provider.invocations) == 3 and events[0].attempts == 3
    client, provider = gateway([MockStep(error_code="rate_limited",retry_after=1)],limits={"total_timeout_s":0.01})
    with pytest.raises(ModelError, match="request_timeout"): await text(client)
    assert len(provider.invocations) == 1
    events = []
    client, provider = gateway([MockStep("late",delay_s=1)],observer=events.append)
    task = asyncio.create_task(text(client)); await asyncio.sleep(0.01); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert events[0].outcome == "cancelled" and events[0].output_hash is None


@pytest.mark.asyncio
async def test_concurrent_calls_keep_script_result_and_metadata_identity():
    events = []
    scripts = {"a":[MockStep("result a",delay_s=0.02,usage=TokenUsage(2,3,5))],"b":[MockStep("result b")]}
    provider = ScriptedMockProvider(scripts)
    client = ModelGateway(provider,{"provider":"mock","model":"offline-model"},observer=events.append)
    results = await asyncio.gather(text(client,user="input a",agent="a"),text(client,user="input b",agent="b"))
    assert results == ["result a","result b"]
    by_agent = {event.agent_name:event for event in events}
    assert by_agent["a"].output_hash == digest("result a") and by_agent["a"].usage == TokenUsage(2,3,5)
    assert by_agent["b"].usage is None and by_agent["a"].input_hash != by_agent["b"].input_hash
    assert "input a" not in repr(provider.invocations) and "result a" not in repr(events)
    assert not hasattr(client,"last_call")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bytes","invalid_utf8","schema","capability"])
async def test_budget_and_input_fail_before_invocation(kind):
    provider = ScriptedMockProvider({"unit":[MockStep("unused")]})
    client = ModelGateway(provider,{"provider":"mock","model":"offline-model","supports_structured":kind!="capability"},limits={"max_input_bytes":100})
    with pytest.raises(ModelError):
        if kind == "bytes": await text(client,user="α"*100)
        elif kind == "invalid_utf8": await text(client,user="\ud800")
        else: await structured(client,Plan)
    assert provider.invocations == ()


@pytest.mark.parametrize("config,limits,code", [
    ({"provider":"unknown","model":"x"},None,"unsupported_capability"),
    ({"provider":"mock","model":"x","profile":"responses"},None,"unsupported_capability"),
    ({"provider":"mock","model":""},None,"invalid_config"),
    ({"provider":"mock","model":"x","temperature":0},None,"invalid_config"),
    ({"provider":"mock","model":"x"},{"max_attempts":0},"invalid_config"),
    ({"provider":"mock","model":"x"},{"total_timeout_s":float("nan")},"invalid_config"),
])
def test_explicit_configuration_no_fallback(config,limits,code):
    with pytest.raises(ModelError,match=code): ModelGateway(ScriptedMockProvider({}),config,limits=limits)


@pytest.mark.asyncio
async def test_gateway_provider_ownership_and_closed_reuse():
    client, provider = gateway([MockStep("ok"),MockStep("next")])
    await client.aclose()
    with pytest.raises(ModelError,match="closed"): await text(client)
    other = ModelGateway(provider,{"provider":"mock","model":"offline-model"},owns_provider=True)
    assert await text(other) == "ok"
    await other.aclose()
    with pytest.raises(ModelError,match="closed"): await provider.complete(None)


@pytest.mark.asyncio
async def test_deadline_includes_output_hash_and_failure_has_no_success_hash(monkeypatch):
    events=[]
    original=module.digest
    def slow_output(value):
        if isinstance(value,str): time.sleep(0.03)
        return original(value)
    monkeypatch.setattr(module,"digest",slow_output)
    client,provider=gateway([MockStep("completed")],limits={"total_timeout_s":0.01},observer=events.append)
    with pytest.raises(ModelError,match="request_timeout"): await text(client)
    assert len(provider.invocations)==1 and events[0].outcome=="request_timeout"
    assert events[0].output_hash is None


@pytest.mark.asyncio
async def test_unexpected_provider_failure_and_async_observer_do_not_fake_success():
    class Broken(ScriptedMockProvider):
        async def complete(self,request): raise RuntimeError("private provider detail")
    events=[]
    client=ModelGateway(Broken({}),{"provider":"mock","model":"offline-model"},observer=events.append)
    with pytest.raises(ModelError,match="provider_failure") as error: await text(client)
    assert "private provider detail" not in str(error.value) and events[0].outcome=="provider_failure"
    class AsyncObserver:
        async def __call__(self,event): pass
    with pytest.raises(ModelError,match="invalid_config"):
        ModelGateway(ScriptedMockProvider({}),{"provider":"mock","model":"x"},observer=AsyncObserver())


def test_opaque_nonrecursive_projection_and_strict_nested_models_only():
    class LooseChild(BaseModel):
        value:int
    class Parent(BaseModel):
        model_config=ConfigDict(strict=True,extra="forbid")
        child:LooseChild
    class Recursive(BaseModel):
        model_config=ConfigDict(strict=True,extra="forbid")
        child:"Recursive | None"=None
    Recursive.model_rebuild()
    for model in (Parent,Recursive):
        with pytest.raises(ModelError,match="unsupported_schema"): project_schema(model)


@pytest.mark.asyncio
async def test_same_caller_concurrency_reserves_script_before_await():
    events=[]
    client,provider=gateway([MockStep("first",delay_s=0.02),MockStep("second")],observer=events.append)
    assert await asyncio.gather(text(client,user="first input"),text(client,user="second input"))==["first","second"]
    by_hash={event.input_hash:event for event in events}
    first,second=provider.invocations
    assert first.input_hash!=second.input_hash
    assert by_hash[first.input_hash].output_hash==digest("first")
    assert by_hash[second.input_hash].output_hash==digest("second")
