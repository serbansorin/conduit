"""Asynchronous client for the Conduit LLM gateway.

The :class:`AsyncClient` mirrors :class:`conduit.client.Client` one-to-one;
every method is a coroutine and every stream is an async iterator.
"""

from __future__ import annotations

import json
from typing import (
    Any,
    AsyncIterator,
    Dict,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Union,
    overload,
)

import httpx

from ._common import (
    DEFAULT_BASE_URL,
    build_api_error,
    normalize_base_url,
    wrap_transport_error,
)
from ._sse import aiter_sse_messages
from ._version import __version__
from .exceptions import ConduitError
from .models import (
    AnthropicMessage,
    ChatCompletion,
    ChatCompletionChunk,
    HealthStatus,
    ModelList,
    ResponseObject,
    StreamEvent,
    TokenCount,
)
from .types import JSONObject

__all__ = [
    "AsyncClient",
    "AsyncChatNamespace",
    "AsyncCompletionsNamespace",
    "AsyncMessagesNamespace",
    "AsyncResponsesNamespace",
    "AsyncModelsNamespace",
    "ANTHROPIC_VERSION",
]

ANTHROPIC_VERSION = "2023-06-01"
"""The ``anthropic-version`` header value required by Anthropic clients."""

_USER_AGENT = "conduit-python/{0}".format(__version__)


def _decode_json(payload: str) -> JSONObject:
    """Decode a JSON object, raising :class:`ConduitError` on malformed input."""
    try:
        decoded = json.loads(payload)
    except ValueError as exc:
        raise ConduitError("conduit returned malformed JSON: {0}".format(exc)) from exc
    if not isinstance(decoded, Mapping):
        return {"value": decoded}
    return dict(decoded)


