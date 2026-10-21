"""Schemas for agent definitions.

An agent is a YAML file. The orchestrator is code; everything else is data,
so a new specialist is a file, not a code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "AgentDefinition",
    "SkillSource",
    "RagSource",
    "RagConfig",
    "load_definition",
]


@dataclass
class SkillSource:
    """A directory of skill files folded into an agent's system prompt.

    Attributes:
        name: Label for the group, used in the prompt.
        path: Directory to read skills from.
        include: Glob pattern selecting files, relative to ``path``.
        max_chars: Cap on the characters contributed to the prompt.
    """

    name: str
    path: Path
    include: str = "**/*.md"
    max_chars: int = 12000

    def expand(self) -> Path:
        """Return the resolved source directory."""
        return Path(os.path.expanduser(str(self.path))).resolve()


@dataclass
class RagSource:
    """A corpus of documents ingested into an agent's vector store.

    Attributes:
        path: File or directory to ingest.
        include: Glob pattern selecting files, relative to ``path``.
        extensions: File extensions to ingest when ``path`` is a directory.
    """

    path: Path
    include: Optional[str] = None
    extensions: List[str] = field(
        default_factory=lambda: [".md", ".txt", ".pdf", ".html", ".rst"]
    )

    def expand(self) -> Path:
        """Return the resolved source path."""
        return Path(os.path.expanduser(str(self.path))).resolve()


@dataclass
class RagConfig:
    """The RAG configuration of an agent.

    Attributes:
        collection: Vector store collection name. Defaults to the agent name.
        sources: Documents to ingest.
        chunk_size: Characters per chunk.
        chunk_overlap: Overlap between chunks.
        top_k: Chunks retrieved per query.
    """

    collection: Optional[str] = None
    sources: List[RagSource] = field(default_factory=list)
    chunk_size: int = 900
    chunk_overlap: int = 150
    top_k: int = 5

    def resolved_collection(self, agent_name: str) -> str:
        """Return the collection name, defaulting to the agent's name."""
        return self.collection or agent_name


@dataclass
class AgentDefinition:
    """A single agent, loaded from one YAML file.

    Attributes:
        name: Unique identifier. Also the default collection name.
        description: One line describing when to pick this agent.
        role: Short role label shown in the prompt.
        model: Model id as the gateway knows it.
        instructions: The agent's system prompt.
        temperature: Sampling temperature.
        max_tokens: Response cap.
        capabilities: Tags the orchestrator routes on.
        skills: Skill directories folded into the prompt.
        rag: The agent's retrieval configuration.
        tools: Names of tools the agent may call.
        delegates: Other agents this one may hand work to.
        source_path: The file this definition was loaded from.
    """

    name: str
    description: str = ""
    role: str = ""
    model: str = "glm-5.3"
    instructions: str = ""
    temperature: float = 0.2
    max_tokens: int = 4096
    capabilities: List[str] = field(default_factory=list)
    skills: List[SkillSource] = field(default_factory=list)
    rag: RagConfig = field(default_factory=RagConfig)
    tools: List[str] = field(default_factory=list)
    delegates: List[str] = field(default_factory=list)
    source_path: Optional[Path] = None

    def system_prompt(self, retrieved_context: str = "") -> str:
        """Build the system prompt for this agent.

        Args:
            retrieved_context: RAG passages relevant to the current question.

        Returns:
            The assembled system prompt.
        """
        parts: List[str] = []
        if self.role:
            parts.append(f"Role: {self.role}")
        if self.instructions:
            parts.append(self.instructions.strip())
        if retrieved_context.strip():
            parts.append(
                "Reference material retrieved from your knowledge base. "
                "Ground your answer in it and cite the source file when you use it.\n\n"
                + retrieved_context.strip()
            )
        return "\n\n".join(parts).strip()


def _as_source(raw: Dict[str, Any], kind: str) -> Any:
    """Build a skill or RAG source from its YAML form.

    Args:
        raw: The mapping from YAML.
        kind: Either ``skill`` or ``rag``.

    Returns:
        A :class:`SkillSource` or :class:`RagSource`.

    Raises:
        ValueError: If the mapping is missing its path.
    """
    if "path" not in raw:
        raise ValueError(f"{kind} entry is missing 'path': {raw!r}")
    path = Path(os.path.expanduser(str(raw["path"])))
    if kind == "skill":
        return SkillSource(
            name=str(raw.get("name", path.name)),
            path=path,
            include=str(raw.get("include", "**/*.md")),
            max_chars=int(raw.get("max_chars", 12000)),
        )
    return RagSource(
        path=path,
        include=raw.get("include"),
        extensions=list(raw.get("extensions", [".md", ".txt", ".pdf", ".html", ".rst"])),
    )


def load_definition(path: Path) -> AgentDefinition:
    """Load one agent definition from a YAML file.

    Args:
        path: The YAML file.

    Returns:
        The parsed definition.

    Raises:
        ValueError: If the file is malformed or has no name.
    """
    import yaml

    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a YAML mapping")

    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError(f"{path}: 'name' is required")

    rag_raw = raw.get("rag") or {}
    if not isinstance(rag_raw, dict):
        raise ValueError(f"{path}: 'rag' must be a mapping")

    definition = AgentDefinition(
        name=name,
        description=str(raw.get("description", "")).strip(),
        role=str(raw.get("role", "")).strip(),
        model=str(raw.get("model", "glm-5.3")).strip(),
        instructions=str(raw.get("instructions", "")),
        temperature=float(raw.get("temperature", 0.2)),
        max_tokens=int(raw.get("max_tokens", 4096)),
        capabilities=[str(c) for c in (raw.get("capabilities") or [])],
        skills=[_as_source(s, "skill") for s in (raw.get("skills") or [])],
        rag=RagConfig(
            collection=rag_raw.get("collection"),
            sources=[_as_source(s, "rag") for s in (rag_raw.get("sources") or [])],
            chunk_size=int(rag_raw.get("chunk_size", 900)),
            chunk_overlap=int(rag_raw.get("chunk_overlap", 150)),
            top_k=int(rag_raw.get("top_k", 5)),
        ),
        tools=[str(t) for t in (raw.get("tools") or [])],
        delegates=[str(d) for d in (raw.get("delegates") or [])],
        source_path=path,
    )
    return definition
