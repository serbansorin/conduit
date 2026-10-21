"""Shared fixtures for the crew tests.

The tests never touch the network and never call a real model: a fake chat
model stands in for the gateway, and a fake knowledge base stands in for the
RAG layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from agentic_crew.config import Settings
from agentic_crew.models import AgentDefinition, RagConfig
from agentic_crew.orchestrator import CATALOGUE_MARKER, CrewOrchestrator
from agentic_crew.registry import AgentRegistry

ALPHA_YAML = """
name: alpha
description: "Angular specialist. Use for components, signals and forms."
role: Angular engineer
model: glm-5.3
temperature: 0.1
max_tokens: 1024
capabilities: [frontend, angular]
rag:
  collection: alpha
  top_k: 3
"""

BETA_YAML = """
name: beta
description: "Laravel specialist. Use for controllers, jobs and models."
role: Laravel engineer
model: glm-5.3
capabilities: [backend, laravel]
rag:
  collection: beta
  top_k: 4
"""


# The RAG layer (agentic_crew.rag) belongs to another part of the project.
# Nothing here imports it: the orchestrator and the service both accept an
# injected knowledge-base factory, so the fakes below are enough. That also
# means these tests never shadow the real module for anyone else's tests.


@dataclass
class FakeChunk:
    """A chunk of retrieved text.

    Attributes:
        content: The text.
        source: Where it came from.
        score: How relevant it is.
    """

    content: str = ""
    source: str = ""
    score: float = 0.0


class FakeChatModel(BaseChatModel):
    """A chat model that answers from a script.

    Attributes:
        router_reply: What the router is told.
        answer_reply: What the specialist says.
        fail: When true, every call raises.
        calls: Every prompt the model was given.
    """

    router_reply: str = '{"agent": "alpha"}'
    answer_reply: str = "A deterministic answer."
    fail: bool = False
    calls: List[Dict[str, Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        """Return the provider identifier LangChain reports."""
        return "fake"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Answer from the script.

        Args:
            messages: The prompt.
            stop: Unused.
            run_manager: Unused.
            **kwargs: Unused.

        Returns:
            The scripted answer.

        Raises:
            RuntimeError: When the model is set up to fail.
        """
        text = "\n".join(str(message.content) for message in messages)
        self.calls.append(
            {
                "text": text,
                "is_router": CATALOGUE_MARKER in text,
                "system": [
                    str(message.content)
                    for message in messages
                    if getattr(message, "type", "") == "system"
                ],
            }
        )
        if self.fail:
            raise RuntimeError("the gateway is unreachable")
        reply = self.router_reply if CATALOGUE_MARKER in text else self.answer_reply
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply))])


class FakeKnowledgeBase:
    """A knowledge base that returns a fixed passage.

    Attributes:
        settings: The settings it was built with.
        definition: The agent it belongs to.
        chunks: What it returns from a search.
        context_text: What it returns as context.
        searches: Every search it served.
    """

    def __init__(
        self,
        settings: Settings,
        definition: AgentDefinition,
        chunks: Optional[List[FakeChunk]] = None,
        context_text: str = "## notes.md\nAngular signals are computed values.",
    ) -> None:
        """Build the fake knowledge base.

        Args:
            settings: The settings.
            definition: The agent.
            chunks: The chunks a search returns.
            context_text: The context a search returns.
        """
        self.settings = settings
        self.definition = definition
        self.chunks = chunks if chunks is not None else [FakeChunk(content="hello")]
        self.context_text = context_text
        self.searches: List[Tuple[str, Optional[int]]] = []

    def search(self, query: str, top_k: Optional[int] = None) -> List[FakeChunk]:
        """Return the fixed chunks.

        Args:
            query: The query.
            top_k: The requested chunk count.

        Returns:
            The chunks.
        """
        self.searches.append((query, top_k))
        return list(self.chunks)

    def context(self, query: str, top_k: Optional[int] = None) -> str:
        """Return the fixed context.

        Args:
            query: The query.
            top_k: The requested chunk count.

        Returns:
            The context text.
        """
        self.searches.append((query, top_k))
        return self.context_text

    def ingest(self, force: bool = False) -> Dict[str, Any]:
        """Return a fixed ingest report.

        Args:
            force: Whether the ingest was forced.

        Returns:
            The report.
        """
        return {
            "agent": self.definition.name,
            "collection": self.definition.rag.resolved_collection(self.definition.name),
            "documents": 2,
            "chunks": 5,
            "skipped": not force,
            "messages": [],
        }


