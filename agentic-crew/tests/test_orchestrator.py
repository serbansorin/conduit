"""Tests for the orchestrator, the HTTP service and the CLI.

Nothing here touches the network: a fake chat model stands in for the
gateway and a fake knowledge base stands in for the RAG layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from agentic_crew.cli import main
from agentic_crew.config import Settings
from agentic_crew.orchestrator import (
    CrewOrchestrator,
    NoAgentsError,
    OrchestratorError,
    OrchestratorResult,
    UnknownAgentError,
)
from agentic_crew.registry import AgentRegistry
from agentic_crew.server import create_app

from .conftest import FakeChatModel, FakeKnowledgeBase


class StubOrchestrator:
    """An orchestrator that returns a canned result.

    Attributes:
        result: What ``run`` returns.
        error: What ``run`` raises.
        calls: Every question it was asked.
    """

    def __init__(
        self,
        result: Optional[OrchestratorResult] = None,
        error: Optional[Exception] = None,
    ) -> None:
        """Build the stub.

        Args:
            result: What ``run`` returns.
            error: What ``run`` raises.
        """
        self.result = result or OrchestratorResult(
            answer="stubbed answer", agent="alpha", steps=[], retrieved=[]
        )
        self.error = error
        self.calls: List[Any] = []

    def list_agents(self) -> List[Dict[str, Any]]:
        """Return a one-agent catalogue.

        Returns:
            The catalogue.
        """
        return [
            {
                "name": "alpha",
                "role": "Angular engineer",
                "description": "Angular specialist.",
                "capabilities": ["angular"],
                "model": "glm-5.3",
                "temperature": 0.1,
                "max_tokens": 1024,
                "collection": "alpha",
                "top_k": 3,
                "rag_sources": 1,
                "skills": [],
                "source": "",
            }
        ]

    def run(self, question: str, agent: Optional[str] = None) -> OrchestratorResult:
        """Return the canned result.

        Args:
            question: The question.
            agent: The requested agent.

        Returns:
            The result.

        Raises:
            Exception: The configured error.
        """
        self.calls.append((question, agent))
        if self.error is not None:
            raise self.error
        return self.result


# ----------------------------------------------------------------------
# routing
# ----------------------------------------------------------------------
def test_routes_to_the_named_agent(
    orchestrator: CrewOrchestrator, model: FakeChatModel
) -> None:
    """An explicitly named agent is used and the router is not called."""
    result = orchestrator.run("how do I add a form field?", agent="beta")

    assert result.agent == "beta"
    assert result.answer == "A deterministic answer."
    assert not [call for call in model.calls if call["is_router"]]
    assert result.steps[0]["node"] == "route"


def test_routes_by_catalogue(
    orchestrator: CrewOrchestrator, model: FakeChatModel
) -> None:
    """Without a named agent the router model picks one from the catalogue."""
    model.router_reply = '{"agent": "beta"}'

    result = orchestrator.run("how do I queue a job?")

    assert result.agent == "beta"
    assert [call for call in model.calls if call["is_router"]]
    assert "beta" in result.steps[0]["detail"]


def test_the_router_is_cheap_and_the_specialist_uses_its_own_model(
    orchestrator: CrewOrchestrator, settings: Settings
) -> None:
    """Routing uses the default model, answering uses the agent's model."""
    orchestrator.run("a question about angular")

    calls = orchestrator.llm_calls  # type: ignore[attr-defined]
    assert calls[0]["model"] == settings.default_model
    assert calls[-1]["model"] == "glm-5.3"


def test_an_unknown_named_agent_is_refused(
    orchestrator: CrewOrchestrator,
) -> None:
    """Naming an agent that does not exist is a clear error."""
    with pytest.raises(UnknownAgentError) as error:
        orchestrator.run("a question", agent="ghost")

    assert "ghost" in str(error.value)
    assert "alpha" in str(error.value)


