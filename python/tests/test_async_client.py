"""Tests for the asynchronous :class:`conduit.AsyncClient`."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from conftest import Recorder, json_response, sse_response

from conduit import AsyncClient
from conduit.exceptions import RateLimitError

BASE_URL = "http://gateway.test:9812"
MESSAGES = [{"role": "user", "content": "hello"}]


def _client(recorder: Recorder) -> AsyncClient:
    """Build an async client wired to a mock transport."""
    return AsyncClient(
        base_url=BASE_URL, api_key="sk-gateway-token", transport=recorder.transport
    )


def test_async_chat_completion() -> None:
    recorder = Recorder()
    recorder.add(
        "POST", "/v1/chat/completions", lambda request: json_response("chat_completion.json")
    )

    async def scenario() -> None:
        async with _client(recorder) as client:
            completion = await client.chat.completions.create(
                model="glm-5.1", messages=MESSAGES
            )
        assert completion.choices[0].message.content == "Hello from Conduit."
        assert completion.usage is not None
        assert completion.usage.total_tokens == 16

    asyncio.run(scenario())


def test_async_chat_stream() -> None:
    recorder = Recorder()
    recorder.add(
        "POST", "/v1/chat/completions", lambda request: sse_response("chat_stream.txt")
    )

    async def scenario() -> None:
        async with _client(recorder) as client:
            chunks = []
            stream = await client.chat.completions.create(
                model="glm-5.1", messages=MESSAGES, stream=True
            )
            async for chunk in stream:
                chunks.append(chunk)
        assert len(chunks) == 4
        assert chunks[1].choices[0].delta.content == "Hello"
        assert chunks[3].choices[0].finish_reason == "stop"

    asyncio.run(scenario())


def test_async_messages_and_headers() -> None:
    recorder = Recorder()
    recorder.add("POST", "/v1/messages", lambda request: json_response("message.json"))

    async def scenario() -> None:
        async with _client(recorder) as client:
            message = await client.messages.create(
                model="glm-5.1", messages=MESSAGES, max_tokens=32
            )
        assert message.content[0].text == "Hello from Conduit."
        assert message.usage is not None
        assert message.usage.input_tokens == 11
        assert message.usage.output_tokens == 5

    asyncio.run(scenario())
    assert recorder.requests[0].headers["anthropic-version"] == "2023-06-01"
    assert recorder.requests[0].headers["x-api-key"] == "sk-gateway-token"


def test_async_messages_stream() -> None:
    recorder = Recorder()
    recorder.add("POST", "/v1/messages", lambda request: sse_response("messages_stream.txt"))

    async def scenario() -> None:
        async with _client(recorder) as client:
            events = []
            stream = await client.messages.create(
                model="glm-5.1", messages=MESSAGES, max_tokens=32, stream=True
            )
            async for event in stream:
                events.append(event)
        assert events[0].type == "message_start"
        assert events[2].data["delta"]["text"] == "Hello"
        assert events[-1].type == "message_stop"

    asyncio.run(scenario())


def test_async_responses_and_count_tokens_and_models() -> None:
    recorder = Recorder()
    recorder.add("POST", "/v1/responses", lambda request: json_response("response.json"))
    recorder.add(
        "POST",
        "/v1/messages/count_tokens",
        lambda request: json_response("count_tokens.json"),
    )
    recorder.add("GET", "/v1/models", lambda request: json_response("models.json"))
    recorder.add("GET", "/health", lambda request: json_response("health.json"))

    async def scenario() -> None:
        async with _client(recorder) as client:
            response = await client.responses.create(model="glm-5.1", input="hello")
            assert response.output_text == "Hello from Conduit."
            assert response.usage is not None
            assert response.usage.total_tokens == 16

            tokens = await client.messages.count_tokens(
                model="glm-5.1", messages=MESSAGES
            )
            assert tokens == 42

            catalog = await client.models.list()
            assert [model.id for model in catalog.data] == ["glm-5.1", "claude-sonnet-5"]

            status = await client.health()
            assert status.status == "ok"

    asyncio.run(scenario())


def test_async_error_mapping() -> None:
    recorder = Recorder()
    recorder.add(
        "POST",
        "/v1/chat/completions",
        lambda request: httpx.Response(
            429,
            json={"error": {"type": "rate_limit_exceeded", "message": "slow down"}},
            headers={"Retry-After": "5"},
        ),
    )

    async def scenario() -> None:
        async with _client(recorder) as client:
            with pytest.raises(RateLimitError) as info:
                await client.chat.completions.create(model="glm-5.1", messages=MESSAGES)
        assert info.value.retry_after == 5.0
        assert info.value.message == "slow down"

    asyncio.run(scenario())
