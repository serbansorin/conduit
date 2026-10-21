"""The agent registry.

The orchestrator is code; the crew is data. This module turns a directory of
YAML files into a lookup the orchestrator can route against.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .models import AgentDefinition, load_definition

__all__ = ["AgentRegistry"]

log = logging.getLogger(__name__)


class AgentRegistry:
    """Every agent definition found on disk.

    Attributes:
        agents: The loaded definitions, keyed by name.
        errors: Problems found while loading, for reporting rather than crashing.
    """

    def __init__(
        self,
        agents: Optional[Dict[str, AgentDefinition]] = None,
        errors: Optional[List[str]] = None,
    ) -> None:
        self.agents: Dict[str, AgentDefinition] = agents or {}
        self.errors: List[str] = errors or []

    @classmethod
    def load(cls, agents_dir: Path) -> "AgentRegistry":
        """Scan a directory for agent definitions.

        Every ``*.yaml`` and ``*.yml`` file is loaded. A malformed file is
        recorded in ``errors`` and skipped, so one bad agent does not take the
        whole crew down.

        Args:
            agents_dir: The directory to scan, recursively.

        Returns:
            The registry.
        """
        agents: Dict[str, AgentDefinition] = {}
        errors: List[str] = []
        root = Path(agents_dir).expanduser()

        if not root.exists():
            log.warning("agent directory %s does not exist", root)
            return cls(agents, errors)

        for path in sorted(root.rglob("*.y*ml")):
            try:
                definition = load_definition(path)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                errors.append(f"{path}: {exc}")
                log.warning("skipping agent %s: %s", path, exc)
                continue
            if definition.name in agents:
                errors.append(
                    f"{path}: duplicate agent name {definition.name!r} "
                    f"(already defined by {agents[definition.name].source_path})"
                )
                log.warning("duplicate agent name %s", definition.name)
                continue
            agents[definition.name] = definition

        log.info("loaded %d agent(s) from %s", len(agents), root)
        return cls(agents, errors)

    def get(self, name: str) -> Optional[AgentDefinition]:
        """Return one agent by name.

        Args:
            name: The agent name.

        Returns:
            The definition, or None when it does not exist.
        """
        return self.agents.get(name)

    def all(self) -> List[AgentDefinition]:
        """Return every agent, sorted by name."""
        return [self.agents[name] for name in sorted(self.agents)]

    def names(self) -> List[str]:
        """Return every agent name, sorted."""
        return sorted(self.agents)

    def by_capability(self, capability: str) -> List[AgentDefinition]:
        """Return the agents that declare a capability.

        Args:
            capability: The capability to match, case-insensitively.

        Returns:
            The matching agents.
        """
        wanted = capability.strip().lower()
        return [
            agent
            for agent in self.all()
            if any(cap.lower() == wanted for cap in agent.capabilities)
        ]

    def catalogue(self) -> List[Dict[str, object]]:
        """Return a compact description of every agent, for the router prompt.

        Returns:
            One mapping per agent with its name, role and description.
        """
        return [
            {
                "name": agent.name,
                "role": agent.role,
                "description": agent.description,
                "capabilities": agent.capabilities,
            }
            for agent in self.all()
        ]

    def add(self, definition: AgentDefinition) -> None:
        """Register an agent built in code rather than loaded from disk.

        Args:
            definition: The definition to add.
        """
        self.agents[definition.name] = definition

    def extend(self, definitions: Iterable[AgentDefinition]) -> None:
        """Register several agents built in code.

        Args:
            definitions: The definitions to add.
        """
        for definition in definitions:
            self.add(definition)

    def __len__(self) -> int:
        """Return the number of registered agents."""
        return len(self.agents)

    def __contains__(self, name: object) -> bool:
        """Report whether an agent is registered."""
        return isinstance(name, str) and name in self.agents
