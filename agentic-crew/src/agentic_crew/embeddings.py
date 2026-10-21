"""Embedding backends for the crew's vector stores.

The crew never talks to a language model — Conduit does that — but it does
need vectors. Two backends are supported:

* ``fastembed`` (the default) runs a small ONNX model locally. No torch, no
  API key, no data leaving the machine. The model is downloaded on first use.
* ``openai`` talks to any OpenAI-compatible embedding endpoint, which is handy
  when a gateway already exposes one.

Both are wrapped in a LangChain :class:`~langchain_core.embeddings.Embeddings`
so nothing else in the package has to care which one is in use. Building an
embedding model is expensive, so instances are cached for the life of the
process.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.embeddings import Embeddings

from .config import Settings

__all__ = [
    "EmbeddingError",
    "FastEmbedEmbeddings",
    "build_embeddings",
]

log = logging.getLogger(__name__)

#: The fastembed model used when the settings do not name one.
DEFAULT_FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"

#: Backend identifiers understood by :func:`build_embeddings`.
FASTEMBED_BACKEND = "fastembed"
OPENAI_BACKEND = "openai"

# One embedding model per (backend, model, endpoint, size) for the whole
# process: loading an ONNX model or a tokenizer is not cheap.
_CACHE: Dict[Tuple[str, str, str, int], Embeddings] = {}
_CACHE_LOCK = threading.Lock()


class EmbeddingError(RuntimeError):
    """Raised when the configured embedding backend cannot produce vectors.

    The message is meant to be actionable: it names the model and the reason
    (missing package, failed download, unknown backend) so the caller can
    report it instead of crashing.
    """


class FastEmbedEmbeddings(Embeddings):
    """Local embeddings computed by fastembed's ONNX models.

    The model is loaded lazily, on the first call to
    :meth:`embed_documents` or :meth:`embed_query`. fastembed downloads the
    ONNX weights the first time it sees a model, so loading eagerly would make
    importing this module slow and network-dependent.

    Attributes:
        model_name: The fastembed model id, for example
            ``BAAI/bge-small-en-v1.5``.
        cache_dir: Directory the downloaded model files are kept in.
        dimensions: Dimensionality of the produced vectors. Kept for
            documentation and for the vector store to agree on; the model
            itself decides the real size.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_FASTEMBED_MODEL,
        cache_dir: Optional[str] = None,
        dimensions: int = 384,
    ) -> None:
        """Create the wrapper without loading anything yet.

        Args:
            model_name: The fastembed model id.
            cache_dir: Directory for the downloaded model files.
            dimensions: Dimensionality of the produced vectors.
        """
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.dimensions = dimensions
        self._model: Any = None

    def _load(self) -> Any:
        """Load the underlying fastembed model, downloading it if needed.

        Returns:
            A ``fastembed.TextEmbedding`` instance.

        Raises:
            EmbeddingError: If fastembed is not installed, the model name is
                unknown, or the model could not be downloaded.
        """
        if self._model is not None:
            return self._model

        try:
            from fastembed import TextEmbedding
        except Exception as exc:  # noqa: BLE001 - any import failure is fatal here
            raise EmbeddingError(
                f"the fastembed embedding backend is unavailable ({exc}). "
                f"Install 'fastembed' to use the model {self.model_name!r}, or "
                "switch AGENTIC_CREW_EMBEDDING_BACKEND to 'openai'."
            ) from exc

        options: Dict[str, Any] = {"model_name": self.model_name}
        if self.cache_dir:
            options["cache_dir"] = self.cache_dir

        try:
            self._model = TextEmbedding(**options)
        except Exception as exc:  # noqa: BLE001 - reported, never raised raw
            raise EmbeddingError(
                f"could not load the fastembed model {self.model_name!r}: {exc}. "
                "The model is downloaded on first use, so this usually means the "
                "network is unavailable or the model name is wrong."
            ) from exc

        log.info("loaded fastembed model %s", self.model_name)
        return self._model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of documents.

        Args:
            texts: The documents to embed.

        Returns:
            One vector per document.

        Raises:
            EmbeddingError: If the model could not be loaded.
        """
        items = [str(text) for text in texts]
        if not items:
            return []

        model = self._load()
        passage_embed = getattr(model, "passage_embed", None)
        try:
            if callable(passage_embed):
                vectors = passage_embed(items)
            else:
                vectors = model.embed(items)
            return [_as_list(vector) for vector in vectors]
        except Exception as exc:  # noqa: BLE001 - reported, never raised raw
            raise EmbeddingError(
                f"the fastembed model {self.model_name!r} failed to embed a "
                f"batch of {len(items)} document(s): {exc}"
            ) from exc

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query.

        Args:
            text: The query text.

        Returns:
            The query vector.

        Raises:
            EmbeddingError: If the model could not be loaded.
        """
        model = self._load()
        query_embed = getattr(model, "query_embed", None)
        try:
            if callable(query_embed):
                vectors = list(query_embed(str(text)))
            else:
                vectors = list(model.embed([str(text)]))
        except Exception as exc:  # noqa: BLE001 - reported, never raised raw
            raise EmbeddingError(
                f"the fastembed model {self.model_name!r} failed to embed the "
                f"query: {exc}"
            ) from exc

        if not vectors:
            raise EmbeddingError(
                f"the fastembed model {self.model_name!r} returned no vector for "
                "the query"
            )
        return _as_list(vectors[0])


