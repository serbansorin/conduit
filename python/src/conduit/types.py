"""Shared type aliases and typed request payloads for the Conduit SDK.

The aliases here are deliberately shallow: Conduit proxies three wire
protocols (OpenAI Chat Completions, Anthropic Messages and OpenAI Responses)
and therefore has to accept fairly open-ended JSON bodies.  The aliases give
call sites a name to refer to while keeping the concrete payloads inspectable.
"""

from __future__ import annotations

from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Sequence,
    TypedDict,
    Union,
)

__all__ = [
    "JSONValue",
    "JSONObject",
    "JSONArray",
    "Headers",
    "QueryParams",
    "RequestBody",
    "ChatMessageParam",
    "AnthropicMessageParam",
    "ResponseContentParam",
    "ResponseInputParam",
    "ToolParam",
    "MessageSequence",
]


JSONValue = Union[str, int, float, bool, None, Dict[str, Any], List[Any]]
"""Any value that can appear in a JSON document."""

JSONObject = Dict[str, Any]
"""A decoded JSON object."""

JSONArray = List[Any]
"""A decoded JSON array."""

Headers = Mapping[str, str]
"""HTTP headers to send with a request."""

QueryParams = Mapping[str, Any]
"""Query string parameters to send with a request."""

RequestBody = Mapping[str, Any]
"""A JSON request body."""


class ChatMessageParam(TypedDict, total=False):
    """A single OpenAI-style chat message.

    Only ``role`` is meaningful in every context; the remaining keys mirror
    the OpenAI Chat Completions schema and are forwarded verbatim.
    """

    role: str
    content: Union[str, List[Dict[str, Any]]]
    name: str
    tool_calls: List[Dict[str, Any]]
    tool_call_id: str
    reasoning_content: str


class AnthropicMessageParam(TypedDict, total=False):
    """A single Anthropic-style message.

    ``content`` may be a plain string or a list of typed content blocks.
    """

    role: str
    content: Union[str, List[Dict[str, Any]]]


class ResponseContentParam(TypedDict, total=False):
    """One content part of an OpenAI Responses input item."""

    type: str
    text: str
    image_url: str


class ResponseInputParam(TypedDict, total=False):
    """One input item of an OpenAI Responses request."""

    role: str
    type: str
    content: Union[str, List[ResponseContentParam]]
    id: str
    name: str
    call_id: str
    output: str
    arguments: str


class ToolParam(TypedDict, total=False):
    """A tool definition accepted by the chat, messages and responses APIs."""

    type: str
    name: str
    description: str
    input_schema: Dict[str, Any]
    parameters: Dict[str, Any]
    strict: bool
    function: Dict[str, Any]


MessageSequence = Sequence[Union[ChatMessageParam, AnthropicMessageParam, Dict[str, Any]]]
"""A sequence of chat or Anthropic messages."""
