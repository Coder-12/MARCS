import asyncio
import json

import httpx
import pytest

from core.model_gateway import ModelGateway
from core.model_providers import ENDPOINT, ModelError, OpenAIProvider, TokenUsage
from orchestration.contracts import TaskSpec, PatchProposal


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, delay=0):
        self.chunks, self.delay, self.closed, self.reads = tuple(chunks), delay, False, 0
    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay: await asyncio.sleep(self.delay)
            self.reads += 1
            yield chunk
    async def aclose(self):
        self.closed = True


def envelope(content="completed", **extra):
    return {"id":"chatcmpl-completion-id","model":"actual-model","choices":[{
        "index":0,"finish_reason":"stop","message":{"role":"assistant","content":content}}],**extra}


def response(data, *, status=200, headers=None, delay=0):
    raw = json.dumps(data, ensure_ascii=False).encode() if isinstance(data,dict) else data
    stream = Stream([raw[index:index+17] for index in range(0,len(raw),17)],delay=delay)
    return httpx.Response(status,headers=headers,stream=stream), stream


async def generate(gateway):
    return await gateway.generate_text(system_prompt="System",user_message="User",agent_name="unit",prompt_version="v1")


@pytest.mark.asyncio
async def test_exact_chat_request_schema_metadata_and_borrowed_lifecycle():
    seen, events, streams = [], [], []
    data = {"raw_input":"Fix","task_type":"bug_fix","goal":"Fix","constraints":[],"success_criteria":[]}
    async def handler(request):
        seen.append(request)
        reply, stream = response(envelope(json.dumps(data),usage={"prompt_tokens":7,"completion_tokens":9,"total_tokens":16}),headers={"x-request-id":"req-1"})
        streams.append(stream); return reply
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=True,auth=("unwanted","auth")) as borrowed:
        provider = OpenAIProvider(api_key="offline-test-key",client=borrowed)
        gateway = ModelGateway(provider,{"provider":"openai","model":"requested-model","max_completion_tokens":123},observer=events.append,owns_provider=True)
        result = await gateway.generate_structured(system_prompt="System",user_message="User",output_schema=TaskSpec,agent_name="unit",prompt_version="v1")
        assert type(result) is TaskSpec and result.goal == "Fix"
        request = seen[0]; body = json.loads(request.content)
        assert request.method == "POST" and str(request.url) == ENDPOINT
        assert request.headers["authorization"] == "Bearer offline-test-key"
        assert request.headers["content-type"] == "application/json" and request.headers["accept-encoding"] == "identity"
        assert body["model"] == "requested-model" and body["max_completion_tokens"] == 123
        assert body["n"] == 1 and body["stream"] is False
        assert body["messages"] == [{"role":"system","content":"System"},{"role":"user","content":"User"}]
        assert set(body) == {"model","messages","max_completion_tokens","n","stream","response_format"}
        fmt = body["response_format"]
        assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
        assert fmt["json_schema"]["schema"]["additionalProperties"] is False
        assert events[0].actual_model == "actual-model" and events[0].request_id == "req-1"
        assert events[0].usage == TokenUsage(7,9,16)
        assert streams[0].closed
        await gateway.aclose()
        assert not borrowed.is_closed
        with pytest.raises(ModelError,match="closed"): await generate(gateway)


