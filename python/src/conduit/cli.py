"""Command line interface for the Conduit gateway.

Run ``conduit --help`` for the full command list.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, TextIO

from ._common import DEFAULT_BASE_URL
from ._version import __version__
from .admin import AdminClient
from .client import Client
from .exceptions import ConduitError
from .models import AdminKey, AdminToken, Model

__all__ = ["main", "build_parser"]

ENV_BASE_URL = "CONDUIT_BASE_URL"
ENV_API_KEY = "CONDUIT_API_KEY"
ENV_ADMIN_PASSWORD = "CONDUIT_ADMIN_PASSWORD"

_DEFAULT_TIMEOUT = 120.0
_DEFAULT_ADMIN_TIMEOUT = 30.0


def _env(name: str, fallback: Optional[str] = None) -> Optional[str]:
    """Read an environment variable, ignoring empty values."""
    value = os.environ.get(name)
    if value is None:
        return fallback
    value = value.strip()
    return value or fallback


def _render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Render a simple aligned text table.

    Args:
        headers: Column headers.
        rows: The rows to render.

    Returns:
        The rendered table.
    """
    widths: List[int] = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)).rstrip()
    ]
    lines.append("  ".join("-" * width for width in widths).rstrip())
    for row in rows:
        lines.append(
            "  ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row)).rstrip()
        )
    return "\n".join(lines)


def _model_rows(models: Sequence[Model]) -> List[List[str]]:
    """Build the table rows for the model catalog."""
    return [
        [
            model.id,
            model.protocol,
            str(model.context_length or model.context_len),
            model.owned_by,
            model.name,
        ]
        for model in models
    ]


def _key_rows(keys: Sequence[AdminKey]) -> List[List[str]]:
    """Build the table rows for upstream keys."""
    return [
        [
            str(key.id),
            key.group,
            key.label,
            "yes" if key.enabled else "no",
            str(key.weight),
            str(key.usage_count),
            key.value,
        ]
        for key in keys
    ]


def _token_rows(tokens: Sequence[AdminToken]) -> List[List[str]]:
    """Build the table rows for gateway tokens."""
    return [
        [
            str(token.id),
            token.name,
            "yes" if token.enabled else "no",
            str(token.rate_limit),
            str(token.requests_used),
            str(token.total_tokens),
            token.token,
        ]
        for token in tokens
    ]


