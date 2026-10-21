"""The vector store behind every agent's knowledge base.

Qdrant runs in *embedded* mode: the store is a directory on disk, there is no
server to install or run, and the whole thing works offline. Each agent gets
its own collection inside that directory, so agents never see each other's
documents.

The client is a singleton per store path. Qdrant's local mode takes an
exclusive lock on the storage directory, so a second client for the same path
— in the same process — is an error. Sharing the client keeps one directory
usable by many agents at once.
"""

from __future__ import annotations

import atexit
import logging
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models

from .config import Settings
from .embeddings import build_embeddings

__all__ = ["VectorStore"]

log = logging.getLogger(__name__)

# One Qdrant client per storage directory, shared by every VectorStore that
# points at it. Qdrant's embedded mode locks the directory exclusively.
_CLIENTS: Dict[str, Tuple[QdrantClient, int]] = {}
_CLIENT_LOCK = threading.Lock()

#: How many points are written per Qdrant request.
DEFAULT_BATCH_SIZE = 64


def _point_id(raw: str) -> str:
    """Return a point id Qdrant accepts.

    Qdrant only takes integer or UUID point ids, so a plain string is hashed
    into a UUID. The mapping is stable, so writing the same id again updates
    the same point instead of creating a second one.

    Args:
        raw: The id the caller supplied.

    Returns:
        The point id to write.

    Raises:
        ValueError: If the id is empty.
    """
    text = str(raw).strip()
    if not text:
        raise ValueError("point ids must not be empty")
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, text))


def _client_for(path: Path) -> QdrantClient:
    """Return the shared Qdrant client for a storage directory.

    Args:
        path: The storage directory.

    Returns:
        A ``QdrantClient`` in embedded mode, shared with every other caller
        that asked for the same directory.
    """
    key = str(path)
    with _CLIENT_LOCK:
        entry = _CLIENTS.get(key)
        if entry is None:
            client = QdrantClient(path=key)
            _CLIENTS[key] = (client, 1)
            log.info("opened vector store at %s", key)
            return client
        client, users = entry
        _CLIENTS[key] = (client, users + 1)
        return client


@atexit.register
def _close_all_clients() -> None:
    """Close every shared client while the interpreter is still alive.

    Qdrant's client releases its file lock in ``close()``, and that call
    imports a module — which fails once the interpreter starts tearing down.
    Closing here keeps the shutdown quiet.
    """
    with _CLIENT_LOCK:
        clients = [client for client, _ in _CLIENTS.values()]
        _CLIENTS.clear()
    for client in clients:
        try:
            client.close()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            pass


