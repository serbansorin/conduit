"""Shared pytest fixtures and helpers for the Conduit SDK test suite.

The suite never talks to a live gateway: every test drives the clients
through :class:`httpx.MockTransport` and recorded JSON fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"

Responder = Callable[[httpx.Request], httpx.Response]
Route = Tuple[str, str, Responder]


def fixture_path(name: str) -> Path:
    """Return the path of a fixture file.

    Args:
        name: File name of the fixture.

    Returns:
        The absolute path of the fixture.
    """
    return FIXTURES / name


def load_text(name: str) -> str:
    """Read a fixture file as text.

    Args:
        name: File name of the fixture.

    Returns:
        The file content.
    """
    return fixture_path(name).read_text(encoding="utf-8")


def load_json(name: str) -> Any:
    """Read and decode a JSON fixture.

    Args:
        name: File name of the fixture.

    Returns:
        The decoded payload.
    """
    return json.loads(load_text(name))


def json_response(name: str, status_code: int = 200) -> httpx.Response:
    """Build a JSON response from a fixture.

    Args:
        name: File name of the fixture.
        status_code: HTTP status code of the response.

    Returns:
        The response object.
    """
    return httpx.Response(
        status_code,
        content=load_text(name).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def sse_response(name: str) -> httpx.Response:
    """Build a Server-Sent Events response from a fixture.

    Args:
        name: File name of the fixture.

    Returns:
        The response object.
    """
    return httpx.Response(
        200,
        content=load_text(name).encode("utf-8"),
        headers={"Content-Type": "text/event-stream"},
    )


def streaming_response(parts: List[bytes]) -> httpx.Response:
    """Build a response whose body is produced lazily, part by part.

    Args:
        parts: The byte chunks the upstream would send.

    Returns:
        The response object.
    """
    return httpx.Response(
        200,
        content=iter(parts),
        headers={"Content-Type": "text/event-stream"},
    )


class Recorder:
    """A mock transport that records requests and answers from a route table.

    Attributes:
        requests: Every request seen by the transport, in order.
    """

    def __init__(self, routes: Optional[List[Route]] = None) -> None:
        self.routes: List[Route] = list(routes or [])
        self.requests: List[httpx.Request] = []

    def add(self, method: str, path: str, responder: Responder) -> "Recorder":
        """Register a route.

        Args:
            method: HTTP method to match.
            path: Request path to match.
            responder: Callable producing the response.

        Returns:
            The recorder, for chaining.
        """
        self.routes.append((method, path, responder))
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Answer a request, recording it first.

        Args:
            request: The incoming request.

        Returns:
            The response for the first matching route.
        """
        self.requests.append(request)
        for method, path, responder in self.routes:
            if request.method == method and request.url.path == path:
                return responder(request)
        return httpx.Response(
            404,
            json={
                "error": {
                    "type": "not_found",
                    "message": "no route for {0} {1}".format(
                        request.method, request.url.path
                    ),
                }
            },
        )

    @property
    def transport(self) -> httpx.MockTransport:
        """Return the httpx transport bound to this recorder."""
        return httpx.MockTransport(self.handler)

    def paths(self) -> List[str]:
        """Return the recorded request paths."""
        return [request.url.path for request in self.requests]

    def body_of(self, index: int) -> Dict[str, Any]:
        """Return the decoded JSON body of a recorded request.

        Args:
            index: Index of the recorded request.

        Returns:
            The decoded body.
        """
        content = self.requests[index].content
        return json.loads(content.decode("utf-8")) if content else {}


@pytest.fixture()
def recorder() -> Recorder:
    """Return a fresh request recorder."""
    return Recorder()
