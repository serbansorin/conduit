"""Internal helpers shared by the public clients.

Nothing in this module is part of the public API surface.
"""

from __future__ import annotations

import json
from typing import Mapping, Optional, Tuple

import httpx

from .exceptions import (
    APIConnectionError,
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

__all__ = [
    "DEFAULT_BASE_URL",
    "normalize_base_url",
    "parse_error_payload",
    "build_api_error",
    "wrap_transport_error",
    "request_id_from_headers",
]

DEFAULT_BASE_URL = "http://localhost:9812"
"""Default address of a locally running Conduit gateway."""

_MAX_ERROR_BODY = 1024


def normalize_base_url(base_url: str) -> str:
    """Normalise a gateway base URL.

    Args:
        base_url: The user supplied base URL.

    Returns:
        The base URL without a trailing slash.

    Raises:
        ValueError: If ``base_url`` is empty or not an HTTP(S) URL.
    """
    candidate = (base_url or "").strip()
    if not candidate:
        raise ValueError("base_url must not be empty")
    if not candidate.startswith(("http://", "https://")):
        raise ValueError(
            "base_url must start with http:// or https://, got: {0!r}".format(base_url)
        )
    return candidate.rstrip("/")


def parse_error_payload(body: str) -> Tuple[Optional[str], str]:
    """Extract the error type and message from a gateway error body.

    The public API uses ``{"error": {"type": ..., "message": ...}}`` while the
    admin API mostly uses ``{"error": "message"}``.  Both shapes are handled.

    Args:
        body: The raw response body.

    Returns:
        A tuple of ``(error_type, message)``.  ``error_type`` is ``None`` when
        the payload did not carry one.
    """
    text = (body or "").strip()
    if not text:
        return None, ""
    try:
        payload = json.loads(text)
    except ValueError:
        return None, text[:_MAX_ERROR_BODY]

    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping):
            error_type = error.get("type")
            message = error.get("message") or error.get("detail") or ""
            return (
                str(error_type) if error_type else None,
                str(message) if message else "",
            )
        if isinstance(error, str):
            return None, error
        for key in ("message", "detail", "error_description"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                error_type = payload.get("type")
                return (str(error_type) if error_type else None, value)
    if isinstance(payload, str):
        return None, payload
    return None, text[:_MAX_ERROR_BODY]


def request_id_from_headers(headers: Mapping[str, str]) -> Optional[str]:
    """Return the request id reported by the gateway, when present.

    Args:
        headers: The response headers.

    Returns:
        The request id, or ``None``.
    """
    for key in ("x-request-id", "x-requestid", "request-id"):
        value = headers.get(key)
        if value:
            return value
    return None


def _retry_after(headers: Mapping[str, str]) -> Optional[float]:
    """Parse the ``Retry-After`` header into a number of seconds."""
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw.strip())
    except ValueError:
        return None


def build_api_error(
    response: httpx.Response, body: Optional[str] = None
) -> ConduitError:
    """Map an HTTP error response onto the matching SDK exception.

    Args:
        response: The failing response.
        body: The already-read response body, when available.

    Returns:
        An exception instance ready to be raised.
    """
    text = body if body is not None else _safe_text(response)
    error_type, message = parse_error_payload(text)
    status = response.status_code
    if not message:
        message = "conduit request failed with HTTP {0}".format(status)
    request_id = request_id_from_headers(response.headers)

    if status == 400:
        return BadRequestError(
            message,
            response=response,
            error_type=error_type,
            body=text,
            request_id=request_id,
        )
    if status == 401:
        return AuthenticationError(
            message,
            response=response,
            error_type=error_type,
            body=text,
            request_id=request_id,
        )
    if status == 403:
        return PermissionDeniedError(
            message,
            response=response,
            error_type=error_type,
            body=text,
            request_id=request_id,
        )
    if status == 404:
        return NotFoundError(
            message,
            response=response,
            error_type=error_type,
            body=text,
            request_id=request_id,
        )
    if status == 429:
        return RateLimitError(
            message,
            response=response,
            error_type=error_type,
            body=text,
            request_id=request_id,
            retry_after=_retry_after(response.headers),
        )
    if status >= 500:
        return ServerError(
            message,
            response=response,
            error_type=error_type,
            body=text,
            request_id=request_id,
        )
    return APIStatusError(
        message,
        response=response,
        error_type=error_type,
        body=text,
        request_id=request_id,
    )


def _safe_text(response: httpx.Response) -> str:
    """Read the response body defensively, never raising."""
    try:
        return response.text
    except Exception:  # pragma: no cover - defensive only
        return ""


def wrap_transport_error(exc: Exception) -> ConduitError:
    """Convert an httpx transport failure into an SDK exception.

    Args:
        exc: The exception raised by httpx.

    Returns:
        An :class:`APITimeoutError` for timeouts, otherwise an
        :class:`APIConnectionError`.
    """
    if isinstance(exc, httpx.TimeoutException):
        return APITimeoutError("request to conduit timed out: {0}".format(exc))
    return APIConnectionError("could not reach conduit: {0}".format(exc))
