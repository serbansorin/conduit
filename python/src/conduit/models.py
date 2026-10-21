"""Typed models for the payloads and responses returned by the Conduit SDK.

Every model is a plain :mod:`dataclasses` dataclass - the SDK deliberately
avoids a validation dependency.  Each response model exposes a
``from_dict`` classmethod that converts a decoded JSON object into the
typed structure, ignoring unknown keys so the gateway can add fields
without breaking existing clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .types import JSONObject

__all__ = [
    "ChatMessage",
    "ChatChoice",
    "ChatUsage",
    "ChatCompletion",
    "ChatChunkChoice",
    "ChatCompletionChunk",
    "MessageContent",
    "MessageUsage",
    "AnthropicMessage",
    "StreamEvent",
    "AnthropicStreamEvent",
    "ResponseContent",
    "ResponseOutputItem",
    "ResponseUsage",
    "ResponseObject",
    "Model",
    "ModelList",
    "TokenCount",
    "HealthStatus",
    "AdminKey",
    "AdminToken",
    "AdminModelRoute",
    "ModelMapping",
    "UsageLog",
    "UsageLogPage",
]


# --------------------------------------------------------------------------
# small parsing helpers
# --------------------------------------------------------------------------


def _get(payload: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """Return ``payload[key]`` or ``default`` when the key is absent."""
    value = payload.get(key, default)
    return default if value is None else value


def _as_int(value: Any, default: int = 0) -> int:
    """Coerce ``value`` to :class:`int`, falling back to ``default``."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    """Coerce ``value`` to :class:`float`, falling back to ``default``."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _as_str(value: Any, default: str = "") -> str:
    """Coerce ``value`` to :class:`str`, falling back to ``default``."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _as_dict(value: Any) -> Dict[str, Any]:
    """Return ``value`` when it is a mapping, otherwise an empty dict."""
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_list(value: Any) -> List[Any]:
    """Return ``value`` when it is a list/tuple, otherwise an empty list."""
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _as_opt_str(value: Any) -> Optional[str]:
    """Return ``value`` as a string, or ``None`` when it is missing."""
    if value is None:
        return None
    return _as_str(value)


# --------------------------------------------------------------------------
# OpenAI Chat Completions
# --------------------------------------------------------------------------


@dataclass
class ChatMessage:
    """A chat message as returned by the Chat Completions API.

    Attributes:
        role: The role of the message author (``assistant``, ``user``, ...).
        content: Textual content, when the gateway returned one.
        tool_calls: Tool calls requested by the model, when present.
        tool_call_id: Correlation id when the message is a tool result.
        name: Optional author name.
        reasoning_content: Chain-of-thought text when the model exposes it.
        refusal: Refusal text when the model refused the request.
    """

    role: str = ""
    content: Optional[str] = None
    tool_calls: List[JSONObject] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    reasoning_content: Optional[str] = None
    refusal: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ChatMessage":
        """Build a :class:`ChatMessage` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            role=_as_str(data.get("role")),
            content=_as_opt_str(data.get("content")),
            tool_calls=[_as_dict(item) for item in _as_list(data.get("tool_calls"))],
            tool_call_id=_as_opt_str(data.get("tool_call_id")),
            name=_as_opt_str(data.get("name")),
            reasoning_content=_as_opt_str(data.get("reasoning_content")),
            refusal=_as_opt_str(data.get("refusal")),
        )


@dataclass
class ChatUsage:
    """Token accounting for a Chat Completions call.

    Attributes:
        prompt_tokens: Tokens consumed by the request.
        completion_tokens: Tokens produced by the model.
        total_tokens: Sum of prompt and completion tokens.
        prompt_tokens_details: Breakdown of cached/reasoning prompt tokens.
        completion_tokens_details: Breakdown of reasoning completion tokens.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_tokens_details: Dict[str, Any] = field(default_factory=dict)
    completion_tokens_details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ChatUsage":
        """Build a :class:`ChatUsage` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            prompt_tokens=_as_int(data.get("prompt_tokens")),
            completion_tokens=_as_int(data.get("completion_tokens")),
            total_tokens=_as_int(data.get("total_tokens")),
            prompt_tokens_details=_as_dict(data.get("prompt_tokens_details")),
            completion_tokens_details=_as_dict(data.get("completion_tokens_details")),
        )