def test_falls_back_when_the_router_names_an_unknown_agent(
    orchestrator: CrewOrchestrator, model: FakeChatModel
) -> None:
    """A bad choice from the router degrades to a keyword match."""
    model.router_reply = '{"agent": "ghost"}'

    result = orchestrator.run("I need help with angular signals")

    assert result.agent == "alpha"
    assert result.answer == "A deterministic answer."
    assert "keyword" in result.steps[0]["detail"]


def test_falls_back_to_the_first_agent(
    orchestrator: CrewOrchestrator, model: FakeChatModel
) -> None:
    """With nothing to match on, the first registered agent answers."""
    model.router_reply = "I could not decide."

    result = orchestrator.run("zzz qqq")

    assert result.agent == "alpha"
    assert "first agent" in result.steps[0]["detail"]


def test_an_empty_registry_is_an_error(settings: Settings) -> None:
    """A crew with no agents cannot answer anything."""
    orchestrator = CrewOrchestrator(settings, AgentRegistry({}))

    with pytest.raises(NoAgentsError):
        orchestrator.run("a question")


# ----------------------------------------------------------------------
# retrieval
# ----------------------------------------------------------------------
def test_retrieval_uses_the_agents_own_configuration(
    orchestrator: CrewOrchestrator, knowledge_bases: List[FakeKnowledgeBase]
) -> None:
    """The knowledge base is the agent's, and it is asked for top_k chunks."""
    orchestrator.run("what is a signal?", agent="beta")

    assert len(knowledge_bases) == 1
    knowledge_base = knowledge_bases[0]
    assert knowledge_base.definition.name == "beta"
    assert knowledge_base.definition.rag.resolved_collection("beta") == "beta"
    assert knowledge_base.searches[0] == ("what is a signal?", 4)
    assert orchestrator.run("what is a signal?", agent="beta").retrieved


def test_a_broken_knowledge_base_does_not_break_the_answer(
    settings: Settings, registry: AgentRegistry, model: FakeChatModel
) -> None:
    """Retrieval failures are recorded and the run continues."""

    def broken(
        crew_settings: Settings, definition: Any
    ) -> FakeKnowledgeBase:
        raise RuntimeError("the vector store is gone")

    orchestrator = CrewOrchestrator(
        settings,
        registry,
        llm_factory=lambda *args, **kwargs: model,
        knowledge_base_factory=broken,
    )

    result = orchestrator.run("a question", agent="alpha")

    assert result.answer == "A deterministic answer."
    assert result.retrieved == []
    failures = [
        step
        for step in result.steps
        if step["node"] == "retrieve" and "failed" in step["detail"]
    ]
    assert failures
    assert "the vector store is gone" in failures[0]["detail"]


# ----------------------------------------------------------------------
# the graph
# ----------------------------------------------------------------------
def test_every_step_names_a_node_and_a_detail(
    orchestrator: CrewOrchestrator,
) -> None:
    """The steps show the path taken through the graph."""
    result = orchestrator.run("a question", agent="alpha")

    assert [step["node"] for step in result.steps] == [
        "route",
        "retrieve",
        "answer",
    ]
    for step in result.steps:
        assert set(step) == {"node", "detail"}
        assert step["detail"]


def test_the_graph_is_exposed(orchestrator: CrewOrchestrator) -> None:
    """Advanced callers can drive the compiled graph themselves."""
    graph = orchestrator.graph()

    assert hasattr(graph, "invoke")
    assert hasattr(graph, "stream")


def test_streaming_yields_the_steps_as_they_happen(
    orchestrator: CrewOrchestrator,
) -> None:
    """The stream reports every node as it completes."""
    events = list(orchestrator.stream("a question", agent="alpha"))

    assert [event["node"] for event in events] == ["route", "retrieve", "answer"]
    assert events[-1]["answer"] == "A deterministic answer."


def test_list_agents_describes_the_crew(
    orchestrator: CrewOrchestrator,
) -> None:
    """The catalogue carries the capabilities and the collection."""
    listing = orchestrator.list_agents()

    assert [agent["name"] for agent in listing] == ["alpha", "beta"]
    assert listing[0]["capabilities"] == ["frontend", "angular"]
    assert listing[0]["collection"] == "alpha"
    assert listing[0]["top_k"] == 3


