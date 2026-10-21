"""Tests for the error mapping of the Conduit clients."""

from __future__ import annotations

import httpx
import pytest

from conftest import Recorder, json_response

from conduit import Client
from conduit.exceptions import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConduitError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
)

BASE_URL = "http://gateway.test:9812"
MESSAGES = [{"role": "user", "content": "hello"}]


def _client(recorder: Recorder) -> Client:
    """Build a client wired to a mock transport."""
    return Client(
        base_url=BASE_URL, api_key="sk-gateway-token", transport=recorder.transport
    )


def _fail_with(recorder: Recorder, fixture: str, status: int, headers=None) -> None:
    """Register a failing chat completion route."""
    recorder.add(
        "POST",
        "/v1/chat/completions",
        lambda request: httpx.Response(
            status,
            content=json_response(fixture).content,
            headers=headers or {"Content-Type": "application/json"},
        ),
    )


def test_401_maps_to_authentication_error() -> None:
    recorder = Recorder()
    _fail_with(recorder, "error_401.json", 401)

    with _client(recorder) as client:
        with pytest.raises(AuthenticationError) as info:
            client.chat.completions.create(model="glm-5.1", messages=MESSAGES)

    error = info.value
    assert error.status_code == 401
    assert error.error_type == "authentication_error"
    assert error.message == "invalid api key"
    assert str(error) == "invalid api key"
    assert isinstance(error, ConduitError)


def test_403_maps_to_permission_denied_error() -> None:
    recorder = Recorder()
    _fail_with(recorder, "error_403.json", 403)

    with _client(recorder) as client:
        with pytest.raises(PermissionDeniedError) as info:
            client.chat.completions.create(model="glm-5.1", messages=MESSAGES)

    assert info.value.status_code == 403
    assert info.value.error_type == "permission_denied"
    assert "not allowed to use group" in info.value.message


def test_404_maps_to_not_found_error() -> None:
    recorder = Recorder()
    _fail_with(recorder, "error_404.json", 404)

    with _client(recorder) as client:
        with pytest.raises(NotFoundError) as info:
            client.chat.completions.create(model="missing", messages=MESSAGES)

    assert info.value.status_code == 404
    assert info.value.error_type == "not_found"
    assert info.value.message == "model not found"


def test_429_maps_to_rate_limit_error_with_retry_after() -> None:
    recorder = Recorder()
    _fail_with(
        recorder,
        "error_429.json",
        429,
        headers={"Content-Type": "application/json", "Retry-After": "30"},
    )

    with _client(recorder) as client:
        with pytest.raises(RateLimitError) as info:
            client.chat.completions.create(model="glm-5.1", messages=MESSAGES)

    assert info.value.status_code == 429
    assert info.value.error_type == "rate_limit_exceeded"
    assert info.value.retry_after == 30.0


def test_429_without_retry_after_header() -> None:
    recorder = Recorder()
    _fail_with(recorder, "error_429.json", 429)

    with _client(recorder) as client:
        with pytest.raises(RateLimitError) as info:
            client.chat.completions.create(model="glm-5.1", messages=MESSAGES)

    assert info.value.retry_after is None


def test_500_maps_to_server_error() -> None:
    recorder = Recorder()
    _fail_with(recorder, "error_500.json", 500)

    with _client(recorder) as client:
        with pytest.raises(ServerError) as info:
            client.chat.completions.create(model="glm-5.1", messages=MESSAGES)

    assert info.value.status_code == 500
    assert info.value.error_type == "upstream_error"
    assert info.value.message == "upstream service error"


def test_400_maps_to_bad_request_error() -> None:
    recorder = Recorder()
    _fail_with(recorder, "error_500.json", 400)

    with _client(recorder) as client:
        with pytest.raises(BadRequestError) as info:
            client.chat.completions.create(model="glm-5.1", messages=MESSAGES)

    assert info.value.status_code == 400


def test_admin_style_error_envelope_is_parsed() -> None:
    recorder = Recorder()
    recorder.add(
        "POST",
        "/v1/chat/completions",
        lambda request: httpx.Response(
            400, json={"error": "invalid body"}, headers={"Content-Type": "application/json"}
        ),
    )

    with _client(recorder) as client:
        with pytest.raises(BadRequestError) as info:
            client.chat.completions.create(model="glm-5.1", messages=MESSAGES)

    assert info.value.message == "invalid body"
    assert info.value.error_type is None


def test_connection_error_is_wrapped() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    recorder = Recorder()
    recorder.add("GET", "/v1/models", boom)

    with _client(recorder) as client:
        with pytest.raises(APIConnectionError):
            client.models.list()


def test_timeout_error_is_wrapped() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    recorder = Recorder()
    recorder.add("GET", "/v1/models", boom)

    with _client(recorder) as client:
        with pytest.raises(APITimeoutError):
            client.models.list()


def test_streaming_error_raises_before_yielding() -> None:
    recorder = Recorder()
    _fail_with(recorder, "error_429.json", 429)

    with _client(recorder) as client:
        stream = client.chat.completions.create(
            model="glm-5.1", messages=MESSAGES, stream=True
        )
        with pytest.raises(RateLimitError):
            list(stream)
