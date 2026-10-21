"""Tests for the ``conduit`` command line interface."""

from __future__ import annotations

import json

import pytest

from conduit import cli
from conduit.exceptions import ConduitError
from conduit.models import AdminKey, AdminToken, ChatCompletion, ModelList


class _FakeClient:
    """A stand-in for :class:`conduit.Client` used by the CLI tests."""

    instances = []

    def __init__(self, base_url, api_key=None, timeout=120.0, **kwargs):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        _FakeClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def health(self):
        from conduit.models import HealthStatus

        return HealthStatus(status="ok", service="conduit")

    class _Models:
        @staticmethod
        def list():
            return ModelList.from_dict(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "glm-5.1",
                            "object": "model",
                            "owned_by": "conduit",
                            "name": "GLM 5.1",
                            "protocol": "chat",
                            "context_length": 200000,
                        }
                    ],
                }
            )

    class _Chat:
        class _Completions:
            @staticmethod
            def create(model, messages, **kwargs):
                if kwargs.get("stream"):
                    return iter([])
                return ChatCompletion.from_dict(
                    {
                        "id": "chatcmpl-1",
                        "object": "chat.completion",
                        "created": 1,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "hi there"},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                )

        completions = _Completions()

    class _Messages:
        @staticmethod
        def create(model, messages, **kwargs):
            from conduit.models import AnthropicMessage

            return AnthropicMessage.from_dict(
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [{"type": "text", "text": "hi there"}],
                    "stop_reason": "end_turn",
                }
            )

    @property
    def models(self):
        return self._Models()

    @property
    def chat(self):
        return self._Chat()

    @property
    def messages(self):
        return self._Messages()


class _FakeAdmin:
    """A stand-in for :class:`conduit.AdminClient` used by the CLI tests."""

    def __init__(self, base_url, password=None, timeout=30.0, **kwargs):
        self.base_url = base_url
        self.password = password

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def list_keys(self, group="go"):
        return [
            AdminKey.from_dict(
                {"id": 1, "group": "go", "label": "primary", "enabled": True, "value": "sk-***"}
            )
        ]

    def list_tokens(self):
        return [
            AdminToken.from_dict(
                {
                    "id": 7,
                    "name": "claude-code",
                    "enabled": True,
                    "rate_limit": 60,
                    "requests_used": 3,
                    "total_tokens": 150,
                    "token": "sk-gw-1",
                }
            )
        ]

    def stats(self):
        return {"total_calls": 120, "today_calls": 12, "enabled_keys": 2}


@pytest.fixture(autouse=True)
def _patch_clients(monkeypatch):
    """Replace the CLI clients with fakes and clear the environment."""
    _FakeClient.instances = []
    monkeypatch.setattr(cli, "Client", _FakeClient)
    monkeypatch.setattr(cli, "AdminClient", _FakeAdmin)
    for name in (cli.ENV_BASE_URL, cli.ENV_API_KEY, cli.ENV_ADMIN_PASSWORD):
        monkeypatch.delenv(name, raising=False)


def test_help_exits_successfully(capsys) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["--help"])
    assert info.value.code == 0
    assert "conduit" in capsys.readouterr().out


def test_no_command_prints_usage(capsys) -> None:
    assert cli.main([]) == 2
    assert "usage" in capsys.readouterr().out.lower()


def test_models_command_renders_a_table(capsys) -> None:
    assert cli.main(["models"]) == 0
    out = capsys.readouterr().out
    assert "MODEL" in out
    assert "glm-5.1" in out
    assert "chat" in out


def test_health_command(capsys) -> None:
    assert cli.main(["health"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "ok", "service": "conduit"}


def test_chat_command(capsys) -> None:
    assert cli.main(["chat", "glm-5.1", "hello"]) == 0
    assert capsys.readouterr().out.strip() == "hi there"


def test_chat_command_json_output(capsys) -> None:
    assert cli.main(["chat", "glm-5.1", "hello", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["choices"][0]["message"]["content"] == "hi there"


def test_ask_command(capsys) -> None:
    assert cli.main(["ask", "glm-5.1", "hello"]) == 0
    assert capsys.readouterr().out.strip() == "hi there"


def test_admin_keys_command(capsys) -> None:
    assert cli.main(["--admin-password", "s3cret", "admin", "keys"]) == 0
    out = capsys.readouterr().out
    assert "primary" in out
    assert "GROUP" in out


def test_admin_tokens_command(capsys) -> None:
    assert cli.main(["--admin-password", "s3cret", "admin", "tokens"]) == 0
    out = capsys.readouterr().out
    assert "claude-code" in out


def test_admin_stats_command(capsys) -> None:
    assert cli.main(["--admin-password", "s3cret", "admin", "stats"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_calls"] == 120
    assert payload["enabled_keys"] == 2


def test_admin_without_subcommand(capsys) -> None:
    assert cli.main(["admin"]) == 2
    assert "expected one of" in capsys.readouterr().err


def test_admin_without_password_fails(capsys) -> None:
    assert cli.main(["admin", "keys"]) == 1
    assert "admin password" in capsys.readouterr().err


def test_error_exits_non_zero(monkeypatch, capsys) -> None:
    class Boom(_FakeClient):
        def health(self):
            raise ConduitError("gateway is down")

    monkeypatch.setattr(cli, "Client", Boom)
    assert cli.main(["health"]) == 1
    assert "gateway is down" in capsys.readouterr().err


def test_environment_defaults_are_used(monkeypatch) -> None:
    monkeypatch.setenv(cli.ENV_BASE_URL, "http://from-env:9812")
    monkeypatch.setenv(cli.ENV_API_KEY, "sk-env")
    assert cli.main(["health"]) == 0
    instance = _FakeClient.instances[-1]
    assert instance.base_url == "http://from-env:9812"
    assert instance.api_key == "sk-env"
