"""A LangChain chat model backed by the Conduit gateway.

Every model call goes through Conduit, so the crew inherits the gateway's
key pooling, protocol conversion, rate limiting and cost accounting. Nothing
in this package talks to a model provider directly.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult

__all__ = ["ConduitChatModel", "build_chat_model"]

log = logging.getLogger(__name__)


def _split_system(messages: Sequence[BaseMessage]) -> tuple[str, List[BaseMessage]]:
    """Separate system messages from the rest.

    Args:
        messages: The conversation.

    Returns:
        The joined system text and the remaining messages.
    """
    system_parts: List[str] = []
    rest: List[BaseMessage] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            system_parts.append(str(message.content))
        else:
            rest.append(message)
    return "\n\n".join(part for part in system_parts if part), rest


class ConduitChatModel(BaseChatModel):
    """A chat model that talks to Conduit's Anthropic Messages endpoint.

    Attributes:
        base_url: The gateway base URL.
        api_key: The gateway token.
        model: The model id the gateway should serve.
        temperature: Sampling temperature.
        max_tokens: Response cap.
        timeout: Request timeout in seconds.
    """

    base_url: str = "http://localhost:9812"
    api_key: str = ""
    model: str = "glm-5.3"
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout: float = 120.0

    @property
    def _llm_type(self) -> str:
        """Return the provider identifier LangChain reports."""
        return "conduit"

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        """Return the parameters that identify this model instance."""
        return {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "base_url": self.base_url,
        }

    def _client(self) -> Any:
        """Build a Conduit client for one call.

        Returns:
            A configured client from the ``conduit`` SDK.
        """
        from conduit import Client

        return Client(
            base_url=self.base_url,
            api_key=self.api_key or None,
            timeout=self.timeout,
        )

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Generate one completion.

        Args:
            messages: The conversation, including any system message.
            stop: Optional stop sequences.
            run_manager: LangChain's callback manager.
            **kwargs: Ignored extra generation parameters.

        Returns:
            The model result.

        Raises:
            ValueError: If the gateway returned no content.
        """
        system_text, conversation = _split_system(messages)
        if system_text:
            conversation = [SystemMessage(content=system_text), *conversation]

        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [_to_anthropic(m) for m in conversation],
        }
        if stop:
            payload["stop_sequences"] = list(stop)

        client = self._client()
        response = client.messages.create(**payload)

        text = _extract_text(response)
        if not text:
            raise ValueError("the gateway returned an empty response")

        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=text))]
        )


def _to_anthropic(message: BaseMessage) -> Dict[str, Any]:
    """Convert a LangChain message into the Anthropic wire format.

    Args:
        message: The message to convert.

    Returns:
        The message as a mapping.
    """
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": str(message.content)}
    if isinstance(message, AIMessage):
        return {"role": "assistant", "content": str(message.content)}
    if isinstance(message, SystemMessage):
        return {"role": "user", "content": str(message.content)}
    return {"role": "user", "content": str(message.content)}


def _extract_text(response: Any) -> str:
    """Pull the text out of a Messages response.

    Args:
        response: The SDK response object.

    Returns:
        The concatenated text blocks.
    """
    content = getattr(response, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(str(text))
        return "\n".join(parts)
    return str(content)


def build_chat_model(
    base_url: str,
    api_key: str,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> ConduitChatModel:
    """Build a Conduit-backed chat model.

    Args:
        base_url: The gateway base URL.
        api_key: The gateway token.
        model: The model id.
        temperature: Sampling temperature.
        max_tokens: Response cap.

    Returns:
        The chat model.
    """
    return ConduitChatModel(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _dump(payload: Any) -> str:
    """Render a payload for logging.

    Args:
        payload: The payload to render.

    Returns:
        A JSON string.
    """
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 - logging must never raise
        return str(payload)