@dataclass
class ChatChoice:
    """A single completion alternative.

    Attributes:
        index: Position of the choice in the ``choices`` array.
        message: The assistant message produced for this choice.
        finish_reason: Why the model stopped, e.g. ``stop``.
        logprobs: Optional log-probability payload.
    """

    index: int = 0
    message: ChatMessage = field(default_factory=ChatMessage)
    finish_reason: Optional[str] = None
    logprobs: Optional[JSONObject] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ChatChoice":
        """Build a :class:`ChatChoice` from a decoded JSON object."""
        data = _as_dict(payload)
        logprobs = data.get("logprobs")
        return cls(
            index=_as_int(data.get("index")),
            message=ChatMessage.from_dict(_as_dict(data.get("message"))),
            finish_reason=_as_opt_str(data.get("finish_reason")),
            logprobs=_as_dict(logprobs) if isinstance(logprobs, Mapping) else None,
        )


@dataclass
class ChatCompletion:
    """A non-streamed Chat Completions response.

    Attributes:
        id: Server-side identifier of the completion.
        object: Always ``chat.completion`` for non-streamed responses.
        created: Unix timestamp of creation time.
        model: The gateway-facing model id.
        choices: The completion alternatives.
        usage: Token accounting, when the gateway reported it.
    """

    id: str = ""
    object: str = "chat.completion"
    created: int = 0
    model: str = ""
    choices: List[ChatChoice] = field(default_factory=list)
    usage: Optional[ChatUsage] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ChatCompletion":
        """Build a :class:`ChatCompletion` from a decoded JSON object."""
        data = _as_dict(payload)
        usage = data.get("usage")
        return cls(
            id=_as_str(data.get("id")),
            object=_as_str(data.get("object"), "chat.completion"),
            created=_as_int(data.get("created")),
            model=_as_str(data.get("model")),
            choices=[ChatChoice.from_dict(item) for item in _as_list(data.get("choices"))],
            usage=ChatUsage.from_dict(_as_dict(usage)) if isinstance(usage, Mapping) else None,
        )

    @property
    def text(self) -> str:
        """Return the text of the first choice, or an empty string."""
        if not self.choices:
            return ""
        return self.choices[0].message.content or ""


@dataclass
class ChatChunkChoice:
    """A single streamed completion delta.

    Attributes:
        index: Position of the choice in the ``choices`` array.
        delta: The incremental message payload.
        finish_reason: Why the model stopped, when this is the final chunk.
    """

    index: int = 0
    delta: ChatMessage = field(default_factory=ChatMessage)
    finish_reason: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ChatChunkChoice":
        """Build a :class:`ChatChunkChoice` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            index=_as_int(data.get("index")),
            delta=ChatMessage.from_dict(_as_dict(data.get("delta"))),
            finish_reason=_as_opt_str(data.get("finish_reason")),
        )


@dataclass
class ChatCompletionChunk:
    """A streamed Chat Completions chunk.

    Attributes:
        id: Server-side identifier of the completion.
        object: Always ``chat.completion.chunk`` for streamed responses.
        created: Unix timestamp of creation time.
        model: The gateway-facing model id.
        choices: The incremental choices.
        usage: Token accounting, present on the final chunk when requested.
    """

    id: str = ""
    object: str = "chat.completion.chunk"
    created: int = 0
    model: str = ""
    choices: List[ChatChunkChoice] = field(default_factory=list)
    usage: Optional[ChatUsage] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ChatCompletionChunk":
        """Build a :class:`ChatCompletionChunk` from a decoded JSON object."""
        data = _as_dict(payload)
        usage = data.get("usage")
        return cls(
            id=_as_str(data.get("id")),
            object=_as_str(data.get("object"), "chat.completion.chunk"),
            created=_as_int(data.get("created")),
            model=_as_str(data.get("model")),
            choices=[ChatChunkChoice.from_dict(item) for item in _as_list(data.get("choices"))],
            usage=ChatUsage.from_dict(_as_dict(usage)) if isinstance(usage, Mapping) else None,
        )


# --------------------------------------------------------------------------
# Anthropic Messages
# --------------------------------------------------------------------------


@dataclass
class MessageContent:
    """A single content block of an Anthropic message.

    Attributes:
        type: Block type, e.g. ``text``, ``tool_use`` or ``thinking``.
        text: Text of a ``text`` block.
        id: Identifier of a ``tool_use`` block.
        name: Tool name of a ``tool_use`` block.
        input: Tool arguments of a ``tool_use`` block.
        tool_use_id: Correlation id of a ``tool_result`` block.
        content: Nested content of a ``tool_result`` block.
        is_error: Whether a ``tool_result`` block reports an error.
        thinking: Reasoning text of a ``thinking`` block.
    """

    type: str = ""
    text: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    input: Any = None
    tool_use_id: Optional[str] = None
    content: Any = None
    is_error: bool = False
    thinking: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MessageContent":
        """Build a :class:`MessageContent` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            type=_as_str(data.get("type")),
            text=_as_opt_str(data.get("text")),
            id=_as_opt_str(data.get("id")),
            name=_as_opt_str(data.get("name")),
            input=data.get("input"),
            tool_use_id=_as_opt_str(data.get("tool_use_id")),
            content=data.get("content"),
            is_error=bool(data.get("is_error", False)),
            thinking=_as_opt_str(data.get("thinking")),
        )


