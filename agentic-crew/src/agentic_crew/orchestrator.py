"""The master orchestrator.

The orchestrator is the only hardcoded piece of the crew. It is a LangGraph
with three nodes:

``route``
    Decide which specialist should answer. A caller may name the agent
    explicitly; otherwise the router model picks one from the registry
    catalogue, with a keyword match as a safety net.

``retrieve``
    Open the chosen specialist's private knowledge base and pull the chunks
    relevant to the question. A broken knowledge base must never break the
    answer, so failures are recorded and the run continues with no context.

``answer``
    Let the specialist answer with its own model, its own system prompt and
    the retrieved material.

Every LLM call goes through the Conduit gateway via :func:`build_chat_model`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from .config import Settings
from .llm import build_chat_model
from .models import AgentDefinition
from .registry import AgentRegistry

try:  # pragma: no cover - the RAG layer is an optional import at module load
    from .rag import RetrievedChunk
except Exception:  # noqa: BLE001 - the crew still runs without the RAG layer
    RetrievedChunk = Any  # type: ignore[assignment,misc]

log = logging.getLogger(__name__)

__all__ = [
    "CATALOGUE_MARKER",
    "CrewOrchestrator",
    "CrewState",
    "KnowledgeBaseUnavailable",
    "NoAgentsError",
    "OrchestratorError",
    "OrchestratorResult",
    "UnknownAgentError",
    "default_knowledge_base",
    "jsonable",
]


#: Marker that identifies the router prompt. Tests and callers use it to tell
#: a routing call apart from an answering call.
CATALOGUE_MARKER = "AVAILABLE SPECIALISTS:"

DELEGATE_MARKER = "YOUR SPECIALISTS:"

_ROUTER_SYSTEM = (
    "You are the router of a crew of specialist agents. You never answer the "
    "question yourself. You only choose the one specialist that should answer "
    "it, and you reply with the name of that specialist."
)

_ROUTER_TEMPERATURE = 0.0
_ROUTER_MAX_TOKENS = 128

_DELEGATE_SYSTEM = (
    "You are the master of a team of specialist agents. You decide whether to "
    "hand the question to one of your specialists or to answer it yourself. "
    "You reply with the name of the specialist, or with NONE."
)

_NONE_RE = re.compile(r"^(none|no\s?one|myself|self|keep|master)$", re.I)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    """
    a an and are as at be but by can do for from get has have how i if in into is it
    its me my of on or our so than that the their them then there these they this to
    was what when where which who why will with would you your use used using
    """.split()
)


class OrchestratorError(RuntimeError):
    """Raised when a run cannot be completed."""


class UnknownAgentError(OrchestratorError, ValueError):
    """Raised when a caller names an agent that is not in the registry."""


class NoAgentsError(OrchestratorError, ValueError):
    """Raised when the registry is empty and no agent can be chosen."""


class KnowledgeBaseUnavailable(OrchestratorError):
    """Raised when the RAG layer cannot be loaded at all."""


class _RouteChoice(BaseModel):
    """The structured answer of the router.

    Attributes:
        agent: The name of the chosen specialist.
    """

    agent: str = Field(
        default="",
        description="The name of the specialist agent that should answer the question.",
    )


class CrewState(TypedDict):
    """The state carried through the graph.

    Attributes:
        question: The question asked by the caller.
        agent: The name of the chosen specialist.
        retrieved_context: The material retrieved from its knowledge base.
        answer: The answer produced by the specialist.
        steps: The path taken through the graph, as ``node``/``detail`` pairs.
        retrieved: The retrieved chunks themselves.
    """

    question: str
    agent: str
    retrieved_context: str
    answer: str
    steps: list[dict]
    retrieved: list[Any]


@dataclass
class OrchestratorResult:
    """What one run produced.

    Attributes:
        answer: The specialist's answer.
        agent: The specialist that answered.
        steps: The path taken through the graph.
        retrieved: The chunks used as context.
    """

    answer: str
    agent: str
    steps: list[dict] = field(default_factory=list)
    retrieved: list[RetrievedChunk] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly mapping of the result.

        Returns:
            The result as plain dictionaries and lists.
        """
        return {
            "answer": self.answer,
            "agent": self.agent,
            "steps": [dict(step) for step in self.steps],
            "retrieved": [jsonable(chunk) for chunk in self.retrieved],
        }


