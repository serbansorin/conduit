"""Admin API clients for the Conduit gateway.

The admin API is protected by a password based login that returns a short
lived JWT.  Both clients perform the login lazily: the first authenticated
call exchanges the configured password for a token and caches it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import quote

import httpx

from ._common import (
    DEFAULT_BASE_URL,
    build_api_error,
    normalize_base_url,
    wrap_transport_error,
)
from ._version import __version__
from .exceptions import ConduitError
from .models import (
    AdminKey,
    AdminModelRoute,
    AdminToken,
    ModelMapping,
    UsageLogPage,
)
from .types import JSONObject

__all__ = ["AdminClient", "AsyncAdminClient"]

_USER_AGENT = "conduit-python/{0}".format(__version__)
_ADMIN_PREFIX = "/admin"


def _path_id(value: Any) -> str:
    """Encode a path parameter so it can be embedded in a URL."""
    return quote(str(value), safe="")


def _path_tail(value: str) -> str:
    """Encode a trailing path segment, keeping slashes intact."""
    return quote(value, safe="/")


class _AdminBase:
    """Shared state of the admin clients."""

    def __init__(
        self,
        base_url: str,
        password: Optional[str],
        default_headers: Optional[Mapping[str, str]],
        extra_query: Optional[Mapping[str, Any]],
        user_agent: str,
    ) -> None:
        self._base_url = normalize_base_url(base_url)
        self._password = password
        self._default_headers: Dict[str, str] = dict(default_headers or {})
        self._extra_query: Dict[str, Any] = dict(extra_query or {})
        self._user_agent = user_agent
        self._token: Optional[str] = None

    @property
    def base_url(self) -> str:
        """The normalised base URL of the gateway."""
        return self._base_url

    @property
    def token(self) -> Optional[str]:
        """The admin JWT, or ``None`` when the client has not logged in yet."""
        return self._token

    @property
    def password(self) -> Optional[str]:
        """The admin password used for the login call."""
        return self._password

    def _url(self, path: str) -> str:
        """Build an absolute URL for an API path."""
        return "{0}{1}".format(self._base_url, path)

    def _headers(self, authenticated: bool = True) -> Dict[str, str]:
        """Build the header set for an admin request."""
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }
        headers.update(self._default_headers)
        if authenticated and self._token:
            headers.setdefault("Authorization", "Bearer {0}".format(self._token))
        return headers

    def _params(self, extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """Merge the client-wide query parameters with per-request ones."""
        params: Dict[str, Any] = dict(self._extra_query)
        if extra:
            params.update(extra)
        return params

    def _extract_token(self, payload: JSONObject) -> str:
        """Pull the JWT out of a login response."""
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise ConduitError("conduit admin login did not return a token")
        return token


class AdminClient(_AdminBase):
    """Synchronous client for the Conduit admin API.

    Example:
        >>> from conduit import AdminClient
        >>> admin = AdminClient(base_url="http://localhost:9812", password="secret")
        >>> admin.login()
        'eyJhbGciOi...'
        >>> [key.label for key in admin.list_keys()]

    Args:
        base_url: Base URL of the gateway.
        password: Admin password used for the login call.
        timeout: Per-request timeout in seconds.
        default_headers: Headers added to every request.
        extra_query: Query parameters added to every request.
        transport: Optional httpx transport, mainly useful for tests.
        user_agent: Override the ``User-Agent`` header.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        password: Optional[str] = None,
        timeout: float = 30.0,
        *,
        default_headers: Optional[Mapping[str, str]] = None,
        extra_query: Optional[Mapping[str, Any]] = None,
        transport: Optional[httpx.BaseTransport] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        super().__init__(
            base_url,
            password,
            default_headers,
            extra_query,
            user_agent or _USER_AGENT,
        )
        self._http = httpx.Client(timeout=timeout, transport=transport)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    def __enter__(self) -> "AdminClient":
        """Enter a ``with`` block."""
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """Close the client when leaving a ``with`` block."""
        self.close()

    # -- request helpers ---------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        authenticated: bool = True,
    ) -> httpx.Response:
        """Perform a request and translate transport failures."""
        try:
            return self._http.request(
                method,
                self._url(path),
                json=json_body,
                params=self._params(params) or None,
                headers=self._headers(authenticated=authenticated),
            )
        except httpx.TimeoutException as exc:
            raise wrap_transport_error(exc) from exc
        except httpx.HTTPError as exc:
            raise wrap_transport_error(exc) from exc

    def _json_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        authenticated: bool = True,
    ) -> JSONObject:
        """Perform a request and decode a JSON object response."""
        response = self._request(
            method,
            path,
            json_body=json_body,
            params=params,
            authenticated=authenticated,
        )
        if response.status_code >= 400:
            raise build_api_error(response)
        try:
            payload = response.json()
        except ValueError:
            return {}
        if not isinstance(payload, Mapping):
            return {"value": payload}
        return dict(payload)

    def _ensure_token(self) -> str:
        """Log in on demand and return the admin JWT."""
        if not self._token:
            self.login()
        assert self._token is not None
        return self._token

    # -- authentication ----------------------------------------------------

    def login(self, password: Optional[str] = None) -> str:
        """Exchange the admin password for a JWT.

        Args:
            password: Password to use; defaults to the configured one.

        Returns:
            The signed admin JWT.

        Raises:
            ConduitError: If the password is missing or wrong.
        """
        secret = password if password is not None else self._password
        if not secret:
            raise ConduitError(
                "an admin password is required: pass password=... or set CONDUIT_ADMIN_PASSWORD"
            )
        payload = self._json_request(
            "POST",
            _ADMIN_PREFIX + "/login",
            json_body={"password": secret},
            authenticated=False,
        )
        self._token = self._extract_token(payload)
        return self._token

    # -- keys --------------------------------------------------------------

    def list_keys(self, group: str = "go") -> List[AdminKey]:
        """List the upstream keys of a key-pool group.

        Args:
            group: Key-pool group, ``go`` or ``ollama``.

        Returns:
            The configured keys.
        """
        self._ensure_token()
        payload = self._json_request(
            "GET", _ADMIN_PREFIX + "/keys", params={"group": group}
        )
        data = payload.get("data")
        items = data if isinstance(data, list) else []
        return [AdminKey.from_dict(item) for item in items if isinstance(item, Mapping)]

    def create_key(
        self,
        value: str,
        *,
        group: str = "go",
        label: str = "",
        weight: int = 1,
        proxy_url: str = "",
        cookie: str = "",
        workspace_id: str = "",
    ) -> AdminKey:
        """Add an upstream key.

        Args:
            value: The upstream key value.
            group: Key-pool group, ``go`` or ``ollama``.
            label: Human readable label.
            weight: Selection weight inside the group.
            proxy_url: Optional proxy used for upstream calls.
            cookie: Optional upstream session cookie used for quota lookups.
            workspace_id: Optional upstream workspace id.

        Returns:
            The created key.
        """
        self._ensure_token()
        payload = self._json_request(
            "POST",
            _ADMIN_PREFIX + "/keys",
            json_body={
                "value": value,
                "group": group,
                "label": label,
                "weight": weight,
                "proxy_url": proxy_url,
                "cookie": cookie,
                "workspace_id": workspace_id,
            },
        )
        return AdminKey.from_dict(payload)

    def update_key(self, key_id: Any, **fields: Any) -> AdminKey:
        """Update the settings of an upstream key.

        Args:
            key_id: Identifier of the key.
            **fields: Fields to update, e.g. ``label``, ``weight``,
                ``enabled``, ``proxy_url``, ``cookie`` or ``workspace_id``.

        Returns:
            The updated key.
        """
        self._ensure_token()
        payload = self._json_request(
            "PATCH",
            "{0}/keys/{1}".format(_ADMIN_PREFIX, _path_id(key_id)),
            json_body=dict(fields),
        )
        return AdminKey.from_dict(payload)

    def toggle_key(self, key_id: Any) -> AdminKey:
        """Enable or disable an upstream key.

        Args:
            key_id: Identifier of the key.

        Returns:
            The key after the toggle.
        """
        self._ensure_token()
        payload = self._json_request(
            "POST", "{0}/keys/{1}/toggle".format(_ADMIN_PREFIX, _path_id(key_id))
        )
        return AdminKey.from_dict(payload)

    def reset_key_cooldown(self, key_id: Any) -> JSONObject:
        """Clear the cooldown of an upstream key.

        Args:
            key_id: Identifier of the key.

        Returns:
            The raw gateway response.
        """
        self._ensure_token()
        return self._json_request(
            "POST", "{0}/keys/{1}/reset".format(_ADMIN_PREFIX, _path_id(key_id))
        )

    def fetch_key_quota(self, key_id: Any) -> JSONObject:
        """Fetch the upstream quota of a key.

        Args:
            key_id: Identifier of the key.

        Returns:
            The raw quota payload.
        """
        self._ensure_token()
        return self._json_request(
            "GET", "{0}/keys/{1}/quota".format(_ADMIN_PREFIX, _path_id(key_id))
        )

    def delete_key(self, key_id: Any) -> JSONObject:
        """Delete an upstream key.

        Args:
            key_id: Identifier of the key.

        Returns:
            The raw gateway response.
        """
        self._ensure_token()
        return self._json_request(
            "DELETE", "{0}/keys/{1}".format(_ADMIN_PREFIX, _path_id(key_id))
        )

    # -- tokens ------------------------------------------------------------

    def list_tokens(self) -> List[AdminToken]:
        """List the gateway tokens.

        Returns:
            The configured tokens together with their usage aggregates.
        """
        self._ensure_token()
        payload = self._json_request("GET", _ADMIN_PREFIX + "/tokens")
        data = payload.get("data")
        items = data if isinstance(data, list) else []
        return [AdminToken.from_dict(item) for item in items if isinstance(item, Mapping)]

    def create_token(
        self,
        name: str,
        *,
        description: str = "",
        allowed_groups: str = "",
        rate_limit: int = 0,
        max_requests: int = 0,
        expires_at: Optional[str] = None,
    ) -> AdminToken:
        """Create a gateway token.

        Args:
            name: Human readable name of the token.
            description: Free-form description.
            allowed_groups: Comma-separated list of allowed key-pool groups.
            rate_limit: Requests per minute, ``0`` means unlimited.
            max_requests: Total request cap, ``0`` means unlimited.
            expires_at: Optional expiry timestamp in RFC 3339 format.

        Returns:
            The created token.
        """
        self._ensure_token()
        body: Dict[str, Any] = {
            "name": name,
            "description": description,
            "allowed_groups": allowed_groups,
            "rate_limit": rate_limit,
            "max_requests": max_requests,
        }
        if expires_at is not None:
            body["expires_at"] = expires_at
        payload = self._json_request(
            "POST", _ADMIN_PREFIX + "/tokens", json_body=body
        )
        return AdminToken.from_dict(payload)

    def update_token(self, token_id: Any, **fields: Any) -> AdminToken:
        """Update a gateway token.

        Args:
            token_id: Identifier of the token.
            **fields: Fields to update, e.g. ``name``, ``description``,
                ``rate_limit``, ``max_requests``, ``enabled`` or ``expires_at``.

        Returns:
            The updated token.
        """
        self._ensure_token()
        payload = self._json_request(
            "PATCH",
            "{0}/tokens/{1}".format(_ADMIN_PREFIX, _path_id(token_id)),
            json_body=dict(fields),
        )
        return AdminToken.from_dict(payload)

    def delete_token(self, token_id: Any) -> JSONObject:
        """Delete a gateway token.

        Args:
            token_id: Identifier of the token.

        Returns:
            The raw gateway response.
        """
        self._ensure_token()
        return self._json_request(
            "DELETE", "{0}/tokens/{1}".format(_ADMIN_PREFIX, _path_id(token_id))
        )

    # -- model routes ------------------------------------------------------

    def list_models(self) -> List[AdminModelRoute]:
        """List the configured model routes.

        Returns:
            The model routes.
        """
        self._ensure_token()
        payload = self._json_request("GET", _ADMIN_PREFIX + "/models")
        data = payload.get("data")
        items = data if isinstance(data, list) else []
        return [
            AdminModelRoute.from_dict(item) for item in items if isinstance(item, Mapping)
        ]

    def sync_models(self) -> JSONObject:
        """Synchronise the model catalog with the upstream catalog.

        Returns:
            The raw sync result.
        """
        self._ensure_token()
        return self._json_request("POST", _ADMIN_PREFIX + "/models/sync")

    def rebuild_models(self, confirmation: str = "REBUILD") -> JSONObject:
        """Replace the model catalog with a fresh upstream snapshot.

        Args:
            confirmation: Must be ``REBUILD``; the gateway rejects anything else.

        Returns:
            The raw rebuild result.
        """
        self._ensure_token()
        return self._json_request(
            "POST",
            _ADMIN_PREFIX + "/models/rebuild",
            json_body={"confirmation": confirmation},
        )

    def upsert_model(
        self,
        model_id: str,
        *,
        protocol: str = "chat",
        upstream: str = "go",
        group: str = "",
        name: Optional[str] = None,
        real_model: Optional[str] = None,
        context_len: Optional[int] = None,
        status: Optional[int] = None,
        priority: Optional[int] = None,
        tags: Optional[Sequence[str]] = None,
        pricing: Optional[Mapping[str, str]] = None,
        **extra: Any,
    ) -> AdminModelRoute:
        """Create or replace a model route.

        Args:
            model_id: Gateway-facing model id.
            protocol: Wire protocol of the model.
            upstream: Primary upstream, ``go`` or ``ollama``.
            group: Key-pool group; defaults to the upstream name.
            name: Display name.
            real_model: Upstream model id.
            context_len: Context window hint.
            status: ``1`` enables the route, ``0`` disables it.
            priority: Admin-defined ordering hint.
            tags: Capability tags.
            pricing: Per-token prices keyed by billing dimension.
            **extra: Additional body fields, e.g. ``upstreams`` or ``targets``.

        Returns:
            The stored model route.
        """
        self._ensure_token()
        body: Dict[str, Any] = {
            "id": model_id,
            "protocol": protocol,
            "upstream": upstream,
        }
        if group:
            body["group"] = group
        if name is not None:
            body["name"] = name
        if real_model is not None:
            body["real_model"] = real_model
        if context_len is not None:
            body["context_len"] = context_len
        if status is not None:
            body["status"] = status
        if priority is not None:
            body["priority"] = priority
        if tags is not None:
            body["tags"] = list(tags)
        if pricing is not None:
            body["pricing"] = dict(pricing)
        body.update(extra)
        payload = self._json_request(
            "POST", _ADMIN_PREFIX + "/models", json_body=body
        )
        return AdminModelRoute.from_dict(payload)

    def update_model(self, model_id: str, **fields: Any) -> AdminModelRoute:
        """Update a model route.

        Args:
            model_id: Gateway-facing model id.
            **fields: Fields to update, e.g. ``name``, ``protocol``,
                ``real_model``, ``group``, ``context_len``, ``status``,
                ``priority``, ``tags`` or ``pricing``.

        Returns:
            The updated model route.
        """
        self._ensure_token()
        payload = self._json_request(
            "PATCH",
            "{0}/models/{1}".format(_ADMIN_PREFIX, _path_id(model_id)),
            json_body=dict(fields),
        )
        return AdminModelRoute.from_dict(payload)

    def toggle_model(self, model_id: str) -> AdminModelRoute:
        """Enable or disable a model route.

        Args:
            model_id: Gateway-facing model id.

        Returns:
            The model route after the toggle.
        """
        self._ensure_token()
        payload = self._json_request(
            "POST", "{0}/models/{1}/toggle".format(_ADMIN_PREFIX, _path_id(model_id))
        )
        return AdminModelRoute.from_dict(payload)

    def delete_model(self, model_id: str) -> JSONObject:
        """Delete a model route.

        Args:
            model_id: Gateway-facing model id.

        Returns:
            The raw gateway response.
        """
        self._ensure_token()
        return self._json_request(
            "DELETE", "{0}/models/{1}".format(_ADMIN_PREFIX, _path_id(model_id))
        )

    # -- model mappings ----------------------------------------------------

    def list_model_mappings(self) -> List[ModelMapping]:
        """List the model rewrite rules.

        Returns:
            The configured mappings.
        """
        self._ensure_token()
        payload = self._json_request("GET", _ADMIN_PREFIX + "/model-mappings")
        data = payload.get("data")
        items = data if isinstance(data, list) else []
        return [ModelMapping.from_dict(item) for item in items if isinstance(item, Mapping)]

    def upsert_model_mapping(self, source_model: str, target_model: str) -> ModelMapping:
        """Create or replace a model rewrite rule.

        Args:
            source_model: The model id requested by the client.
            target_model: The model id served instead.

        Returns:
            The stored mapping.
        """
        self._ensure_token()
        payload = self._json_request(
            "POST",
            _ADMIN_PREFIX + "/model-mappings",
            json_body={"source_model": source_model, "target_model": target_model},
        )
        return ModelMapping.from_dict(payload)

    def delete_model_mapping(self, source_model: str) -> JSONObject:
        """Delete a model rewrite rule.

        Args:
            source_model: The model id requested by the client.

        Returns:
            The raw gateway response.
        """
        self._ensure_token()
        return self._json_request(
            "DELETE",
            "{0}/model-mappings/{1}".format(_ADMIN_PREFIX, _path_tail(source_model)),
        )

    # -- observability -----------------------------------------------------

    def stats(self) -> JSONObject:
        """Return the usage statistics of the gateway.

        Returns:
            The raw statistics payload.
        """
        self._ensure_token()
        return self._json_request("GET", _ADMIN_PREFIX + "/stats")

    def usage_logs(self, page: int = 1, page_size: int = 25) -> UsageLogPage:
        """Return a page of usage logs.

        Args:
            page: Page number, 1-based.
            page_size: Number of rows per page, capped at 200 by the gateway.

        Returns:
            The requested page of usage logs.
        """
        self._ensure_token()
        payload = self._json_request(
            "GET",
            _ADMIN_PREFIX + "/usage",
            params={"page": page, "page_size": page_size},
        )
        return UsageLogPage.from_dict(payload)

    def health(self) -> JSONObject:
        """Return the pool health of the gateway.

        Returns:
            The raw health payload.
        """
        self._ensure_token()
        return self._json_request("GET", _ADMIN_PREFIX + "/health")

    def version(self) -> JSONObject:
        """Return the running version of the gateway.

        Returns:
            The raw version payload.
        """
        self._ensure_token()
        return self._json_request("GET", _ADMIN_PREFIX + "/version")

    def backup(self) -> JSONObject:
        """Export the whole gateway configuration.

        The export contains upstream key values, gateway tokens and session
        cookies, so it must be treated as a secret.

        Returns:
            The backup document.
        """
        self._ensure_token()
        return self._json_request("GET", _ADMIN_PREFIX + "/backup")

    def restore(self, backup: Mapping[str, Any]) -> JSONObject:
        """Restore a configuration export.

        Existing entries are updated in place, so restoring the same export
        twice is safe.

        Args:
            backup: A document produced by :meth:`backup`.

        Returns:
            A summary of how many entries of each kind were restored.
        """
        self._ensure_token()
        return self._json_request(
            "POST", _ADMIN_PREFIX + "/restore", json_body=backup
        )