@dataclass
class MessageUsage:
    """Token accounting for an Anthropic Messages call.

    Attributes:
        input_tokens: Tokens consumed by the request.
        output_tokens: Tokens produced by the model.
        cache_read_input_tokens: Prompt tokens served from the cache.
        cache_creation_input_tokens: Prompt tokens written to the cache.
        reasoning_tokens: Reasoning tokens billed by the upstream.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    reasoning_tokens: int = 0

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MessageUsage":
        """Build a :class:`MessageUsage` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            input_tokens=_as_int(data.get("input_tokens")),
            output_tokens=_as_int(data.get("output_tokens")),
            cache_read_input_tokens=_as_int(data.get("cache_read_input_tokens")),
            cache_creation_input_tokens=_as_int(data.get("cache_creation_input_tokens")),
            reasoning_tokens=_as_int(data.get("reasoning_tokens")),
        )


@dataclass
class AnthropicMessage:
    """A non-streamed Anthropic Messages response.

    Attributes:
        id: Server-side identifier of the message.
        type: Always ``message``.
        role: Always ``assistant``.
        content: The content blocks of the message.
        model: The gateway-facing model id.
        stop_reason: Why the model stopped, e.g. ``end_turn``.
        stop_sequence: The stop sequence that terminated generation.
        usage: Token accounting, when the gateway reported it.
    """

    id: str = ""
    type: str = "message"
    role: str = "assistant"
    content: List[MessageContent] = field(default_factory=list)
    model: str = ""
    stop_reason: Optional[str] = None
    stop_sequence: Optional[str] = None
    usage: Optional[MessageUsage] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AnthropicMessage":
        """Build an :class:`AnthropicMessage` from a decoded JSON object."""
        data = _as_dict(payload)
        usage = data.get("usage")
        return cls(
            id=_as_str(data.get("id")),
            type=_as_str(data.get("type"), "message"),
            role=_as_str(data.get("role"), "assistant"),
            content=[MessageContent.from_dict(item) for item in _as_list(data.get("content"))],
            model=_as_str(data.get("model")),
            stop_reason=_as_opt_str(data.get("stop_reason")),
            stop_sequence=_as_opt_str(data.get("stop_sequence")),
            usage=MessageUsage.from_dict(_as_dict(usage)) if isinstance(usage, Mapping) else None,
        )

    @property
    def text(self) -> str:
        """Return the concatenated text of all text blocks."""
        return "".join(block.text or "" for block in self.content if block.type == "text")


@dataclass
class StreamEvent:
    """A single Server-Sent Event emitted by the gateway.

    Attributes:
        type: Event type, e.g. ``message_start`` or ``response.output_text.delta``.
        data: Decoded JSON payload of the event.
        raw: The raw ``data:`` payload before decoding.
    """

    type: str = ""
    data: JSONObject = field(default_factory=dict)
    raw: str = ""

    @classmethod
    def from_payload(cls, event: Optional[str], payload: str) -> "StreamEvent":
        """Build a :class:`StreamEvent` from an SSE event name and data line."""
        decoded: JSONObject = {}
        try:
            import json

            parsed = json.loads(payload)
            if isinstance(parsed, Mapping):
                decoded = dict(parsed)
        except ValueError:
            decoded = {}
        event_type = event or _as_str(decoded.get("type"))
        return cls(type=event_type, data=decoded, raw=payload)


#: Anthropic specific alias of :class:`StreamEvent`.
AnthropicStreamEvent = StreamEvent


# --------------------------------------------------------------------------
# OpenAI Responses
# --------------------------------------------------------------------------


