"""Conduit - Python SDK for the Conduit multi-key LLM gateway.

Conduit exposes the OpenAI Chat Completions, Anthropic Messages and OpenAI
Responses APIs behind a single gateway token, plus an admin API for managing
upstream keys, gateway tokens and model routes.

Example:
    >>> from conduit import Client
    >>> client = Client(base_url="http://localhost:9812", api_key="sk-...")
    >>> completion = client.chat.completions.create(
    ...     model="glm-5.1",
    ...     messages=[{"role": "user", "content": "hello"}],
    ... )
    >>> completion.choices[0].message.content
    'Hello!'
"""

from __future__ import annotations

from ._version import __version__
from .admin import AdminClient, AsyncAdminClient
from .async_client import (
    AsyncChatNamespace,
    AsyncClient,
    AsyncCompletionsNamespace,
    AsyncMessagesNamespace,
    AsyncModelsNamespace,
    AsyncResponsesNamespace,
)
from .client import (
    ANTHROPIC_VERSION,
    ChatNamespace,
    Client,
    CompletionsNamespace,
    MessagesNamespace,
    ModelsNamespace,
    ResponsesNamespace,
)
from .exceptions import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConduitError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
)
from .models import (
    AdminKey,
    AdminModelRoute,
    AdminToken,
    AnthropicMessage,
    AnthropicStreamEvent,
    ChatChoice,
    ChatChunkChoice,
    ChatCompletion,
    ChatCompletionChunk,
    ChatMessage,
    ChatUsage,
    HealthStatus,
    MessageContent,
    MessageUsage,
    Model,
    ModelList,
    ModelMapping,
    ResponseContent,
    ResponseObject,
    ResponseOutputItem,
    ResponseUsage,
    StreamEvent,
    TokenCount,
    UsageLog,
    UsageLogPage,
)
from .types import (
    AnthropicMessageParam,
    ChatMessageParam,
    JSONArray,
    JSONObject,
    JSONValue,
    ResponseInputParam,
    ToolParam,
)

__all__ = [
    "__version__",
    # clients
    "Client",
    "AsyncClient",
    "AdminClient",
    "AsyncAdminClient",
    # namespaces
    "ChatNamespace",
    "CompletionsNamespace",
    "MessagesNamespace",
    "ResponsesNamespace",
    "ModelsNamespace",
    "AsyncChatNamespace",
    "AsyncCompletionsNamespace",
    "AsyncMessagesNamespace",
    "AsyncResponsesNamespace",
    "AsyncModelsNamespace",
    # exceptions
    "ConduitError",
    "APIError",
    "APIStatusError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "APIConnectionError",
    "APITimeoutError",
    # models
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
    # types
    "JSONValue",
    "JSONObject",
    "JSONArray",
    "ChatMessageParam",
    "AnthropicMessageParam",
    "ResponseInputParam",
    "ToolParam",
    # constants
    "ANTHROPIC_VERSION",
]
