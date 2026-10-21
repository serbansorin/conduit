"""Exception hierarchy raised by the Conduit SDK.

Every error raised by the SDK derives from :class:`ConduitError`.  HTTP
failures additionally derive from :class:`APIStatusError` so callers can
catch "any non-2xx response" without enumerating status codes.

The gateway reports failures in two shapes:

* the public API uses ``{"error": {"type": "...", "message": "..."}}``
* the admin API mostly uses ``{"error": "some message"}``

Both are normalised into :attr:`ConduitError.message` and
:attr:`ConduitError.error_type`.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

__all__ = [
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
]


class ConduitError(Exception):
    """Base class for every error raised by the Conduit SDK.

    Attributes:
        message: Human readable error message.
        error_type: The gateway-reported error type, e.g. ``authentication_error``.
        status_code: HTTP status code, when the failure came from a response.
        body: Raw response body, when available.
        headers: Response headers, when available.
        request_id: Request identifier reported by the gateway, when available.
    """

    def __init__(
        self,
        message: str,
        *,
        error_type: Optional[str] = None,
        status_code: Optional[int] = None,
        body: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code
        self.body = body
        self.headers = dict(headers) if headers is not None else {}
        self.request_id = request_id

    def __str__(self) -> str:
        """Return the error message."""
        return self.message


class APIError(ConduitError):
    """Base class for errors caused by an HTTP response."""


class APIStatusError(APIError):
    """Raised when the gateway returns a non-2xx status code.

    Attributes:
        response: The originating ``httpx.Response``, when available.
    """

    def __init__(
        self,
        message: str,
        *,
        response: Any = None,
        error_type: Optional[str] = None,
        body: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        status_code = getattr(response, "status_code", None)
        headers = getattr(response, "headers", None)
        super().__init__(
            message,
            error_type=error_type,
            status_code=status_code,
            body=body,
            headers=headers,
            request_id=request_id,
        )
        self.response = response


class BadRequestError(APIStatusError):
    """HTTP 400 - the request was rejected as invalid."""


class AuthenticationError(APIStatusError):
    """HTTP 401 - the gateway token is missing, invalid, expired or disabled."""


class PermissionDeniedError(APIStatusError):
    """HTTP 403 - the token is not allowed to use the requested model group."""


class NotFoundError(APIStatusError):
    """HTTP 404 - the requested resource does not exist."""


class RateLimitError(APIStatusError):
    """HTTP 429 - the token exceeded its rate limit.

    Attributes:
        retry_after: Value of the ``Retry-After`` response header in seconds,
            when the gateway sent one.
    """

    def __init__(
        self,
        message: str,
        *,
        response: Any = None,
        error_type: Optional[str] = None,
        body: Optional[str] = None,
        request_id: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(
            message,
            response=response,
            error_type=error_type,
            body=body,
            request_id=request_id,
        )
        self.retry_after = retry_after


class ServerError(APIStatusError):
    """HTTP 5xx - the gateway or one of its upstreams failed."""


class APIConnectionError(ConduitError):
    """The request could not be delivered to the gateway."""


class APITimeoutError(APIConnectionError):
    """The request to the gateway timed out."""