@dataclass
class ResponseContent:
    """One content part of an output item.

    Attributes:
        type: Content type, e.g. ``output_text``.
        text: Textual content.
        annotations: Citations attached to the text.
        reasoning_content: Reasoning text when exposed by the upstream.
        image_url: Image URL for image parts.
    """

    type: str = ""
    text: Optional[str] = None
    annotations: List[Any] = field(default_factory=list)
    reasoning_content: Optional[str] = None
    image_url: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResponseContent":
        """Build a :class:`ResponseContent` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            type=_as_str(data.get("type")),
            text=_as_opt_str(data.get("text")),
            annotations=_as_list(data.get("annotations")),
            reasoning_content=_as_opt_str(data.get("reasoning_content")),
            image_url=_as_opt_str(data.get("image_url")),
        )


@dataclass
class ResponseOutputItem:
    """One item of the Responses API output array.

    Attributes:
        type: Item type, e.g. ``message`` or ``function_call``.
        id: Item identifier.
        role: Author role, usually ``assistant``.
        status: Lifecycle status of the item.
        content: Content parts of the item.
        name: Tool name for ``function_call`` items.
        call_id: Correlation id for tool calls.
        arguments: JSON-encoded tool arguments.
        output: Textual output for ``function_call_output`` items.
        summary: Reasoning summary parts.
    """

    type: str = ""
    id: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    content: List[ResponseContent] = field(default_factory=list)
    name: Optional[str] = None
    call_id: Optional[str] = None
    arguments: Optional[str] = None
    output: Optional[str] = None
    summary: List[Any] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResponseOutputItem":
        """Build a :class:`ResponseOutputItem` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            type=_as_str(data.get("type")),
            id=_as_opt_str(data.get("id")),
            role=_as_opt_str(data.get("role")),
            status=_as_opt_str(data.get("status")),
            content=[ResponseContent.from_dict(item) for item in _as_list(data.get("content"))],
            name=_as_opt_str(data.get("name")),
            call_id=_as_opt_str(data.get("call_id")),
            arguments=_as_opt_str(data.get("arguments")),
            output=_as_opt_str(data.get("output")),
            summary=_as_list(data.get("summary")),
        )


@dataclass
class ResponseUsage:
    """Token accounting for a Responses call.

    Attributes:
        input_tokens: Tokens consumed by the request.
        output_tokens: Tokens produced by the model.
        total_tokens: Sum of input and output tokens.
        input_tokens_details: Breakdown of cached prompt tokens.
        output_tokens_details: Breakdown of reasoning tokens.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_tokens_details: Dict[str, Any] = field(default_factory=dict)
    output_tokens_details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResponseUsage":
        """Build a :class:`ResponseUsage` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            input_tokens=_as_int(data.get("input_tokens")),
            output_tokens=_as_int(data.get("output_tokens")),
            total_tokens=_as_int(data.get("total_tokens")),
            input_tokens_details=_as_dict(data.get("input_tokens_details")),
            output_tokens_details=_as_dict(data.get("output_tokens_details")),
        )


@dataclass
class ResponseObject:
    """A non-streamed OpenAI Responses response.

    Attributes:
        id: Server-side identifier of the response.
        object: Always ``response``.
        model: The gateway-facing model id.
        output: The generated output items.
        output_text: Convenience text of the response, when present.
        usage: Token accounting, when the gateway reported it.
        status: Lifecycle status, e.g. ``completed``.
        instructions: Instructions echoed back by the API.
    """

    id: str = ""
    object: str = "response"
    model: str = ""
    output: List[ResponseOutputItem] = field(default_factory=list)
    output_text: str = ""
    usage: Optional[ResponseUsage] = None
    status: Optional[str] = None
    instructions: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResponseObject":
        """Build a :class:`ResponseObject` from a decoded JSON object."""
        data = _as_dict(payload)
        usage = data.get("usage")
        return cls(
            id=_as_str(data.get("id")),
            object=_as_str(data.get("object"), "response"),
            model=_as_str(data.get("model")),
            output=[ResponseOutputItem.from_dict(item) for item in _as_list(data.get("output"))],
            output_text=_as_str(data.get("output_text")),
            usage=ResponseUsage.from_dict(_as_dict(usage)) if isinstance(usage, Mapping) else None,
            status=_as_opt_str(data.get("status")),
            instructions=_as_opt_str(data.get("instructions")),
        )


# --------------------------------------------------------------------------
# Catalog, health and token counting
# --------------------------------------------------------------------------


