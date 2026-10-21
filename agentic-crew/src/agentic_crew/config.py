"""Configuration for the crew, read from the environment and an optional .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

__all__ = ["Settings", "load_settings"]


def _env(key: str, default: str = "") -> str:
    """Read an environment variable, falling back to a default.

    Args:
        key: Variable name.
        default: Value used when the variable is unset or empty.

    Returns:
        The variable value.
    """
    value = os.environ.get(key)
    return value if value else default


def _env_int(key: str, default: int) -> int:
    """Read an integer environment variable.

    Args:
        key: Variable name.
        default: Value used when unset or unparsable.

    Returns:
        The parsed value.
    """
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    """Read a boolean environment variable.

    Args:
        key: Variable name.
        default: Value used when unset or unparsable.

    Returns:
        The parsed value.
    """
    raw = _env(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Runtime configuration for the crew.

    Attributes:
        conduit_base_url: Base URL of the Conduit gateway.
        conduit_api_key: Gateway token used for LLM calls.
        agents_dir: Directory scanned for agent YAML files.
        data_dir: Where the vector store and ingested documents live.
        default_model: Model id used when an agent does not name one.
        embedding_model: Embedding model for the vector store.
        embedding_backend: Either ``fastembed`` or ``openai``.
        embedding_base_url: Base URL for an OpenAI-compatible embedding API.
        embedding_api_key: API key for that endpoint.
        embedding_dimensions: Dimensionality of the embeddings.
        host: Bind address for the HTTP service.
        port: Port for the HTTP service.
        extra_skill_paths: Additional skill directories appended to every agent.
    """

    conduit_base_url: str = "http://localhost:9812"
    conduit_api_key: str = ""
    agents_dir: Path = field(default_factory=lambda: Path("agents"))
    data_dir: Path = field(default_factory=lambda: Path("data"))
    default_model: str = "glm-5.3"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_backend: str = "fastembed"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_dimensions: int = 384
    host: str = "127.0.0.1"
    port: int = 8787
    extra_skill_paths: List[Path] = field(default_factory=list)

    @property
    def store_path(self) -> Path:
        """Return the vector store directory."""
        return self.data_dir / "vectors"

    @property
    def resolved_agents_dir(self) -> Path:
        """Return the agent directory, expanded."""
        return Path(os.path.expanduser(str(self.agents_dir))).resolve()


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a .env file without overriding the environment."""
    path = Path(".env")
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_settings(overrides: Optional[Settings] = None) -> Settings:
    """Build the settings from the environment.

    Args:
        overrides: Settings to use instead of the environment.

    Returns:
        The resolved settings.
    """
    if overrides is not None:
        return overrides

    _load_dotenv()

    extra = [
        Path(os.path.expanduser(p))
        for p in _env("AGENTIC_CREW_EXTRA_SKILLS", "").split(os.pathsep)
        if p.strip()
    ]

    return Settings(
        conduit_base_url=_env("CONDUIT_BASE_URL", "http://localhost:9812").rstrip("/"),
        conduit_api_key=_env("CONDUIT_API_KEY", ""),
        agents_dir=Path(os.path.expanduser(_env("AGENTIC_CREW_AGENTS_DIR", "agents"))),
        data_dir=Path(os.path.expanduser(_env("AGENTIC_CREW_DATA_DIR", "data"))),
        default_model=_env("AGENTIC_CREW_DEFAULT_MODEL", "glm-5.3"),
        embedding_model=_env("AGENTIC_CREW_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        embedding_backend=_env("AGENTIC_CREW_EMBEDDING_BACKEND", "fastembed"),
        embedding_base_url=_env("AGENTIC_CREW_EMBEDDING_BASE_URL", ""),
        embedding_api_key=_env("AGENTIC_CREW_EMBEDDING_API_KEY", ""),
        embedding_dimensions=_env_int("AGENTIC_CREW_EMBEDDING_DIMENSIONS", 384),
        host=_env("AGENTIC_CREW_HOST", "127.0.0.1"),
        port=_env_int("AGENTIC_CREW_PORT", 8787),
        extra_skill_paths=extra,
    )
