"""Incremental Server-Sent Event parsing helpers.

Conduit proxies three streaming protocols and all of them use the SSE wire
format:

* OpenAI Chat Completions - ``data: {json}`` frames terminated by ``[DONE]``
* Anthropic Messages - ``event: <type>`` followed by ``data: {json}``
* OpenAI Responses - ``data: {json}`` frames carrying a ``type`` field

The parsers below are true incremental parsers: they yield each event as
soon as its terminating blank line arrives and never buffer the whole body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Iterator, List, Optional

__all__ = ["SSEMessage", "iter_sse_messages", "aiter_sse_messages"]


@dataclass
class SSEMessage:
    """A single decoded Server-Sent Event.

    Attributes:
        event: The ``event:`` name, when the server sent one.
        data: The joined ``data:`` payload of the event.
    """

    event: Optional[str] = None
    data: str = ""


def _strip_value(raw: str) -> str:
    """Remove the single optional space that follows an SSE field name."""
    return raw[1:] if raw.startswith(" ") else raw


def iter_sse_messages(lines: Iterator[str]) -> Iterator[SSEMessage]:
    """Parse an iterator of raw lines into :class:`SSEMessage` objects.

    Args:
        lines: An iterator of lines without trailing newlines.

    Yields:
        One :class:`SSEMessage` per completed SSE event.
    """
    event: Optional[str] = None
    data: List[str] = []
    for raw_line in lines:
        line = raw_line.rstrip("\r")
        if line.startswith("event:"):
            event = _strip_value(line[len("event:") :])
        elif line.startswith("data:"):
            data.append(_strip_value(line[len("data:") :]))
        elif line.startswith(":"):
            # Comment / keep-alive frame.
            continue
        elif line == "":
            if data:
                yield SSEMessage(event=event, data="\n".join(data))
                data.clear()
            event = None
    if data:
        yield SSEMessage(event=event, data="\n".join(data))


async def aiter_sse_messages(lines: AsyncIterator[str]) -> AsyncIterator[SSEMessage]:
    """Parse an async iterator of raw lines into :class:`SSEMessage` objects.

    Args:
        lines: An async iterator of lines without trailing newlines.

    Yields:
        One :class:`SSEMessage` per completed SSE event.
    """
    event: Optional[str] = None
    data: List[str] = []
    async for raw_line in lines:
        line = raw_line.rstrip("\r")
        if line.startswith("event:"):
            event = _strip_value(line[len("event:") :])
        elif line.startswith("data:"):
            data.append(_strip_value(line[len("data:") :]))
        elif line.startswith(":"):
            continue
        elif line == "":
            if data:
                yield SSEMessage(event=event, data="\n".join(data))
                data.clear()
            event = None
    if data:
        yield SSEMessage(event=event, data="\n".join(data))
