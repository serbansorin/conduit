"""The HTTP service.

The crew is used from n8n and from other automation, so the service is a
small JSON API. It is deliberately boring: a health probe, the agent
catalogue, one endpoint that answers a question and one endpoint that
rebuilds a knowledge base.

The mutating endpoints are protected with a bearer token when one is
configured. When no token is configured the service runs open, which is
convenient for local development and unsafe for anything else, so it says so
in the log at startup.
"""

from __future__ import annotations

import logging
import os
import secrets
from html import escape
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .config import Settings, load_settings
from .orchestrator import (
    CrewOrchestrator,
    KnowledgeBaseUnavailable,
    OrchestratorError,
    UnknownAgentError,
    default_knowledge_base,
    jsonable,
)
from .registry import AgentRegistry

log = logging.getLogger(__name__)

__all__ = ["SERVICE_NAME", "create_app", "run_server"]

SERVICE_NAME = "agentic-crew"

#: Local origins n8n may be served from. The regex covers any local port.
_N8N_ORIGINS = [
    "http://localhost:5678",
    "http://127.0.0.1:5678",
]

_LOCAL_ORIGIN_REGEX = r"http://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?"


class RunRequest(BaseModel):
    """The body of ``POST /run``.

    Attributes:
        question: The question to answer.
        agent: The specialist to use. The router chooses when omitted.
        session_id: An opaque caller-supplied id, echoed back.
    """

    question: str = Field(..., min_length=1, description="The question to answer.")
    agent: Optional[str] = Field(
        default=None, description="Force a specific agent instead of routing."
    )
    session_id: Optional[str] = Field(
        default=None, description="Caller-supplied id, echoed back for correlation."
    )


class IngestRequest(BaseModel):
    """The body of ``POST /agents/{name}/ingest``.

    Attributes:
        force: Rebuild the collection even when it already has documents.
    """

    force: bool = Field(
        default=False, description="Rebuild the collection even if it has data."
    )


def _resolve_token(settings: Settings) -> str:
    """Find the bearer token the API requires, if any.

    Args:
        settings: The runtime settings.

    Returns:
        The token, or an empty string when the service runs open.
    """
    dedicated = os.environ.get("AGENTIC_CREW_API_KEY", "").strip()
    if dedicated:
        return dedicated
    return str(settings.conduit_api_key or "").strip()