@pytest.fixture()
def model() -> FakeChatModel:
    """Return the fake chat model.

    Returns:
        The model.
    """
    return FakeChatModel()


@pytest.fixture()
def definitions() -> List[AgentDefinition]:
    """Return the specialists used by the tests.

    Returns:
        The definitions.
    """
    return [
        AgentDefinition(
            name="alpha",
            description="Angular specialist. Use for components and signals.",
            role="Angular engineer",
            model="glm-5.3",
            temperature=0.1,
            max_tokens=1024,
            capabilities=["frontend", "angular"],
            rag=RagConfig(collection="alpha", top_k=3),
        ),
        AgentDefinition(
            name="beta",
            description="Laravel specialist. Use for controllers and jobs.",
            role="Laravel engineer",
            model="glm-5.3",
            capabilities=["backend", "laravel"],
            rag=RagConfig(collection="beta", top_k=4),
        ),
    ]


@pytest.fixture()
def registry(definitions: List[AgentDefinition]) -> AgentRegistry:
    """Return a registry over the test specialists.

    Args:
        definitions: The definitions.

    Returns:
        The registry.
    """
    return AgentRegistry({item.name: item for item in definitions})


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """Return settings that point at temporary directories.

    Args:
        tmp_path: The temporary directory.

    Returns:
        The settings.
    """
    return Settings(
        conduit_base_url="http://localhost:9812",
        conduit_api_key="",
        agents_dir=tmp_path / "agents",
        data_dir=tmp_path / "data",
        default_model="cheap-model",
    )


@pytest.fixture()
def knowledge_bases() -> List[FakeKnowledgeBase]:
    """Return the knowledge bases handed to the orchestrator.

    Returns:
        The knowledge bases.
    """
    return []


@pytest.fixture()
def orchestrator(
    settings: Settings,
    registry: AgentRegistry,
    model: FakeChatModel,
    knowledge_bases: List[FakeKnowledgeBase],
) -> CrewOrchestrator:
    """Return an orchestrator wired to the fakes.

    Args:
        settings: The settings.
        registry: The registry.
        model: The fake chat model.
        knowledge_bases: The fake knowledge bases.

    Returns:
        The orchestrator.
    """
    calls: List[Dict[str, Any]] = []

    def llm_factory(
        base_url: str,
        api_key: str,
        model_name: str,
        temperature: float,
        max_tokens: int,
    ) -> FakeChatModel:
        calls.append(
            {
                "base_url": base_url,
                "api_key": api_key,
                "model": model_name,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return model

    def knowledge_base_factory(
        crew_settings: Settings, definition: AgentDefinition
    ) -> FakeKnowledgeBase:
        knowledge_base = FakeKnowledgeBase(crew_settings, definition)
        knowledge_bases.append(knowledge_base)
        return knowledge_base

    orchestrator = CrewOrchestrator(
        settings,
        registry,
        llm_factory=llm_factory,
        knowledge_base_factory=knowledge_base_factory,
    )
    orchestrator.llm_calls = calls  # type: ignore[attr-defined]
    return orchestrator


@pytest.fixture()
def agents_dir(tmp_path: Path) -> Path:
    """Return a directory holding two agent definitions.

    Args:
        tmp_path: The temporary directory.

    Returns:
        The directory.
    """
    directory = tmp_path / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "alpha.yaml").write_text(ALPHA_YAML, encoding="utf-8")
    (directory / "beta.yaml").write_text(BETA_YAML, encoding="utf-8")
    return directory


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's environment out of the tests.

    Args:
        monkeypatch: The monkeypatch fixture.
    """
    for name in (
        "AGENTIC_CREW_API_KEY",
        "AGENTIC_CREW_AGENTS_DIR",
        "AGENTIC_CREW_DATA_DIR",
        "AGENTIC_CREW_DEFAULT_MODEL",
        "AGENTIC_CREW_HOST",
        "AGENTIC_CREW_PORT",
        "CONDUIT_API_KEY",
        "CONDUIT_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