@dataclass
class Model:
    """A model exposed by the gateway catalog.

    Attributes:
        id: Gateway-facing model id.
        object: Always ``model``.
        created: Creation timestamp reported by the gateway.
        owned_by: Owner of the model, always ``conduit``.
        name: Display name.
        protocol: Wire protocol of the model, e.g. ``chat`` or ``messages``.
        context_length: Maximum context window in tokens.
        context_len: Alias of ``context_length``.
        priority: Routing priority hint.
        tags: Capability tags.
        architecture: Modality/tokenizer metadata.
        pricing: Per-token prices keyed by billing dimension.
        supported_parameters: Parameters the model understands.
        description: Human readable description.
        knowledge_cutoff: Training data cutoff.
    """

    id: str = ""
    object: str = "model"
    created: int = 0
    owned_by: str = "conduit"
    name: str = ""
    protocol: str = ""
    context_length: int = 0
    context_len: int = 0
    priority: int = 0
    tags: List[str] = field(default_factory=list)
    architecture: Dict[str, Any] = field(default_factory=dict)
    pricing: Dict[str, str] = field(default_factory=dict)
    supported_parameters: List[str] = field(default_factory=list)
    description: str = ""
    knowledge_cutoff: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Model":
        """Build a :class:`Model` from a decoded JSON object."""
        data = _as_dict(payload)
        context_length = _as_int(data.get("context_length"))
        context_len = _as_int(data.get("context_len"), context_length)
        return cls(
            id=_as_str(data.get("id")),
            object=_as_str(data.get("object"), "model"),
            created=_as_int(data.get("created")),
            owned_by=_as_str(data.get("owned_by"), "conduit"),
            name=_as_str(data.get("name")),
            protocol=_as_str(data.get("protocol")),
            context_length=context_length,
            context_len=context_len,
            priority=_as_int(data.get("priority")),
            tags=[_as_str(tag) for tag in _as_list(data.get("tags"))],
            architecture=_as_dict(data.get("architecture")),
            pricing={_as_str(k): _as_str(v) for k, v in _as_dict(data.get("pricing")).items()},
            supported_parameters=[
                _as_str(item) for item in _as_list(data.get("supported_parameters"))
            ],
            description=_as_str(data.get("description")),
            knowledge_cutoff=_as_str(data.get("knowledge_cutoff")),
        )


@dataclass
class ModelList:
    """The result of ``GET /v1/models``.

    Attributes:
        object: Always ``list``.
        data: The catalog entries.
    """

    object: str = "list"
    data: List[Model] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ModelList":
        """Build a :class:`ModelList` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            object=_as_str(data.get("object"), "list"),
            data=[Model.from_dict(item) for item in _as_list(data.get("data"))],
        )

    def __iter__(self) -> Any:
        """Iterate over the catalog entries."""
        return iter(self.data)

    def __len__(self) -> int:
        """Return the number of catalog entries."""
        return len(self.data)


@dataclass
class TokenCount:
    """The result of ``POST /v1/messages/count_tokens``.

    Attributes:
        input_tokens: The estimated number of input tokens.
    """

    input_tokens: int = 0

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TokenCount":
        """Build a :class:`TokenCount` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(input_tokens=_as_int(data.get("input_tokens")))


