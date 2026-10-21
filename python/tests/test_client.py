"""Tests for the synchronous :class:`conduit.Client`."""

from __future__ import annotations

import httpx
import pytest

from conftest import Recorder, json_response, sse_response

from conduit import Client
from conduit.exceptions import ConduitError

BASE_URL = "http://gateway.test:9812"
MESSAGES = [{"role": "user", "content": "hello"}]


def _client(recorder: Recorder, **kwargs) -> Client:
    """Build a client wired to a mock transport."""
    kwargs.setdefault("base_url", BASE_URL)
    kwargs.setdefault("api_key", "sk-gateway-token")
    return Client(transport=recorder.transport, **kwargs)


def test_chat_completion_parsing() -> None:
    recorder = Recorder()
    recorder.add("POST", "/v1/chat/completions", lambda request: json_response("chat_completion.json"))

    with _client(recorder) as client:
        completion = client.chat.completions.create(model="glm-5.1", messages=MESSAGES)

    assert completion.id == "chatcmpl-conduit-0001"
    assert completion.object == "chat.completion"
    assert completion.created == 1730000000
    assert completion.model == "glm-5.1"
    assert len(completion.choices) == 1
    assert completion.choices[0].message.role == "assistant"
    assert completion.choices[0].message.content == "Hello from Conduit."
    assert completion.choices[0].finish_reason == "stop"
    assert completion.text == "Hello from Conduit."
    assert completion.usage is not None
    assert completion.usage.prompt_tokens == 11
    assert completion.usage.completion_tokens == 5
    assert completion.usage.total_tokens == 16


def test_chat_completion_request_body_and_auth_header() -> None:
    recorder = Recorder()
    recorder.add("POST", "/v1/chat/completions", lambda request: json_response("chat_completion.json"))

    with _client(recorder) as client:
        client.chat.completions.create(
            model="glm-5.1", messages=MESSAGES, temperature=0.2, max_tokens=128
        )

    request = recorder.requests[0]
    assert request.headers["authorization"] == "Bearer sk-gateway-token"
    assert request.headers["content-type"] == "application/json"
    body = recorder.body_of(0)
    assert body["model"] == "glm-5.1"
    assert body["messages"] == MESSAGES
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 128
    assert body["stream"] is False


def test_chat_stream_yields_chunks() -> None:
    recorder = Recorder()
    recorder.add("POST", "/v1/chat/completions", lambda request: sse_response("chat_stream.txt"))

    with _client(recorder) as client:
        chunks = list(
            client.chat.completions.create(
                model="glm-5.1", messages=MESSAGES, stream=True
            )
        )

    assert len(chunks) == 4
    assert all(chunk.object == "chat.completion.chunk" for chunk in chunks)
    assert chunks[0].choices[0].delta.role == "assistant"
    assert chunks[1].choices[0].delta.content == "Hello"
    assert chunks[2].choices[0].delta.content == " from Conduit."
    assert chunks[3].choices[0].finish_reason == "stop"
    assert chunks[3].usage is not None
    assert chunks[3].usage.total_tokens == 16
    assert recorder.body_of(0)["stream"] is True


def test_chat_stream_is_incremental() -> None:
    parts = [
        b'data: {"id":"1","object":"chat.completion.chunk","created":1,"model":"m",'
        b'"choices":[{"index":0,"delta":{"content":"a"}}]}\n\n',
        b'data: {"id":"1","object":"chat.completion.chunk","created":1,"model":"m",'
        b'"choices":[{"index":0,"delta":{"content":"b"}}]}\n\n',
        b'data: {"id":"1","object":"chat.completion.chunk","created":1,"model":"m",'
        b'"choices":[{"index":0,"delta":{"content":"c"}}]}\n\n',
    ]
    produced = []

    def body():
        for part in parts:
            produced.append(part)
            yield part

    recorder = Recorder()
    recorder.add(
        "POST",
        "/v1/chat/completions",
        lambda request: httpx.Response(
            200, content=body(), headers={"Content-Type": "text/event-stream"}
        ),
    )

    with _client(recorder) as client:
        stream = client.chat.completions.create(
            model="m", messages=MESSAGES, stream=True
        )
        first = next(stream)
        # The upstream body must not be drained before the first chunk is used.
        assert first.choices[0].delta.content == "a"
        assert produced == [parts[0]]
        rest = list(stream)
        assert [chunk.choices[0].delta.content for chunk in rest] == ["b", "c"]


def test_messages_parsing_and_anthropic_headers() -> None:
    recorder = Recorder()
    recorder.add("POST", "/v1/messages", lambda request: json_response("message.json"))

    with _client(recorder) as client:
        message = client.messages.create(
            model="glm-5.1", messages=MESSAGES, max_tokens=64, system="be brief"
        )

    request = recorder.requests[0]
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.headers["x-api-key"] == "sk-gateway-token"
    body = recorder.body_of(0)
    assert body["max_tokens"] == 64
    assert body["system"] == "be brief"
    assert body["stream"] is False

    assert message.id == "msg_conduit_0001"
    assert message.type == "message"
    assert message.role == "assistant"
    assert message.model == "glm-5.1"
    assert message.stop_reason == "end_turn"
    assert message.content[0].type == "text"
    assert message.content[0].text == "Hello from Conduit."
    assert message.text == "Hello from Conduit."
    assert message.usage is not None
    assert message.usage.input_tokens == 11
    assert message.usage.output_tokens == 5


