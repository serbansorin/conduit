"""The ``agentic-crew`` command.

Six commands, one job each:

``agents``
    Show the crew.
``ask``
    Ask one question and print the answer.
``run``
    Ask one question and watch the crew work.
``ingest``
    Build the knowledge bases.
``serve``
    Run the HTTP service.
``doctor``
    Check that the setup is sane.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .config import Settings, load_settings
from .orchestrator import (
    CrewOrchestrator,
    KnowledgeBaseUnavailable,
    OrchestratorError,
    UnknownAgentError,
    default_knowledge_base,
)
from .registry import AgentRegistry

log = logging.getLogger(__name__)

__all__ = ["main"]

_PROGRAM = "agentic-crew"


def _load_registry(settings: Settings) -> AgentRegistry:
    """Load the specialists, reporting broken files on the way.

    Args:
        settings: The runtime settings.

    Returns:
        The registry.
    """
    registry = AgentRegistry.load(settings.resolved_agents_dir)
    for error in registry.errors:
        print(f"{_PROGRAM}: warning: {error}", file=sys.stderr)
    return registry


def _build_orchestrator(settings: Settings) -> CrewOrchestrator:
    """Build an orchestrator over the loaded registry.

    Args:
        settings: The runtime settings.

    Returns:
        The orchestrator.
    """
    return CrewOrchestrator(settings, _load_registry(settings))


def _dump(payload: Any) -> str:
    """Render a payload as JSON.

    Args:
        payload: The payload.

    Returns:
        The JSON text.
    """
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


# ----------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------
def _cmd_agents(args: argparse.Namespace, settings: Settings) -> int:
    """List the agents and their capabilities.

    Args:
        args: The parsed arguments.
        settings: The runtime settings.

    Returns:
        The exit code.
    """
    orchestrator = _build_orchestrator(settings)
    agents = orchestrator.list_agents()

    if args.json:
        print(_dump(agents))
        return 0

    if not agents:
        print(f"no agents found in {settings.resolved_agents_dir}", file=sys.stderr)
        return 1

    print(f"{len(agents)} agent(s) in {settings.resolved_agents_dir}\n")
    for agent in agents:
        print(f"{agent['name']}  ({agent['role'] or 'no role'})")
        print(
            f"  model: {agent['model']}  collection: {agent['collection']}  "
            f"top_k: {agent['top_k']}"
        )
        capabilities = ", ".join(agent.get("capabilities") or [])
        print(f"  capabilities: {capabilities or '—'}")
        if agent.get("description"):
            print(f"  {agent['description']}")
        if agent.get("skills"):
            print(f"  skills: {', '.join(agent['skills'])}")
        print()
    return 0


def _cmd_ask(args: argparse.Namespace, settings: Settings) -> int:
    """Ask one question and print the answer.

    Args:
        args: The parsed arguments.
        settings: The runtime settings.

    Returns:
        The exit code.
    """
    orchestrator = _build_orchestrator(settings)
    question = " ".join(args.question)
    result = orchestrator.run(question, args.agent)

    if args.json:
        print(_dump(result.to_dict()))
        return 0

    print(result.answer)
    if args.verbose:
        print(file=sys.stderr)
        for step in result.steps:
            print(f"  {step.get('node')}: {step.get('detail')}", file=sys.stderr)
    return 0


def _cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    """Ask one question, streaming the steps as they happen.

    Args:
        args: The parsed arguments.
        settings: The runtime settings.

    Returns:
        The exit code.
    """
    orchestrator = _build_orchestrator(settings)
    question = " ".join(args.question)
    answer = ""
    steps: List[Dict[str, Any]] = []

    for event in orchestrator.stream(question, args.agent):
        print(f"  {event.get('node', '')}: {event.get('detail', '')}", file=sys.stderr)
        steps.extend(event.get("steps") or [])
        if event.get("answer"):
            answer = event["answer"]

    if args.json:
        print(
            _dump(
                {
                    "question": question,
                    "answer": answer,
                    "steps": steps,
                }
            )
        )
        return 0

    print(answer)
    return 0


def _cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    """Build the knowledge bases.

    Args:
        args: The parsed arguments.
        settings: The runtime settings.

    Returns:
        The exit code.
    """
    registry = _load_registry(settings)
    if not registry.agents:
        print(f"no agents found in {settings.resolved_agents_dir}", file=sys.stderr)
        return 1

    targets = (
        [registry.get(args.agent)]
        if args.agent
        else registry.all()
    )
    if args.agent and targets[0] is None:
        known = ", ".join(registry.names()) or "none"
        print(
            f"{_PROGRAM}: unknown agent {args.agent!r}; registered agents: {known}",
            file=sys.stderr,
        )
        return 1

    reports: List[Dict[str, Any]] = []
    failures = 0
    for definition in targets:
        assert definition is not None
        try:
            knowledge_base = default_knowledge_base(settings, definition)
            report = knowledge_base.ingest(force=bool(args.force))
        except KnowledgeBaseUnavailable as exc:
            print(f"{_PROGRAM}: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 - reported per agent
            failures += 1
            print(
                f"{_PROGRAM}: the ingest of {definition.name!r} failed: {exc}",
                file=sys.stderr,
            )
            continue

        payload = report if isinstance(report, dict) else _report_to_dict(report)
        reports.append({"agent": definition.name, "report": payload})
        if args.json:
            continue
        print(f"{definition.name}: {_report_summary(payload)}")

    if args.json:
        print(_dump({"reports": reports, "failed": failures}))
    return 1 if failures else 0


def _cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    """Run the HTTP service.

    Args:
        args: The parsed arguments.
        settings: The runtime settings.

    Returns:
        The exit code.
    """
    from .server import run_server

    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = int(args.port)
    run_server(settings)
    return 0


def _cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    """Check that the setup is sane.

    Args:
        args: The parsed arguments.
        settings: The runtime settings.

    Returns:
        The exit code. Non-zero when a check failed outright.
    """
    checks: List[Dict[str, str]] = []
    checks.append(_check_gateway(settings))
    checks.append(_check_token(settings))
    checks.append(_check_agents(settings))
    checks.append(_check_collections(settings))
    checks.append(_check_embeddings(settings))

    if args.json:
        print(_dump({"checks": checks, "ok": all(c["status"] != "error" for c in checks)}))
        return 0 if all(c["status"] != "error" for c in checks) else 1

    print(f"{_PROGRAM} doctor")
    for check in checks:
        marker = {"ok": "ok  ", "warn": "warn", "error": "FAIL"}.get(
            check["status"], "?"
        )
        print(f"  [{marker}] {check['check']}: {check['detail']}")

    failed = [c for c in checks if c["status"] == "error"]
    if failed:
        print(
            f"{len(failed)} check(s) failed: "
            + ", ".join(c["check"] for c in failed),
            file=sys.stderr,
        )
        return 1
    return 0


# ----------------------------------------------------------------------
# doctor checks
# ----------------------------------------------------------------------
def _check_gateway(settings: Settings) -> Dict[str, str]:
    """Check that the Conduit gateway answers.

    Args:
        settings: The runtime settings.

    Returns:
        The check result.
    """
    url = settings.conduit_base_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {
            "check": "gateway",
            "status": "warn",
            "detail": f"{url} answered HTTP {exc.code}",
        }
    except Exception as exc:  # noqa: BLE001 - the doctor never crashes
        return {
            "check": "gateway",
            "status": "warn",
            "detail": f"not reachable at {url} ({type(exc).__name__})",
        }

    keys = payload.get("keys") or {}
    return {
        "check": "gateway",
        "status": "ok",
        "detail": (
            f"{payload.get('service', 'conduit')} {payload.get('version', '')} at {url}, "
            f"{keys.get('total', '?')} key(s)"
        ).strip(),
    }


def _check_token(settings: Settings) -> Dict[str, str]:
    """Check that a gateway token is configured.

    Args:
        settings: The runtime settings.

    Returns:
        The check result.
    """
    if settings.conduit_api_key:
        return {
            "check": "gateway token",
            "status": "ok",
            "detail": "set (CONDUIT_API_KEY)",
        }
    return {
        "check": "gateway token",
        "status": "warn",
        "detail": "not set (CONDUIT_API_KEY) — the gateway may reject the calls",
    }


def _check_agents(settings: Settings) -> Dict[str, str]:
    """Check that agents were found.

    Args:
        settings: The runtime settings.

    Returns:
        The check result.
    """
    registry = AgentRegistry.load(settings.resolved_agents_dir)
    if not registry.agents:
        return {
            "check": "agents",
            "status": "error",
            "detail": f"none found in {settings.resolved_agents_dir}",
        }
    names = ", ".join(registry.names())
    detail = f"{len(registry)} in {settings.resolved_agents_dir}: {names}"
    if registry.errors:
        detail += f" ({len(registry.errors)} file problem(s))"
        return {"check": "agents", "status": "warn", "detail": detail}
    return {"check": "agents", "status": "ok", "detail": detail}


def _check_collections(settings: Settings) -> Dict[str, str]:
    """Check which collections already hold data.

    Args:
        settings: The runtime settings.

    Returns:
        The check result.
    """
    store = settings.store_path
    if not store.exists():
        return {
            "check": "collections",
            "status": "warn",
            "detail": f"no vector store yet at {store}; run 'agentic-crew ingest'",
        }
    try:
        entries = sorted(
            entry.name
            for entry in store.iterdir()
            if not entry.name.startswith(".")
        )
    except OSError as exc:
        return {
            "check": "collections",
            "status": "warn",
            "detail": f"cannot read {store}: {exc}",
        }
    if not entries:
        return {
            "check": "collections",
            "status": "warn",
            "detail": f"the vector store at {store} is empty",
        }
    shown = ", ".join(entries[:10])
    if len(entries) > 10:
        shown += f", … (+{len(entries) - 10})"
    return {
        "check": "collections",
        "status": "ok",
        "detail": f"{len(entries)} in {store}: {shown}",
    }


def _check_embeddings(settings: Settings) -> Dict[str, str]:
    """Check that the embedding backend is usable.

    Args:
        settings: The runtime settings.

    Returns:
        The check result.
    """
    backend = (settings.embedding_backend or "").strip().lower()
    if backend == "fastembed":
        try:
            import fastembed  # noqa: F401
        except Exception as exc:  # noqa: BLE001 - the doctor never crashes
            return {
                "check": "embeddings",
                "status": "warn",
                "detail": (
                    f"fastembed is not installed ({exc}); using the built-in "
                    f"hash embedder (lower quality, works on any Python). "
                    f"Install python 3.12 and fastembed for full semantic search."
                ),
            }
        version = getattr(fastembed, "__version__", "unknown")
        return {
            "check": "embeddings",
            "status": "ok",
            "detail": f"fastembed {version}, model {settings.embedding_model}",
        }
    if backend == "openai":
        if not settings.embedding_base_url:
            return {
                "check": "embeddings",
                "status": "error",
                "detail": "embedding_base_url is not set for the openai backend",
            }
        return {
            "check": "embeddings",
            "status": "ok",
            "detail": (
                f"{settings.embedding_base_url}, model {settings.embedding_model}"
            ),
        }
    return {
        "check": "embeddings",
        "status": "error",
        "detail": f"unknown embedding backend {backend!r}",
    }


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _report_to_dict(report: Any) -> Dict[str, Any]:
    """Convert an ingest report into a mapping.

    Args:
        report: The report.

    Returns:
        The report as a mapping.
    """
    if hasattr(report, "model_dump"):
        try:
            return dict(report.model_dump())
        except Exception:  # noqa: BLE001 - fall through
            pass
    if hasattr(report, "__dict__"):
        return {
            str(key): value
            for key, value in vars(report).items()
            if not str(key).startswith("_")
        }
    return {"report": str(report)}


def _report_summary(report: Dict[str, Any]) -> str:
    """Summarise an ingest report for the terminal.

    Args:
        report: The report as a mapping.

    Returns:
        A one-line summary.
    """
    if not report:
        return "done"
    parts: List[str] = []
    for key in ("collection", "documents", "chunks", "files", "skipped"):
        if key in report and report[key] is not None:
            parts.append(f"{key}={report[key]}")
    if not parts:
        return ", ".join(f"{k}={v}" for k, v in report.items()) or "done"
    return " ".join(parts)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog=_PROGRAM,
        description=(
            "A file-based agent crew: a master orchestrator routes a question "
            "to a specialist that answers from its own knowledge base."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print the steps of the run and the warnings",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    agents = sub.add_parser("agents", help="list the agents and their capabilities")
    agents.add_argument("--json", action="store_true", help="print the catalogue as JSON")
    agents.set_defaults(handler=_cmd_agents)

    ask = sub.add_parser("ask", help="ask the crew a question")
    ask.add_argument("question", nargs="+", help="the question")
    ask.add_argument("--agent", default=None, help="force a specific agent")
    ask.add_argument("--json", action="store_true", help="print the raw result as JSON")
    ask.set_defaults(handler=_cmd_ask)

    run = sub.add_parser("run", help="ask the crew a question and watch it work")
    run.add_argument("question", nargs="+", help="the question")
    run.add_argument("--agent", default=None, help="force a specific agent")
    run.add_argument("--json", action="store_true", help="print the raw result as JSON")
    run.set_defaults(handler=_cmd_run)

    ingest = sub.add_parser("ingest", help="build the knowledge bases")
    ingest.add_argument("--agent", default=None, help="only ingest this agent")
    ingest.add_argument(
        "--force", action="store_true", help="rebuild even when there is data"
    )
    ingest.add_argument("--json", action="store_true", help="print the reports as JSON")
    ingest.set_defaults(handler=_cmd_ingest)

    serve = sub.add_parser("serve", help="run the HTTP service")
    serve.add_argument("--host", default=None, help="bind address")
    serve.add_argument("--port", type=int, default=None, help="bind port")
    serve.set_defaults(handler=_cmd_serve)

    doctor = sub.add_parser("doctor", help="check the setup")
    doctor.add_argument("--json", action="store_true", help="print the checks as JSON")
    doctor.set_defaults(handler=_cmd_doctor)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Run the command line interface.

    Args:
        argv: The arguments. ``sys.argv`` is used when omitted.

    Returns:
        The exit code. Zero on success.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed the reason
        return int(exc.code) if isinstance(exc.code, int) else 2

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        settings = load_settings()
        return handler(args, settings)
    except UnknownAgentError as exc:
        print(f"{_PROGRAM}: {exc}", file=sys.stderr)
        return 1
    except OrchestratorError as exc:
        print(f"{_PROGRAM}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print(file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - the CLI reports, it never traces
        print(f"{_PROGRAM}: {type(exc).__name__}: {exc}", file=sys.stderr)
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