class AsyncAdminClient(_AdminBase):
    """Asynchronous client for the Conduit admin API.

    Example:
        >>> from conduit import AsyncAdminClient
        >>> admin = AsyncAdminClient(base_url="http://localhost:9812", password="secret")
        >>> await admin.login()
        'eyJhbGciOi...'

    Args:
        base_url: Base URL of the gateway.
        password: Admin password used for the login call.
        timeout: Per-request timeout in seconds.
        default_headers: Headers added to every request.
        extra_query: Query parameters added to every request.
        transport: Optional httpx transport, mainly useful for tests.
        user_agent: Override the ``User-Agent`` header.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        password: Optional[str] = None,
        timeout: float = 30.0,
        *,
        default_headers: Optional[Mapping[str, str]] = None,
        extra_query: Optional[Mapping[str, Any]] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        super().__init__(
            base_url,
            password,
            default_headers,
            extra_query,
            user_agent or _USER_AGENT,
        )
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)

    # -- lifecycle ---------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncAdminClient":
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
        authenticated: bool = True,
    ) -> httpx.Response:
        """Perform a request and translate transport failures."""
        try:
            return await self._http.request(
                method,
                self._url(path),
                json=json_body,
                params=self._params(params) or None,
                headers=self._headers(authenticated=authenticated),
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
        authenticated: bool = True,
    ) -> JSONObject:
        """Perform a request and decode a JSON object response."""
        response = await self._request(
            method,
            path,
            json_body=json_body,
            params=params,
            authenticated=authenticated,
        )
        if response.status_code >= 400:
            raise build_api_error(response)
        try:
            payload = response.json()
        except ValueError:
            return {}
        if not isinstance(payload, Mapping):
            return {"value": payload}
        return dict(payload)

    async def _ensure_token(self) -> str:
        """Log in on demand and return the admin JWT."""
        if not self._token:
            await self.login()
        assert self._token is not None
        return self._token

    # -- authentication ----------------------------------------------------

    async def login(self, password: Optional[str] = None) -> str:
        """Exchange the admin password for a JWT.

        Args:
            password: Password to use; defaults to the configured one.

        Returns:
            The signed admin JWT.

        Raises:
            ConduitError: If the password is missing or wrong.
        """
        secret = password if password is not None else self._password
        if not secret:
            raise ConduitError(
                "an admin password is required: pass password=... or set CONDUIT_ADMIN_PASSWORD"
            )
        payload = await self._json_request(
            "POST",
            _ADMIN_PREFIX + "/login",
            json_body={"password": secret},
            authenticated=False,
        )
        self._token = self._extract_token(payload)
        return self._token

    # -- keys --------------------------------------------------------------

    async def list_keys(self, group: str = "go") -> List[AdminKey]:
        """List the upstream keys of a key-pool group."""
        await self._ensure_token()
        payload = await self._json_request(
            "GET", _ADMIN_PREFIX + "/keys", params={"group": group}
        )
        data = payload.get("data")
        items = data if isinstance(data, list) else []
        return [AdminKey.from_dict(item) for item in items if isinstance(item, Mapping)]

    async def create_key(
        self,
        value: str,
        *,
        group: str = "go",
        label: str = "",
        weight: int = 1,
        proxy_url: str = "",
        cookie: str = "",
        workspace_id: str = "",
    ) -> AdminKey:
        """Add an upstream key."""
        await self._ensure_token()
        payload = await self._json_request(
            "POST",
            _ADMIN_PREFIX + "/keys",
            json_body={
                "value": value,
                "group": group,
                "label": label,
                "weight": weight,
                "proxy_url": proxy_url,
                "cookie": cookie,
                "workspace_id": workspace_id,
            },
        )
        return AdminKey.from_dict(payload)

    async def update_key(self, key_id: Any, **fields: Any) -> AdminKey:
        """Update the settings of an upstream key."""
        await self._ensure_token()
        payload = await self._json_request(
            "PATCH",
            "{0}/keys/{1}".format(_ADMIN_PREFIX, _path_id(key_id)),
            json_body=dict(fields),
        )
        return AdminKey.from_dict(payload)

    async def toggle_key(self, key_id: Any) -> AdminKey:
        """Enable or disable an upstream key."""
        await self._ensure_token()
        payload = await self._json_request(
            "POST", "{0}/keys/{1}/toggle".format(_ADMIN_PREFIX, _path_id(key_id))
        )
        return AdminKey.from_dict(payload)

    async def reset_key_cooldown(self, key_id: Any) -> JSONObject:
        """Clear the cooldown of an upstream key."""
        await self._ensure_token()
        return await self._json_request(
            "POST", "{0}/keys/{1}/reset".format(_ADMIN_PREFIX, _path_id(key_id))
        )

    async def fetch_key_quota(self, key_id: Any) -> JSONObject:
        """Fetch the upstream quota of a key."""
        await self._ensure_token()
        return await self._json_request(
            "GET", "{0}/keys/{1}/quota".format(_ADMIN_PREFIX, _path_id(key_id))
        )

    async def delete_key(self, key_id: Any) -> JSONObject:
        """Delete an upstream key."""
        await self._ensure_token()
        return await self._json_request(
            "DELETE", "{0}/keys/{1}".format(_ADMIN_PREFIX, _path_id(key_id))
        )

    # -- tokens ------------------------------------------------------------

    async def list_tokens(self) -> List[AdminToken]:
        """List the gateway tokens."""
        await self._ensure_token()
        payload = await self._json_request("GET", _ADMIN_PREFIX + "/tokens")
        data = payload.get("data")
        items = data if isinstance(data, list) else []
        return [AdminToken.from_dict(item) for item in items if isinstance(item, Mapping)]

    async def create_token(
        self,
        name: str,
        *,
        description: str = "",
        allowed_groups: str = "",
        rate_limit: int = 0,
        max_requests: int = 0,
        expires_at: Optional[str] = None,
    ) -> AdminToken:
        """Create a gateway token."""
        await self._ensure_token()
        body: Dict[str, Any] = {
            "name": name,
            "description": description,
            "allowed_groups": allowed_groups,
            "rate_limit": rate_limit,
            "max_requests": max_requests,
        }
        if expires_at is not None:
            body["expires_at"] = expires_at
        payload = await self._json_request(
            "POST", _ADMIN_PREFIX + "/tokens", json_body=body
        )
        return AdminToken.from_dict(payload)

    async def update_token(self, token_id: Any, **fields: Any) -> AdminToken:
        """Update a gateway token."""
        await self._ensure_token()
        payload = await self._json_request(
            "PATCH",
            "{0}/tokens/{1}".format(_ADMIN_PREFIX, _path_id(token_id)),
            json_body=dict(fields),
        )
        return AdminToken.from_dict(payload)

    async def delete_token(self, token_id: Any) -> JSONObject:
        """Delete a gateway token."""
        await self._ensure_token()
        return await self._json_request(
            "DELETE", "{0}/tokens/{1}".format(_ADMIN_PREFIX, _path_id(token_id))
        )

    # -- model routes ------------------------------------------------------

    async def list_models(self) -> List[AdminModelRoute]:
        """List the configured model routes."""
        await self._ensure_token()
        payload = await self._json_request("GET", _ADMIN_PREFIX + "/models")
        data = payload.get("data")
        items = data if isinstance(data, list) else []
        return [
            AdminModelRoute.from_dict(item) for item in items if isinstance(item, Mapping)
        ]

    async def sync_models(self) -> JSONObject:
        """Synchronise the model catalog with the upstream catalog."""
        await self._ensure_token()
        return await self._json_request("POST", _ADMIN_PREFIX + "/models/sync")

    async def rebuild_models(self, confirmation: str = "REBUILD") -> JSONObject:
        """Replace the model catalog with a fresh upstream snapshot."""
        await self._ensure_token()
        return await self._json_request(
            "POST",
            _ADMIN_PREFIX + "/models/rebuild",
            json_body={"confirmation": confirmation},
        )

    async def upsert_model(
        self,
        model_id: str,
        *,
        protocol: str = "chat",
        upstream: str = "go",
        group: str = "",
        name: Optional[str] = None,
        real_model: Optional[str] = None,
        context_len: Optional[int] = None,
        status: Optional[int] = None,
        priority: Optional[int] = None,
        tags: Optional[Sequence[str]] = None,
        pricing: Optional[Mapping[str, str]] = None,
        **extra: Any,
    ) -> AdminModelRoute:
        """Create or replace a model route."""
        await self._ensure_token()
        body: Dict[str, Any] = {
            "id": model_id,
            "protocol": protocol,
            "upstream": upstream,
        }
        if group:
            body["group"] = group
        if name is not None:
            body["name"] = name
        if real_model is not None:
            body["real_model"] = real_model
        if context_len is not None:
            body["context_len"] = context_len
        if status is not None:
            body["status"] = status
        if priority is not None:
            body["priority"] = priority
        if tags is not None:
            body["tags"] = list(tags)
        if pricing is not None:
            body["pricing"] = dict(pricing)
        body.update(extra)
        payload = await self._json_request(
            "POST", _ADMIN_PREFIX + "/models", json_body=body
        )
        return AdminModelRoute.from_dict(payload)

    async def update_model(self, model_id: str, **fields: Any) -> AdminModelRoute:
        """Update a model route."""
        await self._ensure_token()
        payload = await self._json_request(
            "PATCH",
            "{0}/models/{1}".format(_ADMIN_PREFIX, _path_id(model_id)),
            json_body=dict(fields),
        )
        return AdminModelRoute.from_dict(payload)

    async def toggle_model(self, model_id: str) -> AdminModelRoute:
        """Enable or disable a model route."""
        await self._ensure_token()
        payload = await self._json_request(
            "POST", "{0}/models/{1}/toggle".format(_ADMIN_PREFIX, _path_id(model_id))
        )
        return AdminModelRoute.from_dict(payload)

    async def delete_model(self, model_id: str) -> JSONObject:
        """Delete a model route."""
        await self._ensure_token()
        return await self._json_request(
            "DELETE", "{0}/models/{1}".format(_ADMIN_PREFIX, _path_id(model_id))
        )

    # -- model mappings ----------------------------------------------------

    async def list_model_mappings(self) -> List[ModelMapping]:
        """List the model rewrite rules."""
        await self._ensure_token()
        payload = await self._json_request("GET", _ADMIN_PREFIX + "/model-mappings")
        data = payload.get("data")
        items = data if isinstance(data, list) else []
        return [ModelMapping.from_dict(item) for item in items if isinstance(item, Mapping)]

    async def upsert_model_mapping(
        self, source_model: str, target_model: str
    ) -> ModelMapping:
        """Create or replace a model rewrite rule."""
        await self._ensure_token()
        payload = await self._json_request(
            "POST",
            _ADMIN_PREFIX + "/model-mappings",
            json_body={"source_model": source_model, "target_model": target_model},
        )
        return ModelMapping.from_dict(payload)

    async def delete_model_mapping(self, source_model: str) -> JSONObject:
        """Delete a model rewrite rule."""
        await self._ensure_token()
        return await self._json_request(
            "DELETE",
            "{0}/model-mappings/{1}".format(_ADMIN_PREFIX, _path_tail(source_model)),
        )

    # -- observability -----------------------------------------------------

    async def stats(self) -> JSONObject:
        """Return the usage statistics of the gateway."""
        await self._ensure_token()
        return await self._json_request("GET", _ADMIN_PREFIX + "/stats")

    async def usage_logs(self, page: int = 1, page_size: int = 25) -> UsageLogPage:
        """Return a page of usage logs."""
        await self._ensure_token()
        payload = await self._json_request(
            "GET",
            _ADMIN_PREFIX + "/usage",
            params={"page": page, "page_size": page_size},
        )
        return UsageLogPage.from_dict(payload)

    async def health(self) -> JSONObject:
        """Return the pool health of the gateway."""
        await self._ensure_token()
        return await self._json_request("GET", _ADMIN_PREFIX + "/health")

    async def version(self) -> JSONObject:
        """Return the running version of the gateway."""
        await self._ensure_token()
        return await self._json_request("GET", _ADMIN_PREFIX + "/version")

    async def backup(self) -> JSONObject:
        """Export the whole gateway configuration.

        The export contains upstream key values, gateway tokens and session
        cookies, so it must be treated as a secret.
        """
        await self._ensure_token()
        return await self._json_request("GET", _ADMIN_PREFIX + "/backup")

    async def restore(self, backup: Mapping[str, Any]) -> JSONObject:
        """Restore a configuration export.

        Existing entries are updated in place, so restoring the same export
        twice is safe.
        """
        await self._ensure_token()
        return await self._json_request(
            "POST", _ADMIN_PREFIX + "/restore", json_body=backup
        )