@dataclass
class HealthStatus:
    """The result of ``GET /health``.

    Attributes:
        status: Service status, ``ok`` when the gateway is reachable.
        service: Service name reported by the gateway.
    """

    status: str = ""
    service: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HealthStatus":
        """Build a :class:`HealthStatus` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            status=_as_str(data.get("status")),
            service=_as_str(data.get("service")),
        )


# --------------------------------------------------------------------------
# Admin resources
# --------------------------------------------------------------------------


@dataclass
class AdminKey:
    """An upstream key managed through the admin API.

    Attributes:
        id: Numeric identifier of the key.
        value: The key value; the gateway masks it in list responses.
        group: Key-pool group, e.g. ``go`` or ``ollama``.
        label: Human readable label.
        enabled: Whether the key may be used.
        weight: Selection weight inside the group.
        proxy_url: Optional proxy used for upstream calls.
        cookie: Optional upstream session cookie used for quota lookups.
        workspace_id: Optional upstream workspace id.
        quota_updated_at: Timestamp of the last quota lookup.
        fail_count: Number of consecutive failures.
        cooldown_until: Timestamp until which the key is in cooldown.
        last_used: Timestamp of the last successful use.
        usage_count: Number of times the key was used.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        last_quota: Last raw quota payload, when the gateway returned one.
    """

    id: int = 0
    value: str = ""
    group: str = ""
    label: str = ""
    enabled: bool = True
    weight: int = 1
    proxy_url: str = ""
    cookie: str = ""
    workspace_id: str = ""
    quota_updated_at: Optional[str] = None
    fail_count: int = 0
    cooldown_until: Optional[str] = None
    last_used: Optional[str] = None
    usage_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_quota: Optional[JSONObject] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AdminKey":
        """Build an :class:`AdminKey` from a decoded JSON object."""
        data = _as_dict(payload)
        quota = data.get("last_quota")
        return cls(
            id=_as_int(data.get("id")),
            value=_as_str(data.get("value")),
            group=_as_str(data.get("group")),
            label=_as_str(data.get("label")),
            enabled=bool(data.get("enabled", True)),
            weight=_as_int(data.get("weight"), 1),
            proxy_url=_as_str(data.get("proxy_url")),
            cookie=_as_str(data.get("cookie")),
            workspace_id=_as_str(data.get("workspace_id")),
            quota_updated_at=_as_opt_str(data.get("quota_updated_at")),
            fail_count=_as_int(data.get("fail_count")),
            cooldown_until=_as_opt_str(data.get("cooldown_until")),
            last_used=_as_opt_str(data.get("last_used")),
            usage_count=_as_int(data.get("usage_count")),
            created_at=_as_opt_str(data.get("created_at")),
            updated_at=_as_opt_str(data.get("updated_at")),
            last_quota=_as_dict(quota) if isinstance(quota, Mapping) else None,
        )


@dataclass
class AdminToken:
    """A gateway token managed through the admin API.

    Attributes:
        id: Numeric identifier of the token.
        token: The token string itself.
        name: Human readable name.
        description: Free-form description.
        enabled: Whether the token may be used.
        allowed_groups: Comma-separated list of allowed key-pool groups.
        rate_limit: Requests per minute, ``0`` means unlimited.
        max_requests: Total request cap, ``0`` means unlimited.
        requests_used: Number of requests consumed so far.
        expires_at: Expiry timestamp, when set.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        total_requests: Total requests recorded in the usage log.
        total_input_tokens: Total prompt tokens recorded in the usage log.
        total_output_tokens: Total completion tokens recorded in the usage log.
        total_tokens: Total tokens recorded in the usage log.
        today_requests: Requests recorded today.
        today_tokens: Tokens recorded today.
        last_hour_requests: Requests recorded in the last hour.
        last_hour_tokens: Tokens recorded in the last hour.
    """

    id: int = 0
    token: str = ""
    name: str = ""
    description: str = ""
    enabled: bool = True
    allowed_groups: str = ""
    rate_limit: int = 0
    max_requests: int = 0
    requests_used: int = 0
    expires_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    total_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    today_requests: int = 0
    today_tokens: int = 0
    last_hour_requests: int = 0
    last_hour_tokens: int = 0

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AdminToken":
        """Build an :class:`AdminToken` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            id=_as_int(data.get("id")),
            token=_as_str(data.get("token")),
            name=_as_str(data.get("name")),
            description=_as_str(data.get("description")),
            enabled=bool(data.get("enabled", True)),
            allowed_groups=_as_str(data.get("allowed_groups")),
            rate_limit=_as_int(data.get("rate_limit")),
            max_requests=_as_int(data.get("max_requests")),
            requests_used=_as_int(data.get("requests_used")),
            expires_at=_as_opt_str(data.get("expires_at")),
            created_at=_as_opt_str(data.get("created_at")),
            updated_at=_as_opt_str(data.get("updated_at")),
            total_requests=_as_int(data.get("total_requests")),
            total_input_tokens=_as_int(data.get("total_input_tokens")),
            total_output_tokens=_as_int(data.get("total_output_tokens")),
            total_tokens=_as_int(data.get("total_tokens")),
            today_requests=_as_int(data.get("today_requests")),
            today_tokens=_as_int(data.get("today_tokens")),
            last_hour_requests=_as_int(data.get("last_hour_requests")),
            last_hour_tokens=_as_int(data.get("last_hour_tokens")),
        )


