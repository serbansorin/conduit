"""Tests for the Conduit admin clients."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from conftest import Recorder, json_response

from conduit import AdminClient, AsyncAdminClient
from conduit.exceptions import AuthenticationError, ConduitError

BASE_URL = "http://gateway.test:9812"
ADMIN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.admin-token"


def _admin(recorder: Recorder, password: str = "s3cret") -> AdminClient:
    """Build an admin client wired to a mock transport."""
    return AdminClient(
        base_url=BASE_URL, password=password, transport=recorder.transport
    )


def _login_route(recorder: Recorder) -> None:
    """Register the admin login route."""
    recorder.add("POST", "/admin/login", lambda request: json_response("admin_login.json"))


def test_login_returns_the_jwt() -> None:
    recorder = Recorder()
    _login_route(recorder)

    with _admin(recorder) as admin:
        assert admin.token is None
        token = admin.login()

    assert token == ADMIN_TOKEN
    assert admin.token == ADMIN_TOKEN
    body = recorder.body_of(0)
    assert body == {"password": "s3cret"}


def test_auto_login_on_first_use() -> None:
    recorder = Recorder()
    _login_route(recorder)
    recorder.add("GET", "/admin/keys", lambda request: json_response("admin_keys.json"))

    with _admin(recorder) as admin:
        keys = admin.list_keys()

    assert recorder.paths() == ["/admin/login", "/admin/keys"]
    assert recorder.requests[1].headers["authorization"] == "Bearer " + ADMIN_TOKEN
    assert len(keys) == 1
    assert keys[0].id == 1
    assert keys[0].group == "go"
    assert keys[0].label == "primary"
    assert keys[0].enabled is True
    assert keys[0].weight == 2
    assert keys[0].usage_count == 42
    assert keys[0].last_quota == {"remaining": 1000}


def test_login_is_performed_only_once() -> None:
    recorder = Recorder()
    _login_route(recorder)
    recorder.add("GET", "/admin/keys", lambda request: json_response("admin_keys.json"))
    recorder.add("GET", "/admin/tokens", lambda request: json_response("admin_tokens.json"))

    with _admin(recorder) as admin:
        admin.list_keys()
        admin.list_tokens()

    assert recorder.paths().count("/admin/login") == 1


def test_invalid_password_raises_authentication_error() -> None:
    recorder = Recorder()
    recorder.add(
        "POST",
        "/admin/login",
        lambda request: httpx.Response(
            401, json={"error": "invalid password"}, headers={"Content-Type": "application/json"}
        ),
    )

    with _admin(recorder, password="wrong") as admin:
        with pytest.raises(AuthenticationError) as info:
            admin.login()

    assert info.value.status_code == 401
    assert info.value.message == "invalid password"


def test_missing_password_raises_conduit_error() -> None:
    recorder = Recorder()
    admin = AdminClient(base_url=BASE_URL, transport=recorder.transport)
    with pytest.raises(ConduitError):
        admin.login()
    admin.close()


def test_list_tokens_and_models() -> None:
    recorder = Recorder()
    _login_route(recorder)
    recorder.add("GET", "/admin/tokens", lambda request: json_response("admin_tokens.json"))
    recorder.add("GET", "/admin/models", lambda request: json_response("admin_models.json"))

    with _admin(recorder) as admin:
        tokens = admin.list_tokens()
        routes = admin.list_models()

    assert len(tokens) == 1
    assert tokens[0].id == 7
    assert tokens[0].name == "claude-code"
    assert tokens[0].token == "sk-gw-1234567890"
    assert tokens[0].rate_limit == 60
    assert tokens[0].requests_used == 12
    assert tokens[0].total_tokens == 150
    assert tokens[0].today_requests == 5
    assert tokens[0].last_hour_tokens == 20

    assert len(routes) == 1
    assert routes[0].id == "glm-5.1"
    assert routes[0].upstream == "go"
    assert routes[0].protocol == "chat"
    assert routes[0].status == 1
    assert routes[0].context_len == 200000
    assert routes[0].pricing["prompt"] == "0.000001"


def test_usage_logs_pagination() -> None:
    recorder = Recorder()
    _login_route(recorder)
    recorder.add("GET", "/admin/usage", lambda request: json_response("admin_usage.json"))

    with _admin(recorder) as admin:
        page = admin.usage_logs(page=2, page_size=10)

    request = recorder.requests[1]
    assert request.url.params["page"] == "2"
    assert request.url.params["page_size"] == "10"
    assert page.total == 1
    assert page.page == 1
    assert page.page_size == 25
    assert len(page.items) == 1
    assert page.items[0].request_id == "req-conduit-0001"
    assert page.items[0].model_name == "GLM 5.1"
    assert page.items[0].total_tokens == 16
    assert page.summary["total_calls"] == 1


def test_stats_health_and_version() -> None:
    recorder = Recorder()
    _login_route(recorder)
    recorder.add("GET", "/admin/stats", lambda request: json_response("admin_stats.json"))
    recorder.add(
        "GET",
        "/admin/health",
        lambda request: httpx.Response(200, json={"status": "ok", "pools": {}}),
    )
    recorder.add(
        "GET",
        "/admin/version",
        lambda request: httpx.Response(
            200, json={"version": "v0.1.0", "repo": "conduit", "github_url": ""}
        ),
    )

    with _admin(recorder) as admin:
        stats = admin.stats()
        health = admin.health()
        version = admin.version()

    assert stats["total_calls"] == 120
    assert stats["enabled_keys"] == 2
    assert health["status"] == "ok"
    assert version["version"] == "v0.1.0"


def test_key_mutations() -> None:
    recorder = Recorder()
    _login_route(recorder)
    recorder.add(
        "POST",
        "/admin/keys",
        lambda request: httpx.Response(
            200, json={"id": 2, "value": "sk-new", "group": "go", "label": "new"}
        ),
    )
    recorder.add(
        "PATCH",
        "/admin/keys/2",
        lambda request: httpx.Response(200, json={"id": 2, "label": "renamed"}),
    )
    recorder.add(
        "POST",
        "/admin/keys/2/toggle",
        lambda request: httpx.Response(200, json={"id": 2, "enabled": False}),
    )
    recorder.add(
        "DELETE", "/admin/keys/2", lambda request: httpx.Response(200, json={"ok": True})
    )

    with _admin(recorder) as admin:
        created = admin.create_key("sk-new", label="new")
        updated = admin.update_key(2, label="renamed")
        toggled = admin.toggle_key(2)
        deleted = admin.delete_key(2)

    assert created.id == 2
    assert updated.label == "renamed"
    assert toggled.enabled is False
    assert deleted == {"ok": True}
    assert recorder.body_of(1) == {
        "value": "sk-new",
        "group": "go",
        "label": "new",
        "weight": 1,
        "proxy_url": "",
        "cookie": "",
        "workspace_id": "",
    }


def test_model_route_mutations() -> None:
    recorder = Recorder()
    _login_route(recorder)
    recorder.add(
        "POST",
        "/admin/models",
        lambda request: httpx.Response(200, json={"id": "new-model", "protocol": "chat"}),
    )
    recorder.add(
        "POST",
        "/admin/models/sync",
        lambda request: httpx.Response(200, json={"added": 3, "updated": 1}),
    )
    recorder.add(
        "POST",
        "/admin/models/rebuild",
        lambda request: httpx.Response(200, json={"added": 3}),
    )
    recorder.add(
        "POST",
        "/admin/models/new-model/toggle",
        lambda request: httpx.Response(200, json={"id": "new-model", "status": 0}),
    )
    recorder.add(
        "DELETE",
        "/admin/models/new-model",
        lambda request: httpx.Response(200, json={"ok": True}),
    )

    with _admin(recorder) as admin:
        route = admin.upsert_model("new-model", protocol="chat", name="New")
        synced = admin.sync_models()
        rebuilt = admin.rebuild_models()
        toggled = admin.toggle_model("new-model")
        deleted = admin.delete_model("new-model")

    assert route.id == "new-model"
    assert synced["added"] == 3
    assert rebuilt["added"] == 3
    assert toggled.status == 0
    assert deleted == {"ok": True}
    assert recorder.body_of(1)["id"] == "new-model"
    assert recorder.body_of(3) == {"confirmation": "REBUILD"}


def test_model_mapping_mutations() -> None:
    recorder = Recorder()
    _login_route(recorder)
    recorder.add(
        "POST",
        "/admin/model-mappings",
        lambda request: httpx.Response(
            200, json={"source_model": "gpt-4", "target_model": "glm-5.1"}
        ),
    )
    recorder.add(
        "DELETE",
        "/admin/model-mappings/gpt-4",
        lambda request: httpx.Response(200, json={"ok": True}),
    )

    with _admin(recorder) as admin:
        mapping = admin.upsert_model_mapping("gpt-4", "glm-5.1")
        deleted = admin.delete_model_mapping("gpt-4")

    assert mapping.source_model == "gpt-4"
    assert mapping.target_model == "glm-5.1"
    assert deleted == {"ok": True}


def test_async_admin_client() -> None:
    recorder = Recorder()
    _login_route(recorder)
    recorder.add("GET", "/admin/keys", lambda request: json_response("admin_keys.json"))

    async def scenario() -> None:
        admin = AsyncAdminClient(
            base_url=BASE_URL, password="s3cret", transport=recorder.transport
        )
        try:
            token = await admin.login()
            assert token == ADMIN_TOKEN
            keys = await admin.list_keys()
            assert len(keys) == 1
            assert keys[0].label == "primary"
        finally:
            await admin.close()

    asyncio.run(scenario())
    assert recorder.paths() == ["/admin/login", "/admin/keys"]


def test_backup_and_restore() -> None:
    recorder = Recorder()
    _login_route(recorder)
    recorder.add(
        "GET",
        "/admin/backup",
        lambda request: httpx.Response(
            200,
            json={
                "version": 1,
                "exported_at": "2026-01-01T00:00:00Z",
                "keys": [{"value": "sk-upstream", "group": "go", "enabled": True}],
                "tokens": [{"token": "sk-gateway", "name": "gateway"}],
                "models": [{"id": "glm-5.3", "protocol": "chat"}],
                "model_mappings": [],
            },
        ),
    )
    recorder.add(
        "POST",
        "/admin/restore",
        lambda request: httpx.Response(
            200,
            json={
                "restored": {
                    "keys": 1,
                    "tokens": 1,
                    "models": 1,
                    "model_mappings": 0,
                }
            },
        ),
    )

    with _admin(recorder) as admin:
        backup = admin.backup()
        summary = admin.restore(backup)

    assert backup["version"] == 1
    assert len(backup["keys"]) == 1
    assert summary["restored"]["keys"] == 1
    assert summary["restored"]["tokens"] == 1


def test_async_backup_and_restore() -> None:
    recorder = Recorder()
    _login_route(recorder)
    recorder.add(
        "GET",
        "/admin/backup",
        lambda request: httpx.Response(200, json={"version": 1, "keys": []}),
    )
    recorder.add(
        "POST",
        "/admin/restore",
        lambda request: httpx.Response(
            200, json={"restored": {"keys": 0, "tokens": 0}}
        ),
    )

    async def run() -> None:
        admin = AsyncAdminClient(
            base_url=BASE_URL, password="s3cret", transport=recorder.transport
        )
        try:
            backup = await admin.backup()
            assert backup["version"] == 1
            summary = await admin.restore(backup)
            assert summary["restored"]["keys"] == 0
        finally:
            await admin.close()

    asyncio.run(run())