def _dump(value: Any) -> str:
    """Serialise a dataclass or plain object to indented JSON."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        payload: Any = dataclasses.asdict(value)
    else:
        payload = value
    return json.dumps(payload, indent=2, sort_keys=False, default=str)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser of the ``conduit`` command.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="conduit",
        description="Command line client for the Conduit LLM gateway.",
    )
    parser.add_argument(
        "--base-url",
        default=_env(ENV_BASE_URL, DEFAULT_BASE_URL),
        help="gateway base URL (default: $%(default)s)",
    )
    parser.add_argument(
        "--api-key",
        default=_env(ENV_API_KEY),
        help="gateway token (default: $CONDUIT_API_KEY)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT,
        help="request timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--admin-password",
        default=_env(ENV_ADMIN_PASSWORD),
        help="admin password (default: $CONDUIT_ADMIN_PASSWORD)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="conduit-cli {0}".format(__version__),
    )

    commands = parser.add_subparsers(dest="command", metavar="<command>")

    commands.add_parser("version", help="print the client version")
    commands.add_parser("models", help="list the models exposed by the gateway")
    commands.add_parser("health", help="check that the gateway is reachable")

    chat = commands.add_parser("chat", help="one-shot chat completion")
    chat.add_argument("model", help="gateway-facing model id")
    chat.add_argument("prompt", help="user prompt")
    chat.add_argument("--stream", action="store_true", help="stream the response")
    chat.add_argument("--json", action="store_true", help="print the raw JSON response")
    chat.add_argument(
        "--system", default=None, help="optional system prompt for the conversation"
    )

    ask = commands.add_parser("ask", help="one-shot Anthropic-style message")
    ask.add_argument("model", help="gateway-facing model id")
    ask.add_argument("prompt", help="user prompt")
    ask.add_argument(
        "--max-tokens", type=int, default=1024, help="maximum tokens to generate"
    )
    ask.add_argument("--system", default=None, help="optional system prompt")
    ask.add_argument("--stream", action="store_true", help="stream the response")
    ask.add_argument("--json", action="store_true", help="print the raw JSON response")

    admin = commands.add_parser("admin", help="admin API commands")
    admin_commands = admin.add_subparsers(dest="admin_command", metavar="<command>")
    admin_commands.add_parser("keys", help="list upstream keys")
    admin_commands.add_parser("tokens", help="list gateway tokens")
    admin_commands.add_parser("stats", help="show gateway usage statistics")

    return parser


def _run_models(args: argparse.Namespace, out: TextIO) -> int:
    """Run the ``models`` command."""
    with Client(base_url=args.base_url, api_key=args.api_key, timeout=args.timeout) as client:
        catalog = client.models.list()
    if getattr(args, "json", False):
        print(_dump(catalog), file=out)
        return 0
    print(_render_table(["MODEL", "PROTOCOL", "CONTEXT", "OWNER", "NAME"], _model_rows(catalog.data)), file=out)
    return 0


def _run_health(args: argparse.Namespace, out: TextIO) -> int:
    """Run the ``health`` command."""
    with Client(base_url=args.base_url, api_key=args.api_key, timeout=args.timeout) as client:
        status = client.health()
    print(_dump({"status": status.status, "service": status.service}), file=out)
    return 0


def _run_chat(args: argparse.Namespace, out: TextIO) -> int:
    """Run the ``chat`` command."""
    messages: List[Dict[str, Any]] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.prompt})

    with Client(base_url=args.base_url, api_key=args.api_key, timeout=args.timeout) as client:
        if args.stream:
            chunks = client.chat.completions.create(
                model=args.model, messages=messages, stream=True
            )
            for chunk in chunks:
                for choice in chunk.choices:
                    delta = choice.delta.content
                    if delta:
                        out.write(delta)
                        out.flush()
            out.write("\n")
            return 0
        completion = client.chat.completions.create(model=args.model, messages=messages)
    if args.json:
        print(_dump(completion), file=out)
        return 0
    print(completion.text, file=out)
    return 0


def _run_ask(args: argparse.Namespace, out: TextIO) -> int:
    """Run the ``ask`` command."""
    messages: List[Dict[str, Any]] = [{"role": "user", "content": args.prompt}]
    with Client(base_url=args.base_url, api_key=args.api_key, timeout=args.timeout) as client:
        if args.stream:
            events = client.messages.create(
                model=args.model,
                messages=messages,
                max_tokens=args.max_tokens,
                system=args.system,
                stream=True,
            )
            for event in events:
                if event.type == "content_block_delta":
                    text = event.data.get("delta", {})
                    if isinstance(text, dict):
                        piece = text.get("text")
                        if piece:
                            out.write(str(piece))
                            out.flush()
            out.write("\n")
            return 0
        message = client.messages.create(
            model=args.model,
            messages=messages,
            max_tokens=args.max_tokens,
            system=args.system,
        )
    if args.json:
        print(_dump(message), file=out)
        return 0
    print(message.text, file=out)
    return 0


def _run_admin(args: argparse.Namespace, out: TextIO) -> int:
    """Run the ``admin`` subcommands."""
    password = args.admin_password
    if not password:
        raise ConduitError(
            "an admin password is required: pass --admin-password or set {0}".format(
                ENV_ADMIN_PASSWORD
            )
        )
    with AdminClient(
        base_url=args.base_url, password=password, timeout=_DEFAULT_ADMIN_TIMEOUT
    ) as admin:
        if args.admin_command == "keys":
            keys = admin.list_keys()
            print(
                _render_table(
                    ["ID", "GROUP", "LABEL", "ENABLED", "WEIGHT", "USED", "VALUE"],
                    _key_rows(keys),
                ),
                file=out,
            )
            return 0
        if args.admin_command == "tokens":
            tokens = admin.list_tokens()
            print(
                _render_table(
                    ["ID", "NAME", "ENABLED", "RATE", "REQUESTS", "TOKENS", "TOKEN"],
                    _token_rows(tokens),
                ),
                file=out,
            )
            return 0
        stats = admin.stats()
        summary = {
            key: stats.get(key)
            for key in (
                "total_calls",
                "today_calls",
                "last_hour_calls",
                "success_calls",
                "error_calls",
                "rpm",
                "tpm",
                "total_tokens",
                "total_cost",
                "keys",
                "enabled_keys",
                "tokens",
                "enabled_tokens",
            )
            if key in stats
        }
        print(_dump(summary), file=out)
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point of the ``conduit`` console script.

    Args:
        argv: Command line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    out = sys.stdout

    if not args.command:
        parser.print_help(out)
        return 2

    try:
        if args.command == "version":
            print("conduit-cli {0}".format(__version__), file=out)
            return 0
        if args.command == "models":
            return _run_models(args, out)
        if args.command == "health":
            return _run_health(args, out)
        if args.command == "chat":
            return _run_chat(args, out)
        if args.command == "ask":
            return _run_ask(args, out)
        if args.command == "admin":
            if not args.admin_command:
                print("conduit admin: expected one of keys, tokens, stats", file=sys.stderr)
                return 2
            return _run_admin(args, out)
    except ConduitError as exc:
        print("error: {0}".format(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("interrupted", file=sys.stderr)
        return 130

    parser.print_help(out)
    return 2


if __name__ == "__main__":  # pragma: no cover - manual invocation
    raise SystemExit(main())