@dataclass
class AdminModelRoute:
    """A model route as managed through the admin API.

    Attributes:
        id: Gateway-facing model id.
        name: Display name.
        upstream: Primary upstream, ``go`` or ``ollama``.
        upstreams: All upstreams serving the model.
        upstream_groups: Per-upstream key-pool group overrides.
        targets: Per-upstream real-model/protocol/group overrides.
        protocol: Wire protocol of the model.
        real_model: Upstream model id.
        group: Key-pool group the model is served from.
        context_len: Context window hint.
        status: ``1`` when enabled, ``0`` when disabled.
        priority: Admin-defined ordering hint.
        tags: Capability tags.
        is_customized: Whether an admin edited protected fields.
        customized_fields: Fields protected from automatic sync.
        openrouter_id: Matched OpenRouter model id.
        openrouter_name: Matched OpenRouter display name.
        openrouter_matched_by: Matching strategy used during enrichment.
        architecture: Modality/tokenizer metadata.
        pricing: Per-token prices keyed by billing dimension.
        supported_parameters: Parameters the model understands.
        description: Human readable description.
        knowledge_cutoff: Training data cutoff.
    """

    id: str = ""
    name: str = ""
    upstream: str = ""
    upstreams: List[str] = field(default_factory=list)
    upstream_groups: Dict[str, str] = field(default_factory=dict)
    targets: Dict[str, Any] = field(default_factory=dict)
    protocol: str = ""
    real_model: str = ""
    group: str = ""
    context_len: int = 0
    status: Optional[int] = None
    priority: int = 0
    tags: List[str] = field(default_factory=list)
    is_customized: bool = False
    customized_fields: List[str] = field(default_factory=list)
    openrouter_id: str = ""
    openrouter_name: str = ""
    openrouter_matched_by: str = ""
    architecture: Dict[str, Any] = field(default_factory=dict)
    pricing: Dict[str, str] = field(default_factory=dict)
    supported_parameters: List[str] = field(default_factory=list)
    description: str = ""
    knowledge_cutoff: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AdminModelRoute":
        """Build an :class:`AdminModelRoute` from a decoded JSON object."""
        data = _as_dict(payload)
        status = data.get("status")
        return cls(
            id=_as_str(data.get("id")),
            name=_as_str(data.get("name")),
            upstream=_as_str(data.get("upstream")),
            upstreams=[_as_str(item) for item in _as_list(data.get("upstreams"))],
            upstream_groups={
                _as_str(k): _as_str(v) for k, v in _as_dict(data.get("upstream_groups")).items()
            },
            targets=_as_dict(data.get("targets")),
            protocol=_as_str(data.get("protocol")),
            real_model=_as_str(data.get("real_model")),
            group=_as_str(data.get("group")),
            context_len=_as_int(data.get("context_len")),
            status=_as_int(status) if isinstance(status, int) else None,
            priority=_as_int(data.get("priority")),
            tags=[_as_str(tag) for tag in _as_list(data.get("tags"))],
            is_customized=bool(data.get("is_customized", False)),
            customized_fields=[
                _as_str(field_name) for field_name in _as_list(data.get("customized_fields"))
            ],
            openrouter_id=_as_str(data.get("openrouter_id")),
            openrouter_name=_as_str(data.get("openrouter_name")),
            openrouter_matched_by=_as_str(data.get("openrouter_matched_by")),
            architecture=_as_dict(data.get("architecture")),
            pricing={_as_str(k): _as_str(v) for k, v in _as_dict(data.get("pricing")).items()},
            supported_parameters=[
                _as_str(item) for item in _as_list(data.get("supported_parameters"))
            ],
            description=_as_str(data.get("description")),
            knowledge_cutoff=_as_str(data.get("knowledge_cutoff")),
        )