def _as_list(vector: Any) -> List[float]:
    """Convert an embedding into a plain list of floats.

    Args:
        vector: A numpy array or any sequence of numbers.

    Returns:
        The vector as a list of floats.
    """
    tolist = getattr(vector, "tolist", None)
    if callable(tolist):
        return [float(value) for value in tolist()]
    return [float(value) for value in vector]


def _build_fastembed(settings: Settings) -> Embeddings:
    """Build the local fastembed backend.

    Args:
        settings: The runtime settings.

    Returns:
        A :class:`FastEmbedEmbeddings` instance.
    """
    return FastEmbedEmbeddings(
        model_name=settings.embedding_model or DEFAULT_FASTEMBED_MODEL,
        cache_dir=None,
        dimensions=settings.embedding_dimensions,
    )


def _build_openai(settings: Settings) -> Embeddings:
    """Build the OpenAI-compatible embedding backend.

    Args:
        settings: The runtime settings.

    Returns:
        An ``OpenAIEmbeddings`` instance pointed at the configured endpoint.

    Raises:
        EmbeddingError: If ``langchain-openai`` is not installed.
    """
    try:
        from langchain_openai import OpenAIEmbeddings
    except Exception as exc:  # noqa: BLE001 - any import failure is fatal here
        raise EmbeddingError(
            f"the 'openai' embedding backend needs the 'langchain-openai' "
            f"package ({exc}). Install it, or switch "
            "AGENTIC_CREW_EMBEDDING_BACKEND to 'fastembed'."
        ) from exc

    options: Dict[str, Any] = {
        "model": settings.embedding_model,
        # A local server usually ignores the key, but the client insists on
        # having one.
        "api_key": settings.embedding_api_key or "not-needed",
        "check_embedding_ctx_length": False,
    }
    if settings.embedding_base_url:
        options["base_url"] = settings.embedding_base_url
    if settings.embedding_dimensions > 0:
        options["dimensions"] = settings.embedding_dimensions

    try:
        return OpenAIEmbeddings(**options)
    except Exception as exc:  # noqa: BLE001 - reported, never raised raw
        raise EmbeddingError(
            f"could not build the OpenAI embedding backend for model "
            f"{settings.embedding_model!r}: {exc}"
        ) from exc


def build_embeddings(settings: Settings) -> Embeddings:
    """Build the embedding model described by the settings.

    The model is cached per process: two agents that share a backend share a
    single loaded model.

    Args:
        settings: The runtime settings.

    Returns:
        A LangChain ``Embeddings`` implementation.

    Raises:
        EmbeddingError: If the backend is unknown or cannot be built.
    """
    backend = (settings.embedding_backend or FASTEMBED_BACKEND).strip().lower()
    model = (settings.embedding_model or "").strip()
    base_url = (settings.embedding_base_url or "").strip()
    dimensions = int(settings.embedding_dimensions or 0)

    key = (backend, model, base_url, dimensions)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

    if backend == FASTEMBED_BACKEND:
        embeddings = _build_fastembed(settings)
    elif backend == OPENAI_BACKEND:
        embeddings = _build_openai(settings)
    else:
        raise EmbeddingError(
            f"unknown embedding backend {settings.embedding_backend!r}; "
            f"expected {FASTEMBED_BACKEND!r} or {OPENAI_BACKEND!r}"
        )

    with _CACHE_LOCK:
        # Another thread may have won the race; keep whichever landed first.
        return _CACHE.setdefault(key, embeddings)