@pytest.mark.asyncio
async def test_text_request_and_completion_id_is_not_fabricated_request_id():
    async def handler(request):
        body = json.loads(request.content)
        assert "response_format" not in body and "text" not in body
        return response(envelope())[0]
    events = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = ModelGateway(OpenAIProvider(api_key="offline",client=client),{"provider":"openai","model":"requested"},observer=events.append)
        assert await generate(gateway) == "completed"
        assert events[0].request_id is None and events[0].usage is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,code", [
    (lambda data:data.pop("choices"),"provider_protocol"),
    (lambda data:data.update(choices=[]),"provider_protocol"),
    (lambda data:data["choices"].append(data["choices"][0].copy()),"provider_protocol"),
    (lambda data:data["choices"][0].update(index=1),"provider_protocol"),
    (lambda data:data["choices"][0]["message"].update(role="user"),"provider_protocol"),
    (lambda data:data["choices"][0]["message"].update(refusal="refused"),"refusal"),
    (lambda data:data["choices"][0].update(finish_reason="length"),"incomplete_generation"),
    (lambda data:data["choices"][0].update(finish_reason="content_filter"),"content_filtered"),
    (lambda data:data["choices"][0]["message"].update(tool_calls=[{}]),"unexpected_tool_calls"),
    (lambda data:data["choices"][0]["message"].update(tool_calls=0),"provider_protocol"),
    (lambda data:data["choices"][0]["message"].update(content=""),"empty_content"),
    (lambda data:data["choices"][0]["message"].pop("content"),"empty_content"),
    (lambda data:data["choices"][0]["message"].update(content=[{"text":"wrong"}]),"provider_protocol"),
    (lambda data:data.update(usage={"prompt_tokens":True}),"provider_protocol"),
    (lambda data:data.update(usage={"total_tokens":-1}),"provider_protocol"),
    (lambda data:data.update(model="invalid\nmodel"),"provider_protocol"),
])
async def test_http_200_is_not_application_success(mutation,code):
    data = envelope(); mutation(data); seen, streams, events = [], [], []
    async def handler(request):
        seen.append(request); result, stream = response(data,headers={"x-request-id":"req-failed"}); streams.append(stream); return result
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = ModelGateway(OpenAIProvider(api_key="offline",client=client),{"provider":"openai","model":"requested"},observer=events.append)
        with pytest.raises(ModelError) as error: await generate(gateway)
        assert error.value.code == code and len(seen) == 1 and streams[0].closed
        assert events[0].outcome == code and events[0].output_hash is None
        if code == "refusal":
            assert events[0].request_id == "req-failed" and events[0].actual_model == "actual-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,code", [(b'{"broken":',"provider_protocol"),(b"\xff","invalid_encoding"),
    (b'{"choices":[],"choices":[]}',"provider_protocol"),(b'['*40+b']'*40,"provider_protocol")])