@dataclass
class ModelMapping:
    """A model rewrite rule.

    Attributes:
        source_model: The model id requested by the client.
        target_model: The model id served instead.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """

    source_model: str = ""
    target_model: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ModelMapping":
        """Build a :class:`ModelMapping` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            source_model=_as_str(data.get("source_model")),
            target_model=_as_str(data.get("target_model")),
            created_at=_as_opt_str(data.get("created_at")),
            updated_at=_as_opt_str(data.get("updated_at")),
        )


@dataclass
class UsageLog:
    """A single proxied request as recorded by the gateway.

    Attributes:
        id: Numeric identifier of the log row.
        request_id: Correlation id of the request.
        token_id: Identifier of the gateway token used.
        token_name: Name of the gateway token used.
        key_id: Identifier of the upstream key used.
        model: Gateway-facing model id.
        model_name: Display name of the model.
        group: Key-pool group the request was served from.
        protocol: Wire protocol used for the request.
        ip_address: Client IP address.
        status_code: HTTP status code returned to the client.
        duration_ms: Total duration of the request.
        first_response_ms: Time until the first upstream byte.
        ttft_ms: Time to first token.
        stream: Whether the request was streamed.
        input_tokens: Prompt tokens billed.
        output_tokens: Completion tokens billed.
        reasoning_tokens: Reasoning tokens billed.
        cache_tokens: Total cache tokens.
        cache_read_tokens: Cache read tokens.
        cache_creation_tokens: Cache write tokens.
        total_tokens: Total tokens billed.
        total_cost: Estimated cost.
        actual_cost: Cost after the group multiplier.
        account_cost: Cost billed to the account.
        input_unit_price: Price per input token.
        output_unit_price: Price per output token.
        cache_read_unit_price: Price per cached input token.
        cache_write_unit_price: Price per written cache token.
        group_multiplier: Cost multiplier of the group.
        billing_mode: Billing mode, ``token`` or ``request``.
        error: Error message, when the request failed.
        created_at: Timestamp of the request.
    """

    id: int = 0
    request_id: str = ""
    token_id: int = 0
    token_name: str = ""
    key_id: int = 0
    model: str = ""
    model_name: str = ""
    group: str = ""
    protocol: str = ""
    ip_address: str = ""
    status_code: int = 0
    duration_ms: int = 0
    first_response_ms: int = 0
    ttft_ms: int = 0
    stream: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    actual_cost: float = 0.0
    account_cost: float = 0.0
    input_unit_price: float = 0.0
    output_unit_price: float = 0.0
    cache_read_unit_price: float = 0.0
    cache_write_unit_price: float = 0.0
    group_multiplier: float = 0.0
    billing_mode: str = ""
    error: str = ""
    created_at: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "UsageLog":
        """Build a :class:`UsageLog` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            id=_as_int(data.get("id")),
            request_id=_as_str(data.get("request_id")),
            token_id=_as_int(data.get("token_id")),
            token_name=_as_str(data.get("token_name")),
            key_id=_as_int(data.get("key_id")),
            model=_as_str(data.get("model")),
            model_name=_as_str(data.get("model_name")),
            group=_as_str(data.get("group")),
            protocol=_as_str(data.get("protocol")),
            ip_address=_as_str(data.get("ip_address")),
            status_code=_as_int(data.get("status_code")),
            duration_ms=_as_int(data.get("duration_ms")),
            first_response_ms=_as_int(data.get("first_response_ms")),
            ttft_ms=_as_int(data.get("ttft_ms")),
            stream=bool(data.get("stream", False)),
            input_tokens=_as_int(data.get("input_tokens")),
            output_tokens=_as_int(data.get("output_tokens")),
            reasoning_tokens=_as_int(data.get("reasoning_tokens")),
            cache_tokens=_as_int(data.get("cache_tokens")),
            cache_read_tokens=_as_int(data.get("cache_read_tokens")),
            cache_creation_tokens=_as_int(data.get("cache_creation_tokens")),
            total_tokens=_as_int(data.get("total_tokens")),
            total_cost=_as_float(data.get("total_cost")),
            actual_cost=_as_float(data.get("actual_cost")),
            account_cost=_as_float(data.get("account_cost")),
            input_unit_price=_as_float(data.get("input_unit_price")),
            output_unit_price=_as_float(data.get("output_unit_price")),
            cache_read_unit_price=_as_float(data.get("cache_read_unit_price")),
            cache_write_unit_price=_as_float(data.get("cache_write_unit_price")),
            group_multiplier=_as_float(data.get("group_multiplier")),
            billing_mode=_as_str(data.get("billing_mode")),
            error=_as_str(data.get("error")),
            created_at=_as_opt_str(data.get("created_at")),
        )


@dataclass
class UsageLogPage:
    """A page of usage logs.

    Attributes:
        items: The log rows of this page.
        total: Total number of matching rows.
        page: The requested page number (1-based).
        page_size: The requested page size.
        summary: Aggregated counters for the current filter window.
    """

    items: List[UsageLog] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 25
    summary: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "UsageLogPage":
        """Build a :class:`UsageLogPage` from a decoded JSON object."""
        data = _as_dict(payload)
        return cls(
            items=[UsageLog.from_dict(item) for item in _as_list(data.get("items"))],
            total=_as_int(data.get("total")),
            page=_as_int(data.get("page"), 1),
            page_size=_as_int(data.get("page_size"), 25),
            summary=_as_dict(data.get("summary")),
        )