def jsonable(value: Any) -> Any:
    """Convert a value into something JSON can encode.

    Args:
        value: Any value.

    Returns:
        A JSON-encodable version of the value.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return jsonable(value.model_dump())
        except Exception:  # noqa: BLE001 - fall through to the generic path
            pass
    if hasattr(value, "__dict__"):
        try:
            return {
                str(key): jsonable(item)
                for key, item in vars(value).items()
                if not str(key).startswith("_")
            }
        except Exception:  # noqa: BLE001 - fall through to the generic path
            pass
    return str(value)


def _tokens(text: str) -> List[str]:
    """Split text into comparable lowercase tokens.

    Args:
        text: The text to split.

    Returns:
        The meaningful tokens.
    """
    return [
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 2 and token not in _STOPWORDS
    ]


def _match_name(candidate: str, known: Sequence[str]) -> Optional[str]:
    """Match a free-text choice against the known agent names.

    Args:
        candidate: What the model said.
        known: The registered agent names.

    Returns:
        The canonical agent name, or None when nothing matches.
    """
    cleaned = candidate.strip().strip("\"'`.,:;!?[]{}").lower()
    if not cleaned:
        return None
    for name in known:
        if cleaned == name.lower():
            return name
    for name in known:
        if name.lower() in cleaned:
            return name
    return None


def _parse_choice(text: str, known: Sequence[str]) -> Optional[str]:
    """Extract the chosen agent name from a router reply.

    The router is asked for JSON, but a model that ignores the format must
    not break the run, so plain text is parsed too.

    Args:
        text: The raw reply.
        known: The registered agent names.

    Returns:
        The chosen agent name, or None when the reply names nothing usable.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    candidates: List[str] = []

    def _collect(payload: Any) -> None:
        if isinstance(payload, str):
            candidates.append(payload)
        elif isinstance(payload, dict):
            for key in ("agent", "name", "choice", "selected", "specialist"):
                value = payload.get(key)
                if isinstance(value, str):
                    candidates.append(value)
        elif isinstance(payload, list):
            for item in payload:
                _collect(item)

    try:
        _collect(json.loads(cleaned))
    except (ValueError, TypeError):
        pass

    if not candidates:
        for match in re.finditer(r"\{[^{}]*\}", cleaned):
            try:
                _collect(json.loads(match.group(0)))
            except (ValueError, TypeError):
                continue

    for candidate in candidates:
        matched = _match_name(candidate, known)
        if matched:
            return matched

    # The model ignored the format and wrote prose. Accept it when it names a
    # registered agent somewhere in the text.
    lowered = cleaned.lower()
    for name in known:
        if name.lower() in lowered:
            return name
    return None


def _score_agent(question: str, agent: AgentDefinition) -> int:
    """Score how well an agent matches a question by keyword.

    Args:
        question: The question.
        agent: The candidate agent.

    Returns:
        The score. Zero means no match at all.
    """
    lowered = question.lower()
    score = 0

    name = agent.name.lower()
    if name and name in lowered:
        score += 50
    else:
        for part in re.split(r"[-_\s]+", name):
            if len(part) > 2 and part in lowered:
                score += 15

    for capability in agent.capabilities:
        if capability.strip().lower() in lowered:
            score += 10

    for token in _tokens(agent.description):
        if token in lowered:
            score += 2

    for token in _tokens(agent.role):
        if token in lowered:
            score += 1

    return score


def default_knowledge_base(settings: Settings, definition: AgentDefinition) -> Any:
    """Build the knowledge base of one specialist.

    Args:
        settings: The runtime settings.
        definition: The specialist's definition.

    Returns:
        The knowledge base.

    Raises:
        KnowledgeBaseUnavailable: If the RAG layer cannot be imported.
    """
    try:
        from .rag import AgentKnowledgeBase
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        raise KnowledgeBaseUnavailable(
            f"the RAG layer (agentic_crew.rag) is not available: {exc}"
        ) from exc
    return AgentKnowledgeBase(settings, definition)