async def test_invalid_http_envelope_and_utf8_are_bounded_errors(raw,code):
    streams=[]
    async def handler(request):
        result,stream=response(raw);streams.append(stream);return result
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway=ModelGateway(OpenAIProvider(api_key="offline",client=client),{"provider":"openai","model":"requested"})
        with pytest.raises(ModelError) as error: await generate(gateway)
        assert error.value.code==code and streams[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status",[200,429,500])
@pytest.mark.parametrize("headers",[{}, {"content-length":"1"}])
async def test_success_and_error_bodies_bounded_without_trusting_content_length(status,headers):
    stream=Stream([b"x"*17]*100)
    async def handler(request): return httpx.Response(status,headers=headers,stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway=ModelGateway(OpenAIProvider(api_key="offline",client=client),{"provider":"openai","model":"requested"},limits={"max_response_bytes":64})
        with pytest.raises(ModelError,match="response_size"): await generate(gateway)
        assert stream.closed and stream.reads==4


@pytest.mark.asyncio
async def test_redirect_never_forwards_credentials_and_compression_not_implicitly_decoded():
    for headers in ({"location":"https://invalid.example/collect"},{"content-encoding":"gzip"}):
        seen=[]
        async def handler(request):
            seen.append(request)
            return response({},status=302 if "location" in headers else 200,headers=headers)[0]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=True) as client:
            gateway=ModelGateway(OpenAIProvider(api_key="offline",client=client),{"provider":"openai","model":"requested"})
            with pytest.raises(ModelError,match="provider_protocol"): await generate(gateway)
            assert len(seen)==1 and str(seen[0].url)==ENDPOINT


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code",[(401,"authorization"),(403,"authorization"),(400,"unsupported_capability"),(404,"unsupported_capability"),(422,"unsupported_capability")])
async def test_permanent_http_errors_do_not_retry_or_leak_provider_text(status,code):
    calls=[]
    async def handler(request):
        calls.append(request);return response({"error":{"message":"secret-provider-body"}},status=status)[0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway=ModelGateway(OpenAIProvider(api_key="offline",client=client),{"provider":"openai","model":"requested"})
        with pytest.raises(ModelError) as error: await generate(gateway)
        assert error.value.code==code and "secret-provider-body" not in str(error.value) and len(calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize("first_status",[429,500,502,503,504])
async def test_real_adapter_bounded_retry_owned_only_by_gateway(first_status):
    calls, waits, streams, events=[],[],[],[]
    async def sleep(delay): waits.append(delay)
    async def handler(request):
        calls.append(request)
        result,stream=response({"error":{"message":"retry"}} if len(calls)==1 else envelope(),
            status=first_status if len(calls)==1 else 200,headers={"retry-after":"999","x-request-id":"req-final"})
        streams.append(stream);return result
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway=ModelGateway(OpenAIProvider(api_key="offline",client=client),{"provider":"openai","model":"requested"},
            limits={"backoff_cap_s":0.5},sleep=sleep,observer=events.append)
        assert await generate(gateway)=="completed"
        assert len(calls)==2 and len(waits)==1 and waits[0]<=0.5
        assert all(stream.closed for stream in streams) and events[0].attempts==2


@pytest.mark.asyncio
async def test_http_retry_exhaustion_transport_failure_and_missing_credentials():
    calls, waits, events=[],[],[]
    async def sleep(delay): waits.append(delay)
    async def handler(request):
        calls.append(request);return response({"error":{}},status=429,headers={"x-request-id":"req-rate"})[0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        missing=ModelGateway(OpenAIProvider(client=client),{"provider":"openai","model":"requested"},observer=events.append)
        with pytest.raises(ModelError,match="missing_credentials"): await generate(missing)
        assert calls==[] and events[-1].attempts==0
        gateway=ModelGateway(OpenAIProvider(api_key="offline",client=client),{"provider":"openai","model":"requested"},sleep=sleep,observer=events.append)
        with pytest.raises(ModelError) as error: await generate(gateway)
        assert error.value.code=="retry_exhausted" and error.value.cause_code=="rate_limited"
        assert len(calls)==3 and len(waits)==2 and events[-1].request_id=="req-rate"
    calls=[]
    async def broken(request):
        calls.append(request);raise httpx.ConnectError("private network text",request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as client:
        gateway=ModelGateway(OpenAIProvider(api_key="offline",client=client),{"provider":"openai","model":"requested"},sleep=sleep)
        with pytest.raises(ModelError,match="retry_exhausted") as error: await generate(gateway)
        assert "private network text" not in str(error.value) and len(calls)==3


@pytest.mark.asyncio
async def test_timeout_and_external_cancellation_close_response_stream():
    for cancel in (False,True):
        streams=[]
        async def handler(request):
            result,stream=response(envelope(),delay=0.1);streams.append(stream);return result
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway=ModelGateway(OpenAIProvider(api_key="offline",client=client),{"provider":"openai","model":"requested"},
                limits={"max_attempts":1,"attempt_timeout_s":0.02})
            task=asyncio.create_task(generate(gateway))
            if cancel:
                await asyncio.sleep(0.005);task.cancel()
                with pytest.raises(asyncio.CancelledError): await task
            else:
                with pytest.raises(ModelError,match="retry_exhausted"): await task
            assert len(streams)==1 and streams[0].closed


@pytest.mark.asyncio
async def test_client_credentials_independent_owned_close_and_no_constructor_calls():
    seen=[]
    async def handler(request):
        seen.append(request.headers["authorization"]);return response(envelope())[0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as shared:
        a=OpenAIProvider(api_key="offline-a",client=shared);b=OpenAIProvider(api_key="offline-b",client=shared)
        assert seen==[]
        ga=ModelGateway(a,{"provider":"openai","model":"model-a"})
        gb=ModelGateway(b,{"provider":"openai","model":"model-b"})
        assert await asyncio.gather(generate(ga),generate(gb))==["completed","completed"]
        assert seen==["Bearer offline-a","Bearer offline-b"]
        await a.aclose();assert not shared.is_closed
        assert await generate(gb)=="completed"
    owned=OpenAIProvider(api_key="offline")
    assert not owned._client.is_closed
    await owned.aclose();await owned.aclose()
    assert owned._client.is_closed
    with pytest.raises(ModelError,match="closed"): owned.validate_request(None)


@pytest.mark.asyncio
async def test_preflight_limits_and_unsupported_schema_never_reach_http(caplog):
    seen=[]
    async def handler(request):
        seen.append(request);return response(envelope("unused"))[0]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider=OpenAIProvider(api_key="offline-not-a-secret",client=client)
        gateway=ModelGateway(provider,{"provider":"openai","model":"requested"},limits={"max_input_bytes":10})
        with pytest.raises(ModelError,match="input_budget"):
            await gateway.generate_text(system_prompt="System",user_message="α"*20,agent_name="unit",prompt_version="v1")
        gateway=ModelGateway(provider,{"provider":"openai","model":"requested"})
        with pytest.raises(ModelError,match="unsupported_schema"):
            await gateway.generate_structured(system_prompt="System",user_message="User",output_schema=PatchProposal,agent_name="unit",prompt_version="v1")
        assert seen==[] and "offline-not-a-secret" not in caplog.text and "System" not in caplog.text


@pytest.mark.asyncio
async def test_generated_content_has_independent_byte_bound():
    stream=None
    async def handler(request):
        nonlocal stream
        result,stream=response(envelope("α"*10));return result
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway=ModelGateway(OpenAIProvider(api_key="offline",client=client),{"provider":"openai","model":"requested"},limits={"max_content_bytes":8})
        with pytest.raises(ModelError,match="response_size"): await generate(gateway)
        assert stream.closed