def _error_body(code: str, message: str, status: int, **extra: Any) -> Dict[str, Any]:
    """Build a structured error body.

    Args:
        code: A stable machine-readable error code.
        message: A human-readable message.
        status: The HTTP status code.
        **extra: Extra fields to include.

    Returns:
        The error body.
    """
    body: Dict[str, Any] = {"error": code, "message": message, "status": status}
    body.update(extra)
    return body


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: The runtime settings. The environment is read when omitted.

    Returns:
        The application.
    """
    resolved = settings if settings is not None else load_settings()
    registry = AgentRegistry.load(resolved.resolved_agents_dir)
    orchestrator = CrewOrchestrator(resolved, registry)
    token = _resolve_token(resolved)

    app = FastAPI(
        title=SERVICE_NAME,
        version="0.1.0",
        description=(
            "A file-based agent crew: a master orchestrator routes a question "
            "to a specialist that answers from its own knowledge base."
        ),
    )
    app.state.settings = resolved
    app.state.registry = registry
    app.state.orchestrator = orchestrator
    app.state.knowledge_base_factory = default_knowledge_base
    app.state.api_token = token

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_N8N_ORIGINS,
        allow_origin_regex=_LOCAL_ORIGIN_REGEX,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    for error in registry.errors:
        log.warning("agent definition problem: %s", error)
    if token:
        log.info("%s: bearer token required on the mutating endpoints", SERVICE_NAME)
    else:
        log.warning(
            "%s: no API token configured (AGENTIC_CREW_API_KEY or CONDUIT_API_KEY); "
            "/run and /agents/{name}/ingest are open. Do not expose this service "
            "to an untrusted network.",
            SERVICE_NAME,
        )

    _register_error_handlers(app)
    _register_routes(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    """Register the handlers that keep every error readable.

    Args:
        app: The application.
    """

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            body = _error_body(
                str(detail.get("error", "http_error")),
                str(detail.get("message", "request failed")),
                exc.status_code,
            )
            for key, value in detail.items():
                if key not in body:
                    body[key] = jsonable(value)
        else:
            body = _error_body("http_error", str(detail), exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content=body,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "validation_error",
                "the request body is not valid",
                422,
                errors=jsonable(exc.errors()),
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error in %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content=_error_body(
                "internal_error",
                f"{type(exc).__name__}: {exc}",
                500,
            ),
        )


def _register_routes(app: FastAPI) -> None:
    """Register the routes of the service.

    Args:
        app: The application.
    """

    def require_token(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ) -> None:
        """Check the bearer token of a mutating request.

        Args:
            request: The request.
            authorization: The ``Authorization`` header.

        Raises:
            HTTPException: When the token is missing or wrong.
        """
        expected = str(getattr(request.app.state, "api_token", "") or "")
        if not expected:
            return
        if not authorization or not authorization.strip():
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "missing_token",
                    "message": "an Authorization: Bearer <token> header is required",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            value.strip(), expected
        ):
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "invalid_token",
                    "message": "the bearer token is not valid",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health")
    def health() -> Dict[str, Any]:
        """Report that the service is up and how many agents it knows.

        Returns:
            The health payload.
        """
        registry: AgentRegistry = app.state.registry
        return {
            "status": "ok",
            "agents": len(registry),
            "service": SERVICE_NAME,
        }

    @app.get("/agents")
    def agents() -> List[Dict[str, Any]]:
        """Return the agent catalogue.

        Returns:
            One mapping per agent.
        """
        orchestrator: CrewOrchestrator = app.state.orchestrator
        return orchestrator.list_agents()

    @app.post("/run")
    def run(
        payload: RunRequest, _token: None = Depends(require_token)
    ) -> Dict[str, Any]:
        """Answer a question.

        Args:
            payload: The request body.
            _token: The bearer token check.

        Returns:
            The orchestrator result.

        Raises:
            HTTPException: When the question cannot be answered.
        """
        orchestrator: CrewOrchestrator = app.state.orchestrator
        try:
            result = orchestrator.run(payload.question, payload.agent)
        except UnknownAgentError as exc:
            raise HTTPException(
                status_code=404,
                detail={"error": "unknown_agent", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_request", "message": str(exc)},
            ) from exc
        except OrchestratorError as exc:
            raise HTTPException(
                status_code=502,
                detail={"error": "upstream_error", "message": str(exc)},
            ) from exc
        except Exception as exc:  # noqa: BLE001 - never leak a bare 500
            log.exception("the run failed")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "internal_error",
                    "message": f"{type(exc).__name__}: {exc}",
                },
            ) from exc

        body = result.to_dict()
        body["session_id"] = payload.session_id
        body["service"] = SERVICE_NAME
        return body

    @app.post("/agents/{name}/ingest")
    def ingest(
        name: str,
        payload: Optional[IngestRequest] = None,
        _token: None = Depends(require_token),
    ) -> Dict[str, Any]:
        """Rebuild the knowledge base of one agent.

        Args:
            name: The agent name.
            payload: The request body.
            _token: The bearer token check.

        Returns:
            The ingest report.

        Raises:
            HTTPException: When the agent is unknown or the ingest failed.
        """
        registry: AgentRegistry = app.state.registry
        settings: Settings = app.state.settings
        definition = registry.get(name)
        if definition is None:
            known = ", ".join(registry.names()) or "none"
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "unknown_agent",
                    "message": f"unknown agent {name!r}; registered agents: {known}",
                },
            )

        force = bool(payload.force) if payload is not None else False
        factory = app.state.knowledge_base_factory
        try:
            knowledge_base = factory(settings, definition)
            report = knowledge_base.ingest(force=force)
        except KnowledgeBaseUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": "rag_unavailable", "message": str(exc)},
            ) from exc
        except Exception as exc:  # noqa: BLE001 - never leak a bare 500
            log.exception("the ingest of %s failed", name)
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "ingest_failed",
                    "message": f"{type(exc).__name__}: {exc}",
                },
            ) from exc

        return {
            "agent": name,
            "force": force,
            "report": jsonable(report),
            "service": SERVICE_NAME,
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        """Return a tiny HTML page listing the agents.

        Returns:
            The page.
        """
        orchestrator: CrewOrchestrator = app.state.orchestrator
        rows: List[str] = []
        for agent in orchestrator.list_agents():
            capabilities = ", ".join(agent.get("capabilities") or []) or "—"
            rows.append(
                "<tr>"
                f"<td><code>{escape(str(agent.get('name', '')))}</code></td>"
                f"<td>{escape(str(agent.get('role', '')) or '—')}</td>"
                f"<td>{escape(capabilities)}</td>"
                f"<td>{escape(str(agent.get('model', '')))}</td>"
                f"<td>{escape(str(agent.get('collection', '')))}</td>"
                "<td>"
                f"{escape(str(agent.get('description', '')) or '—')}"
                "</td>"
                "</tr>"
            )

        html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{SERVICE_NAME}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.5 ui-sans-serif, system-ui, sans-serif; margin: 2rem auto;
         max-width: 60rem; padding: 0 1rem; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 0.2rem; }}
  p.sub {{ opacity: 0.7; margin-top: 0; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
  th, td {{ text-align: left; padding: 0.5rem 0.6rem; vertical-align: top;
            border-bottom: 1px solid rgba(128,128,128,0.3); }}
  th {{ font-weight: 600; opacity: 0.8; }}
  code {{ font-family: ui-monospace, monospace; }}
  form {{ margin-top: 1.5rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }}
  input, select, button {{ font: inherit; padding: 0.45rem 0.6rem;
                           border: 1px solid rgba(128,128,128,0.5);
                           border-radius: 6px; background: transparent;
                           color: inherit; }}
  input {{ flex: 1 1 20rem; }}
  #answer {{ margin-top: 1rem; white-space: pre-wrap; }}
</style>
</head>
<body>
<h1>{SERVICE_NAME}</h1>
<p class="sub">{len(rows)} agent(s). <code>POST /run</code> answers a question,
<code>POST /agents/&lt;name&gt;/ingest</code> rebuilds a knowledge base.</p>
<table>
<thead><tr><th>Name</th><th>Role</th><th>Capabilities</th><th>Model</th>
<th>Collection</th><th>Description</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
<form id="ask">
  <input id="question" placeholder="Ask the crew a question" required>
  <select id="agent"><option value="">route automatically</option>
  {''.join(f'<option value="{escape(str(a["name"]))}">{escape(str(a["name"]))}</option>' for a in orchestrator.list_agents())}
  </select>
  <button type="submit">Ask</button>
</form>
<div id="answer"></div>
<script>
document.getElementById('ask').addEventListener('submit', async (event) => {{
  event.preventDefault();
  const body = {{ question: document.getElementById('question').value }};
  const agent = document.getElementById('agent').value;
  if (agent) {{ body.agent = agent; }}
  const out = document.getElementById('answer');
  out.textContent = 'Working...';
  const response = await fetch('/run', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(body),
  }});
  const data = await response.json();
  out.textContent = data.answer || JSON.stringify(data, null, 2);
}});
</script>
</body>
</html>
"""
        return HTMLResponse(content=html)


def run_server(settings: Optional[Settings] = None) -> None:
    """Run the HTTP service until it is stopped.

    Args:
        settings: The runtime settings. The environment is read when omitted.
    """
    import uvicorn

    resolved = settings if settings is not None else load_settings()
    app = create_app(resolved)
    log.info(
        "%s listening on http://%s:%d", SERVICE_NAME, resolved.host, resolved.port
    )
    uvicorn.run(app, host=resolved.host, port=resolved.port, log_level="info")