class VectorStore:
    """A local Qdrant vector store, shared by one or more agents.

    Attributes:
        settings: The runtime settings the store was built from.
        embeddings: The embedding model used for every collection.
    """

    def __init__(
        self,
        settings: Settings,
        embeddings: Optional[Embeddings] = None,
    ) -> None:
        """Open the store and prepare its client.

        Args:
            settings: The runtime settings. ``settings.store_path`` is the
                directory the store lives in, and
                ``settings.embedding_dimensions`` is the width of every
                vector.
            embeddings: The embedding model to use. Defaults to
                ``build_embeddings(settings)``.
        """
        self.settings = settings
        self.embeddings = (
            embeddings if embeddings is not None else build_embeddings(settings)
        )
        self.dimensions = max(1, int(settings.embedding_dimensions or 0) or 1)

        self.path = Path(settings.store_path).expanduser()
        self.path.mkdir(parents=True, exist_ok=True)

        self._client = _client_for(self.path)
        self._collections: Dict[str, QdrantVectorStore] = {}

    # -- collections ----------------------------------------------------

    def collection(self, name: str) -> QdrantVectorStore:
        """Return the named collection, creating it when it does not exist.

        The collection is created with a fixed vector size — taken from
        ``settings.embedding_dimensions`` — and cosine distance, so every
        agent's vectors are comparable and no server round trip is needed.

        Args:
            name: The collection name.

        Returns:
            The LangChain vector store for that collection.

        Raises:
            ValueError: If the collection name is empty.
        """
        key = str(name).strip()
        if not key:
            raise ValueError("collection name must not be empty")

        # A no-op when the collection is already there, and it repairs the case
        # where another store dropped it in the meantime.
        self._create_collection(key)

        cached = self._collections.get(key)
        if cached is not None:
            return cached

        store = QdrantVectorStore(
            client=self._client,
            collection_name=key,
            embedding=self.embeddings,
            distance=models.Distance.COSINE,
        )
        self._collections[key] = store
        return store

    def has(self, collection: str) -> bool:
        """Report whether a collection exists.

        Args:
            collection: The collection name.

        Returns:
            True when the collection exists in the store.
        """
        key = str(collection).strip()
        if not key:
            return False
        return bool(self._client.collection_exists(key))

    def count(self, collection: str) -> int:
        """Return the number of points in a collection.

        Args:
            collection: The collection name.

        Returns:
            The number of stored vectors; 0 when the collection is missing.
        """
        key = str(collection).strip()
        if not key or not self.has(key):
            return 0
        return int(self._client.count(collection_name=key, exact=True).count)

    def delete(self, collection: str) -> None:
        """Clear a collection.

        The collection is dropped, so the next :meth:`collection` call creates
        it again — with the configured vector size and distance. Dropping it
        rather than deleting its points also clears out a collection left over
        from a different embedding size.

        Args:
            collection: The collection name.
        """
        key = str(collection).strip()
        if not key:
            return
        if self.has(key):
            self._client.delete_collection(key)
            log.info("deleted vector store collection %r", key)
        self._collections.pop(key, None)

    # -- writing and reading --------------------------------------------

    def upsert(
        self,
        collection: str,
        docs: List[Document],
        ids: List[str],
    ) -> None:
        """Write documents into a collection, replacing points with the same id.

        Args:
            collection: The collection name.
            docs: The documents to store. Each document's metadata is kept
                alongside its text.
            ids: The point ids, one per document, in the same order.

        Raises:
            ValueError: If the number of ids does not match the number of
                documents.
        """
        if not docs:
            return
        if len(ids) != len(docs):
            raise ValueError(
                f"expected one id per document, got {len(ids)} ids for "
                f"{len(docs)} documents"
            )

        store = self.collection(collection)
        texts = [doc.page_content for doc in docs]
        metadatas = [dict(doc.metadata or {}) for doc in docs]
        store.add_texts(
            texts,
            metadatas,
            [_point_id(point_id) for point_id in ids],
            batch_size=DEFAULT_BATCH_SIZE,
        )

    def search(
        self,
        collection: str,
        query: str,
        top_k: int,
    ) -> List[Tuple[Document, float]]:
        """Search a collection for the passages closest to a query.

        Args:
            collection: The collection name.
            query: The query text.
            top_k: How many passages to return.

        Returns:
            ``(document, score)`` pairs, best first. The score is the cosine
            similarity, so higher is better. A collection that does not exist
            yields an empty list.
        """
        limit = int(top_k)
        if limit <= 0:
            return []
        text = (query or "").strip()
        if not text:
            return []
        if not self.has(collection):
            return []

        store = self.collection(collection)
        results = store.similarity_search_with_score(text, k=limit)
        return [(doc, float(score)) for doc, score in results]

    # -- internals ------------------------------------------------------

    def _create_collection(self, name: str) -> None:
        """Create a collection when it is missing.

        Args:
            name: The collection name.
        """
        if self._client.collection_exists(name):
            return
        self._client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=self.dimensions,
                distance=models.Distance.COSINE,
            ),
        )
        log.info(
            "created vector store collection %r (%d dimensions, cosine) in %s",
            name,
            self.dimensions,
            self.path,
        )

    def close(self) -> None:
        """Release this store's client.

        The client is shared with other stores pointing at the same directory,
        so it is only closed once the last user is done with it.
        """
        key = str(self.path)
        with _CLIENT_LOCK:
            entry = _CLIENTS.get(key)
            if entry is None:
                return
            client, users = entry
            if users > 1:
                _CLIENTS[key] = (client, users - 1)
                return
            _CLIENTS.pop(key, None)
        try:
            client.close()
        except Exception as exc:  # noqa: BLE001 - closing must not raise
            log.debug("could not close the Qdrant client for %s: %s", self.path, exc)