class _AsyncBaseClient:
    """Shared plumbing for the async Conduit client."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str],
        default_headers: Optional[Mapping[str, str]],
        extra_query: Optional[Mapping[str, Any]],
        user_agent: str,
    ) -> None:
        self._base_url = normalize_base_url(base_url)
        self._api_key = api_key
        self._default_headers: Dict[str, str] = dict(default_headers or {})
        self._extra_query: Dict[str, Any] = dict(extra_query or {})
        self._user_agent = user_agent

    @property
    def base_url(self) -> str:
        """The normalised base URL of the gateway."""
        return self._base_url

    @property
    def api_key(self) -> Optional[str]:
        """The gateway token used for authenticated requests."""
        return self._api_key

    def _url(self, path: str) -> str:
        """Build an absolute URL for an API path."""
        return "{0}{1}".format(self._base_url, path)

    def _headers(
        self, extra: Optional[Mapping[str, str]] = None, anthropic: bool = False
    ) -> Dict[str, str]:
        """Build the header set for a request.

        Args:
            extra: Additional headers for this single request.
            anthropic: Whether to add Anthropic specific headers.

        Returns:
            The headers to send.
        """
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }
        headers.update(self._default_headers)
        if self._api_key:
            headers.setdefault("Authorization", "Bearer {0}".format(self._api_key))
            if anthropic:
                headers.setdefault("x-api-key", self._api_key)
        if anthropic:
            headers.setdefault("anthropic-version", ANTHROPIC_VERSION)
        if extra:
            headers.update(extra)
        return headers

    def _params(self, extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """Merge the client-wide query parameters with per-request ones."""
        params: Dict[str, Any] = dict(self._extra_query)
        if extra:
            params.update(extra)
        return params


class AsyncClient(_AsyncBaseClient):
    """Asynchronous client for the Conduit gateway.

    Example:
        >>> from conduit import AsyncClient
        >>> client = AsyncClient(base_url="http://localhost:9812", api_key="sk-...")
        >>> completion = await client.chat.completions.create(
        ...     model="glm-5.1",
        ...     messages=[{"role": "user", "content": "hello"}],
        ... )
        >>> completion.choices[0].message.content
        'Hello!'

    Args:
        base_url: Base URL of the gateway.
        api_key: Gateway token.  May be ``None`` for the public endpoints.
        timeout: Per-request timeout in seconds.
        default_headers: Headers added to every request.
        extra_query: Query parameters added to every request.
        transport: Optional httpx transport, mainly useful for tests.
        user_agent: Override the ``User-Agent`` header.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
        *,
        default_headers: Optional[Mapping[str, str]] = None,
        extra_query: Optional[Mapping[str, Any]] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        super().__init__(
            base_url,
            api_key,
            default_headers,
            extra_query,
            user_agent or _USER_AGENT,
        )
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)
        self.chat = AsyncChatNamespace(self)
        self.messages = AsyncMessagesNamespace(self)
        self.responses = AsyncResponsesNamespace(self)
        self.models = AsyncModelsNamespace(self)

    # -- lifecycle ---------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncClient":
        """Enter an ``async with`` block."""
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        """Close the client when leaving an ``async with`` block."""
        await self.close()

    # -- request helpers ---------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> httpx.Response:
        """Perform a request and translate transport failures."""
        request_headers = self._headers() if headers is None else headers
        try:
            return await self._http.request(
                method,
                self._url(path),
                json=json_body,
                params=self._params(params) or None,
                headers=request_headers,
            )
        except httpx.TimeoutException as exc:
            raise wrap_transport_error(exc) from exc
        except httpx.HTTPError as exc:
            raise wrap_transport_error(exc) from exc

    async def _json_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> JSONObject:
        """Perform a request and decode a JSON object response."""
        response = await self._request(
            method, path, json_body=json_body, params=params, headers=headers
        )
        if response.status_code >= 400:
            raise build_api_error(response)
        return _decode_json(response.text)

    async def _open_stream(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> httpx.Response:
        """Open a streaming response, raising on HTTP errors."""
        request = self._http.build_request(
            method,
            self._url(path),
            json=json_body,
            params=self._params(params) or None,
            headers=headers,
        )
        try:
            response = await self._http.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise wrap_transport_error(exc) from exc
        except httpx.HTTPError as exc:
            raise wrap_transport_error(exc) from exc
        if response.status_code >= 400:
            body = response.text
            await response.aclose()
            raise build_api_error(response, body)
        return response

    # -- top level endpoints ----------------------------------------------

    async def health(self) -> HealthStatus:
        """Check that the gateway is reachable.

        Returns:
            The parsed health payload.

        Raises:
            ConduitError: If the gateway could not be reached or replied with
                an error status.
        """
        payload = await self._json_request("GET", "/health")
        return HealthStatus.from_dict(payload)


class AsyncChatNamespace:
    """Namespace grouping the OpenAI Chat Completions endpoints."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self.completions = AsyncCompletionsNamespace(client)


class AsyncCompletionsNamespace:
    """The ``/v1/chat/completions`` endpoint."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    @overload
    async def create(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        stream: Literal[False] = False,
        **kwargs: Any,
    ) -> ChatCompletion: ...

    @overload
    async def create(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        stream: Literal[True],
        **kwargs: Any,
    ) -> AsyncIterator[ChatCompletionChunk]: ...

    async def create(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[ChatCompletion, AsyncIterator[ChatCompletionChunk]]:
        """Create a chat completion.

        Args:
            model: Gateway-facing model id.
            messages: The conversation, as OpenAI-style message objects.
            stream: Whether to stream the response incrementally.
            **kwargs: Extra body fields forwarded to the upstream verbatim.

        Returns:
            A :class:`~conduit.models.ChatCompletion`, or an async iterator of
            :class:`~conduit.models.ChatCompletionChunk` when ``stream`` is
            true.

        Raises:
            ConduitError: If the request failed.
        """
        body: Dict[str, Any] = {"model": model, "messages": list(messages)}
        body.update(kwargs)
        body["stream"] = bool(stream)
        headers = self._client._headers({"Content-Type": "application/json"})

        if not stream:
            payload = await self._client._json_request(
                "POST", "/v1/chat/completions", json_body=body, headers=headers
            )
            return ChatCompletion.from_dict(payload)

        return self._stream_chunks(body, headers)

    async def _stream_chunks(
        self, body: Dict[str, Any], headers: Dict[str, str]
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Yield chat completion chunks as they arrive."""
        response = await self._client._open_stream(
            "POST", "/v1/chat/completions", json_body=body, headers=headers
        )
        try:
            async for message in aiter_sse_messages(response.aiter_lines()):
                payload = message.data.strip()
                if not payload or payload == "[DONE]":
                    continue
                yield ChatCompletionChunk.from_dict(_decode_json(payload))
        finally:
            await response.aclose()


class AsyncMessagesNamespace:
    """The ``/v1/messages`` and ``/v1/messages/count_tokens`` endpoints."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    @overload
    async def create(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        max_tokens: int = 1024,
        system: Optional[Any] = None,
        stream: Literal[False] = False,
        **kwargs: Any,
    ) -> AnthropicMessage: ...

    @overload
    async def create(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        max_tokens: int = 1024,
        system: Optional[Any] = None,
        stream: Literal[True],
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]: ...

    async def create(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        max_tokens: int = 1024,
        system: Optional[Any] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[AnthropicMessage, AsyncIterator[StreamEvent]]:
        """Create an Anthropic-style message.

        Args:
            model: Gateway-facing model id.
            messages: The conversation, as Anthropic-style message objects.
            max_tokens: Maximum number of tokens to generate.
            system: Optional system prompt, a string or a list of blocks.
            stream: Whether to stream the response incrementally.
            **kwargs: Extra body fields forwarded to the upstream verbatim.

        Returns:
            An :class:`~conduit.models.AnthropicMessage`, or an async iterator
            of :class:`~conduit.models.StreamEvent` when ``stream`` is true.

        Raises:
            ConduitError: If the request failed.
        """
        body: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "max_tokens": max_tokens,
        }
        if system is not None:
            body["system"] = system
        body.update(kwargs)
        body["stream"] = bool(stream)
        headers = self._client._headers(
            {"Content-Type": "application/json"}, anthropic=True
        )

        if not stream:
            payload = await self._client._json_request(
                "POST", "/v1/messages", json_body=body, headers=headers
            )
            return AnthropicMessage.from_dict(payload)

        return self._stream_events(body, headers)

    async def _stream_events(
        self, body: Dict[str, Any], headers: Dict[str, str]
    ) -> AsyncIterator[StreamEvent]:
        """Yield Anthropic SSE events as they arrive."""
        response = await self._client._open_stream(
            "POST", "/v1/messages", json_body=body, headers=headers
        )
        try:
            async for message in aiter_sse_messages(response.aiter_lines()):
                payload = message.data.strip()
                if not payload or payload == "[DONE]":
                    continue
                yield StreamEvent.from_payload(message.event, payload)
        finally:
            await response.aclose()

    async def count_tokens(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        system: Optional[Any] = None,
        tools: Optional[Sequence[Mapping[str, Any]]] = None,
        **kwargs: Any,
    ) -> int:
        """Estimate the number of input tokens of a Messages request.

        Args:
            model: Gateway-facing model id.
            messages: The conversation that would be sent.
            system: Optional system prompt.
            tools: Optional tool definitions.
            **kwargs: Extra body fields; the gateway ignores unknown fields.

        Returns:
            The estimated number of input tokens.
        """
        body: Dict[str, Any] = {"model": model, "messages": list(messages)}
        if system is not None:
            body["system"] = system
        if tools is not None:
            body["tools"] = list(tools)
        body.update(kwargs)
        headers = self._client._headers(
            {"Content-Type": "application/json"}, anthropic=True
        )
        payload = await self._client._json_request(
            "POST", "/v1/messages/count_tokens", json_body=body, headers=headers
        )
        return TokenCount.from_dict(payload).input_tokens


class AsyncResponsesNamespace:
    """The ``/v1/responses`` endpoint."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    @overload
    async def create(
        self,
        model: str,
        input: Union[str, Sequence[Mapping[str, Any]]],
        *,
        stream: Literal[False] = False,
        **kwargs: Any,
    ) -> ResponseObject: ...

    @overload
    async def create(
        self,
        model: str,
        input: Union[str, Sequence[Mapping[str, Any]]],
        *,
        stream: Literal[True],
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]: ...

    async def create(
        self,
        model: str,
        input: Union[str, Sequence[Mapping[str, Any]]],
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[ResponseObject, AsyncIterator[StreamEvent]]:
        """Create an OpenAI Responses API response.

        Args:
            model: Gateway-facing model id.
            input: A prompt string or a list of input items.
            stream: Whether to stream the response incrementally.
            **kwargs: Extra body fields forwarded to the upstream verbatim.

        Returns:
            A :class:`~conduit.models.ResponseObject`, or an async iterator of
            :class:`~conduit.models.StreamEvent` when ``stream`` is true.

        Raises:
            ConduitError: If the request failed.
        """
        body: Dict[str, Any] = {"model": model, "input": input}
        body.update(kwargs)
        body["stream"] = bool(stream)
        headers = self._client._headers({"Content-Type": "application/json"})

        if not stream:
            payload = await self._client._json_request(
                "POST", "/v1/responses", json_body=body, headers=headers
            )
            return ResponseObject.from_dict(payload)

        return self._stream_events(body, headers)

    async def _stream_events(
        self, body: Dict[str, Any], headers: Dict[str, str]
    ) -> AsyncIterator[StreamEvent]:
        """Yield Responses API stream events as they arrive."""
        response = await self._client._open_stream(
            "POST", "/v1/responses", json_body=body, headers=headers
        )
        try:
            async for message in aiter_sse_messages(response.aiter_lines()):
                payload = message.data.strip()
                if not payload or payload == "[DONE]":
                    continue
                yield StreamEvent.from_payload(message.event, payload)
        finally:
            await response.aclose()


class AsyncModelsNamespace:
    """The ``/v1/models`` catalog endpoint."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def list(self) -> ModelList:
        """List the models exposed by the gateway.

        The endpoint is public, so no gateway token is required.

        Returns:
            The model catalog.
        """
        payload = await self._client._json_request("GET", "/v1/models")
        return ModelList.from_dict(payload)