def test_messages_stream_yields_sse_events() -> None:
    recorder = Recorder()
    recorder.add("POST", "/v1/messages", lambda request: sse_response("messages_stream.txt"))

    with _client(recorder) as client:
        events = list(
            client.messages.create(
                model="glm-5.1", messages=MESSAGES, max_tokens=64, stream=True
            )
        )

    types = [event.type for event in events]
    assert types == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert events[0].data["message"]["id"] == "msg_conduit_0001"
    assert events[2].data["delta"]["text"] == "Hello"
    assert events[3].data["delta"]["text"] == " from Conduit."
    assert events[5].data["delta"]["stop_reason"] == "end_turn"
    assert events[0].raw.startswith("{")


def test_responses_parsing() -> None:
    recorder = Recorder()
    recorder.add("POST", "/v1/responses", lambda request: json_response("response.json"))

    with _client(recorder) as client:
        response = client.responses.create(model="glm-5.1", input="hello")

    assert response.id == "resp_conduit_0001"
    assert response.object == "response"
    assert response.status == "completed"
    assert response.output_text == "Hello from Conduit."
    assert len(response.output) == 1
    assert response.output[0].type == "message"
    assert response.output[0].role == "assistant"
    assert response.output[0].content[0].text == "Hello from Conduit."
    assert response.usage is not None
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 5
    assert response.usage.total_tokens == 16
    assert recorder.body_of(0)["input"] == "hello"


def test_responses_stream_yields_events() -> None:
    recorder = Recorder()
    recorder.add("POST", "/v1/responses", lambda request: sse_response("responses_stream.txt"))

    with _client(recorder) as client:
        events = list(client.responses.create(model="glm-5.1", input="hello", stream=True))

    assert [event.type for event in events] == [
        "response.created",
        "response.output_text.delta",
        "response.output_text.delta",
        "response.completed",
    ]
    assert events[1].data["delta"] == "Hello"
    assert events[2].data["delta"] == " from Conduit."


def test_models_list() -> None:
    recorder = Recorder()
    recorder.add("GET", "/v1/models", lambda request: json_response("models.json"))

    with _client(recorder) as client:
        catalog = client.models.list()

    assert catalog.object == "list"
    assert len(catalog.data) == 2
    assert catalog.data[0].id == "glm-5.1"
    assert catalog.data[0].owned_by == "conduit"
    assert catalog.data[0].protocol == "chat"
    assert catalog.data[0].context_length == 200000
    assert catalog.data[0].context_len == 200000
    assert catalog.data[0].pricing["prompt"] == "0.000001"
    assert "tools" in catalog.data[0].supported_parameters
    assert [model.id for model in catalog] == ["glm-5.1", "claude-sonnet-5"]
    assert len(catalog) == 2


def test_models_list_is_public_without_a_token() -> None:
    recorder = Recorder()
    recorder.add("GET", "/v1/models", lambda request: json_response("models.json"))

    with Client(base_url=BASE_URL, transport=recorder.transport) as client:
        client.models.list()

    assert "authorization" not in recorder.requests[0].headers


def test_count_tokens() -> None:
    recorder = Recorder()
    recorder.add(
        "POST", "/v1/messages/count_tokens", lambda request: json_response("count_tokens.json")
    )

    with _client(recorder) as client:
        tokens = client.messages.count_tokens(
            model="glm-5.1", messages=MESSAGES, system="be brief"
        )

    assert tokens == 42
    body = recorder.body_of(0)
    assert body["model"] == "glm-5.1"
    assert body["system"] == "be brief"
    assert recorder.requests[0].headers["anthropic-version"] == "2023-06-01"


def test_health() -> None:
    recorder = Recorder()
    recorder.add("GET", "/health", lambda request: json_response("health.json"))

    with _client(recorder) as client:
        status = client.health()

    assert status.status == "ok"
    assert status.service == "conduit"


def test_default_headers_and_extra_query() -> None:
    recorder = Recorder()
    recorder.add("GET", "/v1/models", lambda request: json_response("models.json"))

    with _client(
        recorder,
        default_headers={"X-Trace": "abc"},
        extra_query={"tenant": "acme"},
    ) as client:
        client.models.list()

    request = recorder.requests[0]
    assert request.headers["x-trace"] == "abc"
    assert request.url.params["tenant"] == "acme"


def test_base_url_is_normalised() -> None:
    client = Client(base_url="http://gateway.test:9812/", api_key="k")
    assert client.base_url == "http://gateway.test:9812"
    client.close()

    with pytest.raises(ValueError):
        Client(base_url="gateway.test")
    with pytest.raises(ValueError):
        Client(base_url="")


def test_malformed_json_raises_conduit_error() -> None:
    recorder = Recorder()
    recorder.add(
        "POST",
        "/v1/chat/completions",
        lambda request: httpx.Response(200, content=b"not json", headers={"Content-Type": "application/json"}),
    )

    with _client(recorder) as client:
        with pytest.raises(ConduitError):
            client.chat.completions.create(model="glm-5.1", messages=MESSAGES)