# ----------------------------------------------------------------------
# the HTTP service
# ----------------------------------------------------------------------
def _client(settings: Settings, orchestrator: Any = None) -> TestClient:
    """Build a test client for the service.

    Args:
        settings: The settings.
        orchestrator: The orchestrator to use instead of the real one.

    Returns:
        The client.
    """
    app = create_app(settings)
    if orchestrator is not None:
        app.state.orchestrator = orchestrator
    return TestClient(app)


def test_health_reports_the_agent_count(
    settings: Settings, agents_dir: Path
) -> None:
    """The health probe counts the agents."""
    settings.agents_dir = agents_dir
    client = _client(settings)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "agents": 2,
        "service": "agentic-crew",
    }


def test_agents_returns_the_catalogue(settings: Settings, agents_dir: Path) -> None:
    """The catalogue is served as JSON."""
    settings.agents_dir = agents_dir
    client = _client(settings)

    response = client.get("/agents")

    assert response.status_code == 200
    payload = response.json()
    assert [agent["name"] for agent in payload] == ["alpha", "beta"]
    assert payload[0]["capabilities"] == ["frontend", "angular"]


def test_run_answers_a_question(settings: Settings) -> None:
    """A question comes back with its answer and its path."""
    stub = StubOrchestrator()
    client = _client(settings, stub)

    response = client.post(
        "/run", json={"question": "how do I add a form?", "agent": "alpha"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "stubbed answer"
    assert payload["agent"] == "alpha"
    assert payload["session_id"] is None
    assert stub.calls == [("how do I add a form?", "alpha")]


def test_run_echoes_the_session_id(settings: Settings) -> None:
    """A caller-supplied session id is echoed back."""
    client = _client(settings, StubOrchestrator())

    response = client.post(
        "/run", json={"question": "q", "session_id": "abc-123"}
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "abc-123"


def test_run_with_an_unknown_agent_is_a_404(settings: Settings) -> None:
    """An unknown agent is a structured 404, not a bare 500."""
    stub = StubOrchestrator(error=UnknownAgentError("unknown agent 'ghost'"))
    client = _client(settings, stub)

    response = client.post("/run", json={"question": "q", "agent": "ghost"})

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == "unknown_agent"
    assert "ghost" in payload["message"]
    assert payload["status"] == 404


def test_run_with_a_broken_agent_is_a_structured_error(settings: Settings) -> None:
    """A failing agent produces a readable error body."""
    stub = StubOrchestrator(error=OrchestratorError("the gateway is down"))
    client = _client(settings, stub)

    response = client.post("/run", json={"question": "q"})

    assert response.status_code == 502
    payload = response.json()
    assert payload["error"] == "upstream_error"
    assert "the gateway is down" in payload["message"]


def test_run_without_a_question_is_a_422(settings: Settings) -> None:
    """A missing question is a validation error, not a crash."""
    client = _client(settings, StubOrchestrator())

    response = client.post("/run", json={})

    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


def test_ingest_rebuilds_a_knowledge_base(settings: Settings, agents_dir: Path) -> None:
    """The ingest endpoint returns the report of the knowledge base."""
    settings.agents_dir = agents_dir
    app = create_app(settings)
    app.state.knowledge_base_factory = (
        lambda crew_settings, definition: FakeKnowledgeBase(crew_settings, definition)
    )
    client = TestClient(app)

    response = client.post("/agents/alpha/ingest", json={"force": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent"] == "alpha"
    assert payload["force"] is True
    assert payload["report"]


def test_ingest_of_an_unknown_agent_is_a_404(settings: Settings) -> None:
    """An unknown agent cannot be ingested."""
    client = _client(settings)

    response = client.post("/agents/ghost/ingest", json={"force": False})

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_agent"


def test_the_bearer_token_is_required_when_one_is_configured(
    settings: Settings,
) -> None:
    """With a token configured, the mutating endpoints need it."""
    settings.conduit_api_key = "s3cret"
    stub = StubOrchestrator()
    client = _client(settings, stub)

    denied = client.post("/run", json={"question": "q"})
    assert denied.status_code == 401
    assert denied.json()["error"] == "missing_token"

    wrong = client.post(
        "/run", json={"question": "q"}, headers={"Authorization": "Bearer nope"}
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"] == "invalid_token"

    allowed = client.post(
        "/run", json={"question": "q"}, headers={"Authorization": "Bearer s3cret"}
    )
    assert allowed.status_code == 200


def test_the_open_service_answers_without_a_token(settings: Settings) -> None:
    """Without a token the service runs open, for local development."""
    client = _client(settings, StubOrchestrator())

    response = client.post("/run", json={"question": "q"})

    assert response.status_code == 200


def test_the_index_page_lists_the_agents(
    settings: Settings, agents_dir: Path
) -> None:
    """A human can open the root page and see the crew."""
    settings.agents_dir = agents_dir
    client = _client(settings)

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "alpha" in response.text
    assert "beta" in response.text


def test_cors_allows_the_local_n8n_origin(settings: Settings) -> None:
    """n8n runs on localhost, so the browser may call the service."""
    client = _client(settings)

    response = client.get("/agents", headers={"Origin": "http://localhost:5678"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5678"


# ----------------------------------------------------------------------
# the CLI
# ----------------------------------------------------------------------
def test_cli_agents_lists_the_crew(
    agents_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``agentic-crew agents`` prints the crew and its capabilities."""
    monkeypatch.setenv("AGENTIC_CREW_AGENTS_DIR", str(agents_dir))
    monkeypatch.setenv("AGENTIC_CREW_DATA_DIR", str(tmp_path / "data"))

    code = main(["agents"])

    out = capsys.readouterr().out
    assert code == 0
    assert "alpha" in out
    assert "beta" in out
    assert "angular" in out


def test_cli_agents_as_json(
    agents_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--json`` prints the catalogue as JSON."""
    monkeypatch.setenv("AGENTIC_CREW_AGENTS_DIR", str(agents_dir))
    monkeypatch.setenv("AGENTIC_CREW_DATA_DIR", str(tmp_path / "data"))

    code = main(["agents", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [agent["name"] for agent in payload] == ["alpha", "beta"]


def test_cli_doctor_checks_the_setup(
    agents_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``agentic-crew doctor`` reports on every part of the setup."""
    monkeypatch.setenv("AGENTIC_CREW_AGENTS_DIR", str(agents_dir))
    monkeypatch.setenv("AGENTIC_CREW_DATA_DIR", str(tmp_path / "data"))

    code = main(["doctor"])

    out = capsys.readouterr().out
    assert code == 0
    assert "gateway" in out
    assert "agents" in out
    assert "embeddings" in out
    assert "2 in " in out


def test_cli_doctor_as_json(
    agents_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``doctor --json`` prints the checks as JSON."""
    monkeypatch.setenv("AGENTIC_CREW_AGENTS_DIR", str(agents_dir))
    monkeypatch.setenv("AGENTIC_CREW_DATA_DIR", str(tmp_path / "data"))

    main(["doctor", "--json"])

    payload = json.loads(capsys.readouterr().out)
    checks = {check["check"]: check for check in payload["checks"]}
    assert checks["agents"]["status"] == "ok"
    assert "alpha" in checks["agents"]["detail"]


def test_cli_fails_loudly_on_a_bad_agent(
    agents_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure exits non-zero with a readable message."""
    monkeypatch.setenv("AGENTIC_CREW_AGENTS_DIR", str(agents_dir))
    monkeypatch.setenv("AGENTIC_CREW_DATA_DIR", str(tmp_path / "data"))

    code = main(["ask", "a question", "--agent", "ghost"])

    captured = capsys.readouterr()
    assert code == 1
    assert "ghost" in captured.err


def test_run_with_a_blank_question_is_a_400(settings: Settings) -> None:
    """A blank question is refused with a readable body."""
    stub = StubOrchestrator(error=ValueError("the question must not be empty"))
    client = _client(settings, stub)

    response = client.post("/run", json={"question": "   "})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