def _message_text(response: Any) -> str:
    """Pull the text out of a chat model response.

    Args:
        response: The model response.

    Returns:
        The text content.
    """
    content = getattr(response, "content", None)
    if content is None:
        return str(response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(str(text))
        return "\n".join(parts)
    return str(content)


class CrewOrchestrator:
    """The master orchestrator of the crew.

    Attributes:
        settings: The runtime settings.
        registry: The specialists the orchestrator may route to.
    """

    def __init__(
        self,
        settings: Settings,
        registry: AgentRegistry,
        llm_factory: Optional[Callable[..., BaseChatModel]] = None,
        knowledge_base_factory: Optional[Callable[[Settings, AgentDefinition], Any]] = None,
    ) -> None:
        """Build the orchestrator and compile its graph.

        Args:
            settings: The runtime settings.
            registry: The specialists to route to.
            llm_factory: Builds a chat model. Defaults to
                :func:`agentic_crew.llm.build_chat_model`. It is called as
                ``factory(base_url, api_key, model, temperature, max_tokens)``.
            knowledge_base_factory: Builds the knowledge base of a
                specialist. Defaults to the RAG layer's
                :class:`~agentic_crew.rag.AgentKnowledgeBase`.

        Raises:
            NoAgentsError: If the registry is empty.
        """
        self.settings = settings
        self.registry = registry
        self._llm_factory = llm_factory or build_chat_model
        self._knowledge_base_factory = (
            knowledge_base_factory or default_knowledge_base
        )
        self._graph = self._build_graph()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def graph(self) -> Any:
        """Return the compiled LangGraph.

        Returns:
            The compiled graph, for callers that want to drive it directly.
        """
        return self._graph

    def list_agents(self) -> List[Dict[str, Any]]:
        """Describe every registered specialist.

        Returns:
            One mapping per agent, with its capabilities and its collection.
        """
        listing: List[Dict[str, Any]] = []
        for agent in self.registry.all():
            listing.append(
                {
                    "name": agent.name,
                    "role": agent.role,
                    "description": agent.description,
                    "capabilities": list(agent.capabilities),
                    "model": agent.model or self.settings.default_model,
                    "temperature": agent.temperature,
                    "max_tokens": agent.max_tokens,
                    "collection": agent.rag.resolved_collection(agent.name),
                    "top_k": agent.rag.top_k,
                    "rag_sources": len(agent.rag.sources),
                    "skills": [skill.name for skill in agent.skills],
                    "source": str(agent.source_path) if agent.source_path else "",
                }
            )
        return listing

    def run(self, question: str, agent: Optional[str] = None) -> OrchestratorResult:
        """Answer a question with the crew.

        Args:
            question: The question to answer.
            agent: The name of the specialist to use. When omitted, the
                router chooses one.

        Returns:
            The answer, the agent that produced it, the path taken and the
            retrieved chunks.

        Raises:
            ValueError: If the question is empty.
            UnknownAgentError: If the named agent is not registered.
            NoAgentsError: If no agent could be chosen.
            OrchestratorError: If the specialist could not answer.
        """
        state = self._initial_state(question, agent)
        final = self._graph.invoke(state)
        answer = str(final.get("answer", "") or "")
        if not answer:
            raise OrchestratorError(
                f"agent {final.get('agent', '')!r} returned an empty answer"
            )
        return OrchestratorResult(
            answer=answer,
            agent=str(final.get("agent", "") or ""),
            steps=[dict(step) for step in (final.get("steps") or [])],
            retrieved=list(final.get("retrieved") or []),
        )

    def stream(
        self, question: str, agent: Optional[str] = None
    ) -> Iterator[Dict[str, Any]]:
        """Run the graph, yielding one event per node as it completes.

        Args:
            question: The question to answer.
            agent: The name of the specialist to use.

        Yields:
            One mapping per node, with the node name, the step detail and
            the answer once the specialist has produced it.
        """
        state = self._initial_state(question, agent)
        seen = 0
        for update in self._graph.stream(state, stream_mode="updates"):
            if not isinstance(update, dict):
                continue
            for node, payload in update.items():
                if not isinstance(payload, dict):
                    continue
                steps = payload.get("steps") or []
                fresh = [
                    step for step in steps[seen:] if isinstance(step, dict)
                ]
                seen = max(seen, len(steps))
                detail = str(fresh[-1].get("detail", "")) if fresh else ""
                yield {
                    "node": str(node),
                    "detail": detail,
                    "answer": str(payload.get("answer", "") or ""),
                    "steps": fresh,
                }

    # ------------------------------------------------------------------
    # graph
    # ------------------------------------------------------------------
    def _initial_state(
        self, question: str, agent: Optional[str]
    ) -> CrewState:
        """Build the initial graph state.

        Args:
            question: The question.
            agent: The explicitly requested agent, if any.

        Returns:
            The initial state.

        Raises:
            ValueError: If the question is empty.
        """
        if not question or not question.strip():
            raise ValueError("the question must not be empty")
        return {
            "question": question,
            "agent": (agent or "").strip(),
            "retrieved_context": "",
            "answer": "",
            "steps": [],
            "retrieved": [],
        }

    def _build_graph(self) -> Any:
        """Compile the LangGraph.

        Returns:
            The compiled graph.
        """
        builder = StateGraph(CrewState)
        builder.add_node("route", self._route)
        builder.add_node("delegate", self._delegate)
        builder.add_node("retrieve", self._retrieve)
        builder.add_node("answer", self._answer)
        builder.add_edge(START, "route")
        builder.add_edge("route", "delegate")
        builder.add_edge("delegate", "retrieve")
        builder.add_edge("retrieve", "answer")
        builder.add_edge("answer", END)
        return builder.compile()

    # ------------------------------------------------------------------
    # nodes
    # ------------------------------------------------------------------
    def _route(self, state: CrewState) -> Dict[str, Any]:
        """Choose the specialist that should answer.

        Args:
            state: The current state.

        Returns:
            The state update.

        Raises:
            UnknownAgentError: If the named agent is not registered.
            NoAgentsError: If no agent could be chosen.
        """
        steps = list(state.get("steps") or [])
        requested = str(state.get("agent") or "").strip()

        if requested:
            if self.registry.get(requested) is None:
                known = ", ".join(self.registry.names()) or "none"
                raise UnknownAgentError(
                    f"unknown agent {requested!r}; registered agents: {known}"
                )
            steps.append(
                {"node": "route", "detail": f"explicitly requested agent {requested!r}"}
            )
            return {"agent": requested, "steps": steps}

        chosen, detail = self._choose_agent(str(state.get("question", "")))
        steps.append({"node": "route", "detail": detail})
        return {"agent": chosen, "steps": steps}

    def _delegate(self, state: CrewState) -> Dict[str, Any]:
        """Let a master agent hand the work to one of its specialists.

        An agent that declares no delegates, or whose delegates are not
        registered, keeps the question and answers it itself.

        Args:
            state: The current state.

        Returns:
            The state update.
        """
        steps = list(state.get("steps") or [])
        name = str(state.get("agent") or "")
        definition = self.registry.get(name)
        delegates = [
            delegate
            for delegate in (list(getattr(definition, "delegates", None) or []))
            if self.registry.get(delegate) is not None
        ]
        if not delegates:
            steps.append(
                {
                    "node": "delegate",
                    "detail": f"{name!r} has no specialists, it answers itself",
                }
            )
            return {"steps": steps}

        question = str(state.get("question", ""))
        chosen: Optional[str] = None
        detail = ""

        try:
            reply = self._ask_delegate(definition, question, delegates)
            if _NONE_RE.match(_choice_text(reply).strip().strip("\"'`.,:;!?[]{}")):
                steps.append(
                    {"node": "delegate", "detail": f"{name!r} kept the question itself"}
                )
                return {"steps": steps}
            chosen = _parse_choice(reply, delegates)
            if chosen:
                detail = f"{name!r} delegated to {chosen!r}"
        except Exception as exc:  # noqa: BLE001 - delegation is best effort
            log.warning("the delegation check failed: %s", exc)
            detail = f"delegation check failed: {exc}"

        if not chosen:
            scored = [
                (_score_agent(question, self.registry.get(delegate)), delegate)
                for delegate in delegates
            ]
            best_score, best = max(scored, key=lambda item: item[0])
            if best_score > 0:
                chosen = best
                detail = (
                    f"{name!r} delegated to {chosen!r} "
                    f"(keyword match, score {best_score})"
                )

        if not chosen:
            steps.append(
                {"node": "delegate", "detail": f"{name!r} kept the question itself"}
            )
            return {"steps": steps}

        steps.append({"node": "delegate", "detail": detail})
        return {"agent": chosen, "steps": steps}

    def _ask_delegate(
        self,
        definition: Any,
        question: str,
        delegates: Sequence[str],
    ) -> str:
        """Ask a master which of its specialists should take the question.

        Args:
            definition: The master agent.
            question: The question.
            delegates: The names the master may hand to.

        Returns:
            The master's reply.
        """
        catalogue = "\n".join(
            f"- {name}: {self.registry.get(name).description}"
            for name in delegates
            if self.registry.get(name) is not None
        )
        prompt = (
            f"You are the master agent {getattr(definition, 'name', 'master')!r}.\n"
            f"Question: {question}\n\n"
            f"{DELEGATE_MARKER}\n{catalogue}\n\n"
            'Reply with JSON: {"agent": "<specialist name>"} to hand the '
            'question over, or {"agent": "NONE"} to answer it yourself.'
        )

        model = self._llm_factory(
            self.settings.conduit_base_url,
            self.settings.conduit_api_key,
            getattr(definition, "model", "") or self.settings.default_model,
            getattr(definition, "temperature", 0.0),
            getattr(definition, "max_tokens", 2048),
        )
        messages: List[BaseMessage] = [
            SystemMessage(content=_DELEGATE_SYSTEM),
            HumanMessage(content=prompt),
        ]

        try:
            structured = model.with_structured_output(_RouteChoice)
            parsed = structured.invoke(messages)
            choice = _choice_text(parsed)
            if choice:
                return choice
        except Exception as exc:  # noqa: BLE001 - not every model can do this
            log.debug("structured delegation unavailable: %s", exc)

        return _message_text(model.invoke(messages))

    def _delegate_brief(self, definition: Any) -> str:
        """Describe a master's specialists, for its own system prompt.

        Args:
            definition: The master agent.

        Returns:
            A short brief, or an empty string when it has no specialists.
        """
        delegates = [
            delegate
            for delegate in (list(getattr(definition, "delegates", None) or []))
            if self.registry.get(delegate) is not None
        ]
        if not delegates:
            return ""
        lines = [
            f"- {name}: {self.registry.get(name).description}"
            for name in delegates
            if self.registry.get(name) is not None
        ]
        return (
            "You lead a team. When the question belongs to one of these "
            "specialists, say so and answer from their field instead:\n"
            + "\n".join(lines)
        )

    def _retrieve(self, state: CrewState) -> Dict[str, Any]:
        """Retrieve the material relevant to the question.

        A failure here is recorded and the run continues without context.

        Args:
            state: The current state.

        Returns:
            The state update.
        """
        steps = list(state.get("steps") or [])
        name = str(state.get("agent") or "")
        definition = self.registry.get(name)
        if definition is None:
            steps.append(
                {"node": "retrieve", "detail": f"agent {name!r} vanished from the registry"}
            )
            return {"retrieved_context": "", "retrieved": [], "steps": steps}

        collection = definition.rag.resolved_collection(definition.name)
        try:
            knowledge_base = self._knowledge_base_factory(self.settings, definition)
            chunks = knowledge_base.search(
                state.get("question", ""), top_k=definition.rag.top_k
            )
            retrieved = list(chunks or [])
            context = knowledge_base.context(
                state.get("question", ""), top_k=definition.rag.top_k
            )
            steps.append(
                {
                    "node": "retrieve",
                    "detail": (
                        f"{len(retrieved)} chunk(s) from collection {collection!r} "
                        f"for agent {name!r}"
                    ),
                }
            )
            return {
                "retrieved_context": str(context or ""),
                "retrieved": retrieved,
                "steps": steps,
            }
        except Exception as exc:  # noqa: BLE001 - retrieval must never break a run
            log.warning("retrieval failed for %s: %s", name, exc)
            steps.append(
                {
                    "node": "retrieve",
                    "detail": f"retrieval failed for {name!r}: {exc}",
                }
            )
            return {"retrieved_context": "", "retrieved": [], "steps": steps}

    def _answer(self, state: CrewState) -> Dict[str, Any]:
        """Let the chosen specialist answer.

        Args:
            state: The current state.

        Returns:
            The state update.

        Raises:
            OrchestratorError: If the specialist could not answer.
        """
        steps = list(state.get("steps") or [])
        name = str(state.get("agent") or "")
        definition = self.registry.get(name)
        if definition is None:
            raise OrchestratorError(f"agent {name!r} is not registered")

        model_name = definition.model or self.settings.default_model
        try:
            model = self._llm_factory(
                self.settings.conduit_base_url,
                self.settings.conduit_api_key,
                model_name,
                definition.temperature,
                definition.max_tokens,
            )
            messages: List[BaseMessage] = []
            system_prompt = definition.system_prompt(
                str(state.get("retrieved_context", "") or "")
            )
            brief = self._delegate_brief(definition)
            if brief:
                system_prompt = f"{system_prompt}\n\n{brief}".strip()
            if system_prompt.strip():
                messages.append(SystemMessage(content=system_prompt))
            messages.append(HumanMessage(content=str(state.get("question", ""))))
            answer = _message_text(model.invoke(messages))
        except Exception as exc:  # noqa: BLE001 - reported with context
            log.warning("agent %s failed to answer: %s", name, exc)
            raise OrchestratorError(
                f"agent {name!r} could not answer: {exc}"
            ) from exc

        steps.append(
            {
                "node": "answer",
                "detail": f"model {model_name!r} answered {len(answer)} characters",
            }
        )
        return {"answer": answer, "steps": steps}

    # ------------------------------------------------------------------
    # routing helpers
    # ------------------------------------------------------------------
    def _choose_agent(self, question: str) -> tuple[str, str]:
        """Choose a specialist for a question.

        The router model is asked first. When it fails, or names an agent
        that does not exist, the choice degrades to a keyword match and then
        to the first registered agent.

        Args:
            question: The question.

        Returns:
            The chosen agent name and a human-readable detail.

        Raises:
            NoAgentsError: If the registry is empty.
        """
        catalogue = self.registry.catalogue()
        known = [str(entry.get("name", "")) for entry in catalogue if entry.get("name")]
        if not known:
            raise NoAgentsError(
                "no agents are registered; add a YAML file to the agents directory"
            )

        detail = ""
        try:
            reply = self._ask_router(question, known)
            chosen = _parse_choice(reply, known)
            if chosen:
                return chosen, f"router model chose {chosen!r}"
            detail = (
                "router model did not name a registered agent "
                f"(reply: {reply.strip()[:160]!r})"
            )
        except Exception as exc:  # noqa: BLE001 - the router is best effort
            log.warning("the router model failed: %s", exc)
            detail = f"router model failed: {exc}"

        scored = [
            (_score_agent(question, agent), agent.name)
            for agent in self.registry.all()
        ]
        best_score, best_name = max(scored, key=lambda item: item[0])

        if best_score > 0:
            return (
                best_name,
                f"{detail}; keyword match chose {best_name!r} (score {best_score})",
            )

        first = known[0]
        return first, f"{detail}; fell back to the first agent {first!r}"

    def _ask_router(self, question: str, known: Sequence[str]) -> str:
        """Ask the router model which specialist should answer.

        A structured output is preferred, because it yields a plain name
        rather than prose. Models that cannot do structured output fall back
        to the JSON the prompt asks for.

        Args:
            question: The question.
            known: The registered agent names.

        Returns:
            The router's reply.
        """
        prompt = self._routing_prompt(question)
        model = self._router_model()
        messages = [
            SystemMessage(content=_ROUTER_SYSTEM),
            HumanMessage(content=prompt),
        ]

        try:
            structured = model.with_structured_output(_RouteChoice)
            parsed = structured.invoke(messages)
            choice = _choice_text(parsed)
            if choice:
                return choice
        except Exception as exc:  # noqa: BLE001 - not every model can do this
            log.debug("structured routing unavailable: %s", exc)

        return _message_text(model.invoke(messages))

    def _routing_prompt(self, question: str) -> str:
        """Build the router prompt.

        Args:
            question: The question.

        Returns:
            The prompt text.
        """
        lines: List[str] = [CATALOGUE_MARKER]
        for entry in self.registry.catalogue():
            lines.append(f"- name: {entry.get('name', '')}")
            if entry.get("role"):
                lines.append(f"  role: {entry.get('role')}")
            if entry.get("description"):
                lines.append(f"  description: {entry.get('description')}")
            if entry.get("capabilities"):
                lines.append(
                    "  capabilities: " + ", ".join(
                        str(cap) for cap in (entry.get("capabilities") or [])
                    )
                )
        catalogue = "\n".join(lines)
        return (
            f"{catalogue}\n\n"
            f"Question: {question}\n\n"
            "Choose the one specialist that should answer the question. "
            'Reply with JSON only, in the form {"agent": "<name>"}, '
            "where <name> is one of the names listed above."
        )

    def _router_model(self) -> BaseChatModel:
        """Build the cheap routing model.

        Returns:
            A chat model on the default model.
        """
        return self._llm_factory(
            self.settings.conduit_base_url,
            self.settings.conduit_api_key,
            self.settings.default_model,
            _ROUTER_TEMPERATURE,
            _ROUTER_MAX_TOKENS,
        )


def _choice_text(parsed: Any) -> str:
    """Pull the agent name out of a structured routing result.

    Args:
        parsed: Whatever ``with_structured_output`` returned.

    Returns:
        The agent name, or an empty string.
    """
    if parsed is None:
        return ""
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, dict):
        for key in ("agent", "name", "choice", "selected", "specialist"):
            value = parsed.get(key)
            if isinstance(value, str):
                return value
        return ""
    value = getattr(parsed, "agent", None)
    if isinstance(value, str):
        return value
    return ""
