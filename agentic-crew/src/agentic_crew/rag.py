"""Agent knowledge bases.

Every agent owns a private collection inside the shared vector store. An
agent's knowledge base is built from the folders named in its definition:
Markdown and text files, HTML pages and PDF lecture notes are read, split
into overlapping chunks, embedded and written to the collection.

Ingestion is incremental and idempotent. Chunk ids are a stable hash of the
agent, the file and the chunk index, so re-running an ingest updates the
existing points instead of piling up duplicates. A collection that already
holds chunks is left alone unless a forced ingest is asked for.

The retrieval side is deliberately small: search returns the best passages,
and context formats them for a prompt. Nothing here talks to a model.
"""

from __future__ import annotations

import html as html_module
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import Settings
from .embeddings import EmbeddingError, build_embeddings
from .models import AgentDefinition, RagSource
from .store import VectorStore

__all__ = [
    "AgentKnowledgeBase",
    "IngestReport",
    "RetrievedChunk",
]

log = logging.getLogger(__name__)

#: How many characters of retrieved context are handed to a prompt.
MAX_CONTEXT_CHARS = 12000

#: Appended to the context when it did not fit in :data:`MAX_CONTEXT_CHARS`.
CONTEXT_TRUNCATION_MARKER = "\n\n[... context truncated ...]"

#: How many chunks are written to the vector store in one go.
INGEST_BATCH_SIZE = 64

#: File types that are read as plain text.
TEXT_EXTENSIONS: Set[str] = {".md", ".markdown", ".txt", ".text", ".rst"}

#: File types that are read as HTML.
HTML_EXTENSIONS: Set[str] = {".html", ".htm"}

#: File types that are read as PDF.
PDF_EXTENSIONS: Set[str] = {".pdf"}


class _IngestAbort(Exception):
    """Raised internally when an ingest cannot continue.

    A failure to embed or to write is fatal for the whole run: every batch
    would fail the same way, so the ingest stops and the reason is recorded
    in the report.
    """


@dataclass
class RetrievedChunk:
    """One passage retrieved from an agent's knowledge base.

    Attributes:
        text: The passage text.
        source: Where the passage came from — a file path, with the page
            number appended for PDF passages.
        score: The similarity score. Higher means a closer match.
    """

    text: str
    source: str
    score: float


@dataclass
class IngestReport:
    """What happened during an ingestion run.

    Attributes:
        collection: The collection that was written to.
        documents: Number of files read and turned into chunks.
        chunks: Number of chunks written. When the ingest was skipped because
            the collection was already populated, this is the number of chunks
            already there.
        skipped: Number of things that were not ingested — a source path that
            does not exist, a file without any text, or the ingest itself when
            the collection was already populated.
        errors: One message per problem. The run continues after a problem.
    """

    collection: str
    documents: int = 0
    chunks: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)


def chunk_id(agent: str, source: str, index: int) -> str:
    """Return the stable id of a chunk.

    The id is a hash of the agent name, the source file and the chunk index,
    so the same chunk always lands on the same point in the store. The hash is
    rendered as a UUID because Qdrant only accepts UUID or integer point ids.

    Args:
        agent: The agent name.
        source: The source file path.
        index: The index of the chunk inside the file.

    Returns:
        The chunk id.
    """
    key = f"{agent}\x00{source}\x00{index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _normalise_extension(extension: str) -> str:
    """Return an extension in the ``.lower`` form used for comparison.

    Args:
        extension: The extension, with or without a leading dot.

    Returns:
        The normalised extension.
    """
    text = str(extension).strip().lower()
    if not text:
        return ""
    if not text.startswith("."):
        text = f".{text}"
    return text


def _source_root(source: RagSource) -> Path:
    """Return the absolute path a source points at.

    The path keeps the spelling the definition used — ``~`` is expanded and a
    relative path is anchored at the working directory, but symlinks are left
    alone, so a cited source looks the way the definition wrote it.

    Args:
        source: The source to resolve.

    Returns:
        The absolute path of the source.
    """
    return Path(os.path.abspath(os.path.expanduser(str(source.path))))


def _read_text(path: Path) -> str:
    """Read a text file.

    Args:
        path: The file to read.

    Returns:
        The file contents.
    """
    return path.read_text(encoding="utf-8", errors="replace")


def _strip_html(raw: str) -> str:
    """Turn an HTML document into readable plain text.

    The conversion is deliberately crude: scripts and styles are dropped, the
    remaining tags become whitespace, and entities are decoded. Layout is not
    preserved, but the words are — which is what retrieval cares about.

    Args:
        raw: The HTML source.

    Returns:
        The plain text.
    """
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", " ", raw)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


class AgentKnowledgeBase:
    """The searchable knowledge base of a single agent.

    Attributes:
        settings: The runtime settings.
        definition: The agent definition that owns this knowledge base.
        embeddings: The embedding model used for indexing and querying.
        store: The vector store the collections live in.
        collection_name: The name of this agent's collection.
    """

    def __init__(
        self,
        settings: Settings,
        definition: AgentDefinition,
        embeddings: Optional[Embeddings] = None,
    ) -> None:
        """Prepare the knowledge base.

        Nothing is read from disk here: the collection is created the first
        time the agent is ingested into or searched.

        Args:
            settings: The runtime settings.
            definition: The agent definition. Its ``rag`` section names the
                sources, the chunking and the collection.
            embeddings: The embedding model to use. Defaults to
                ``build_embeddings(settings)``.
        """
        self.settings = settings
        self.definition = definition
        self.embeddings = (
            embeddings if embeddings is not None else build_embeddings(settings)
        )
        self.store = VectorStore(settings, embeddings=self.embeddings)
        self.collection_name = definition.rag.resolved_collection(definition.name)

    # -- ingestion ------------------------------------------------------

    def ingest(self, force: bool = False) -> IngestReport:
        """Read the agent's sources and index them.

        The run is idempotent: a collection that already holds chunks is left
        untouched and the report says how many chunks are there. A forced
        ingest clears the collection first, so the agent's knowledge base is
        rebuilt from scratch.

        Args:
            force: Clear the collection before ingesting.

        Returns:
            A report of what was read, written, skipped and what went wrong.
            Problems are collected in the report rather than raised, so one
            unreadable file does not take the whole run down.
        """
        collection = self.collection_name
        report = IngestReport(collection=collection)

        if force:
            self.store.delete(collection)
        else:
            existing = self.store.count(collection)
            if existing > 0:
                log.info(
                    "skipping ingestion of collection %r: already populated "
                    "with %d chunk(s)",
                    collection,
                    existing,
                )
                report.chunks = existing
                report.skipped = 1
                return report

        # Create the collection up front so its vector size is fixed before
        # the first write.
        try:
            self.store.collection(collection)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            report.errors.append(
                f"{collection}: could not prepare the collection: {exc}"
            )
            report.skipped += 1
            log.error("could not prepare the collection %s: %s", collection, exc)
            return report

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.definition.rag.chunk_size,
            chunk_overlap=self.definition.rag.chunk_overlap,
        )

        pending: List[Tuple[Document, str]] = []

        for source in self.definition.rag.sources:
            path = _source_root(source)
            if not path.exists():
                message = f"{path}: source path does not exist"
                report.errors.append(message)
                report.skipped += 1
                log.warning("%s", message)
                continue

            try:
                files = self._select_files(source)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                report.errors.append(f"{path}: {exc}")
                report.skipped += 1
                log.warning("could not list the rag source %s: %s", path, exc)
                continue

            if not files:
                log.warning("the rag source %s matched no files", path)
                continue

            for file in files:
                wrote = False
                index = 0
                try:
                    # Read lazily: a PDF yields one page at a time, so a large
                    # lecture note is never held in memory all at once.
                    for text, page in self._read_document(file):
                        for piece in splitter.split_text(text):
                            if not piece.strip():
                                continue
                            content = (
                                f"[page {page}] {piece}" if page is not None else piece
                            )
                            metadata: Dict[str, object] = {
                                "source": str(file),
                                "agent": self.definition.name,
                            }
                            if page is not None:
                                metadata["page"] = page
                            point_id = chunk_id(
                                self.definition.name, str(file), index
                            )
                            index += 1
                            pending.append(
                                (
                                    Document(page_content=content, metadata=metadata),
                                    point_id,
                                )
                            )
                            wrote = True

                            if len(pending) >= INGEST_BATCH_SIZE:
                                self._write_batch(collection, pending, report)
                except _IngestAbort:
                    return report
                except Exception as exc:  # noqa: BLE001 - reported, not raised
                    report.errors.append(f"{file}: {exc}")
                    report.skipped += 1
                    log.warning("could not read %s: %s", file, exc)
                    continue

                if wrote:
                    report.documents += 1
                else:
                    report.skipped += 1
                    log.info("no text found in %s", file)

        try:
            self._write_batch(collection, pending, report)
        except _IngestAbort:
            pass

        report.chunks = self.store.count(collection)
        log.info(
            "ingested %d document(s) into collection %r as %d chunk(s)",
            report.documents,
            collection,
            report.chunks,
        )
        return report

    # -- retrieval ------------------------------------------------------

    def search(self, query: str, top_k: Optional[int] = None) -> List[RetrievedChunk]:
        """Find the passages closest to a question.

        Args:
            query: The question or search text.
            top_k: How many passages to return. Defaults to the agent's
                configured ``rag.top_k``.

        Returns:
            The best passages, closest first. An agent whose knowledge base
            has never been ingested into returns an empty list.
        """
        limit = int(top_k) if top_k is not None else int(self.definition.rag.top_k)
        if limit <= 0:
            return []

        results = self.store.search(self.collection_name, query, limit)
        chunks: List[RetrievedChunk] = []
        for document, score in results:
            metadata = document.metadata or {}
            source = str(metadata.get("source") or "")
            page = metadata.get("page")
            if page is not None:
                source = f"{source} (page {page})"
            chunks.append(
                RetrievedChunk(
                    text=document.page_content,
                    source=source,
                    score=float(score),
                )
            )
        return chunks

    def context(self, query: str, top_k: Optional[int] = None) -> str:
        """Format the passages matching a question for a prompt.

        Each passage becomes a ``### <source>`` block, so the agent can see
        which file a passage came from — and, for PDF passages, which page.

        Args:
            query: The question or search text.
            top_k: How many passages to use. Defaults to the agent's
                configured ``rag.top_k``.

        Returns:
            The formatted context, empty when nothing was retrieved. The text
            is capped at :data:`MAX_CONTEXT_CHARS` and marked when it was cut.
        """
        chunks = self.search(query, top_k)
        if not chunks:
            return ""

        blocks: List[str] = []
        used = 0
        truncated = False

        for chunk in chunks:
            block = f"### {chunk.source}\n{chunk.text}".strip()
            extra = len(block) + (2 if blocks else 0)
            if used + extra > MAX_CONTEXT_CHARS:
                if not blocks:
                    # A single passage larger than the whole budget: keep the
                    # part that fits rather than dropping it entirely.
                    block = block[:MAX_CONTEXT_CHARS]
                    blocks.append(block)
                truncated = True
                break
            blocks.append(block)
            used += extra

        text = "\n\n".join(blocks)
        if truncated:
            text += CONTEXT_TRUNCATION_MARKER
        return text

    # -- internals ------------------------------------------------------

    def _write_batch(
        self,
        collection: str,
        pending: List[Tuple[Document, str]],
        report: IngestReport,
    ) -> None:
        """Write the buffered chunks to the store.

        Args:
            collection: The collection to write to.
            pending: The buffered chunks and their ids.
            report: The report the failure is recorded in.

        Raises:
            _IngestAbort: If the chunks could not be written.
        """
        if not pending:
            return
        docs = [doc for doc, _ in pending]
        ids = [point_id for _, point_id in pending]
        try:
            self.store.upsert(collection, docs, ids)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            report.errors.append(f"{collection}: {self._describe_failure(exc)}")
            report.skipped += 1
            log.error("ingestion of %s stopped: %s", collection, exc)
            raise _IngestAbort from exc
        pending.clear()

    @staticmethod
    def _describe_failure(exc: Exception) -> str:
        """Describe why a batch of chunks could not be indexed.

        Args:
            exc: The failure.

        Returns:
            A message for the ingest report.
        """
        if isinstance(exc, EmbeddingError):
            return f"could not embed the chunks: {exc}"
        return f"could not index the chunks: {exc}"
    def _select_files(self, source: RagSource) -> List[Path]:
        """Return the files a source contributes.

        A source that points at a file contributes that file. A source that
        points at a directory contributes the files whose extension is in
        ``source.extensions`` and, when ``source.include`` is set, that match
        the glob relative to the directory.

        Args:
            source: The source to expand.

        Returns:
            The matching files, sorted by path.
        """
        path = _source_root(source)

        if path.is_file():
            return [path]

        if not path.is_dir():
            raise FileNotFoundError(f"source path does not exist: {path}")

        if source.include:
            candidates = [item for item in path.glob(source.include) if item.is_file()]
        else:
            candidates = [item for item in path.rglob("*") if item.is_file()]

        extensions = {
            _normalise_extension(extension) for extension in source.extensions
        }
        if extensions:
            candidates = [
                item for item in candidates if item.suffix.lower() in extensions
            ]

        return sorted(candidates)

    def _read_document(self, path: Path) -> Iterator[Tuple[str, Optional[int]]]:
        """Read a document as ``(text, page)`` segments.

        A PDF is read one page at a time so a large lecture note never has to
        be held in memory all at once. Every other supported file yields a
        single segment.

        Args:
            path: The file to read.

        Yields:
            The text of the document and the page it came from, if any.

        Raises:
            ValueError: If the file type is not supported.
        """
        suffix = path.suffix.lower()

        if suffix in PDF_EXTENSIONS:
            yield from self._read_pdf(path)
            return

        if suffix in HTML_EXTENSIONS:
            yield _strip_html(_read_text(path)), None
            return

        if suffix in TEXT_EXTENSIONS:
            yield _read_text(path), None
            return

        raise ValueError(f"unsupported file type: {path.name}")

    @staticmethod
    def _read_pdf(path: Path) -> Iterator[Tuple[str, Optional[int]]]:
        """Read a PDF one page at a time.

        Args:
            path: The PDF to read.

        Yields:
            The text of each page and its page number.

        Raises:
            ImportError: If pypdf is not installed.
        """
        try:
            from pypdf import PdfReader
        except Exception as exc:  # noqa: BLE001 - any import failure is fatal here
            raise ImportError(
                f"reading PDF files needs the 'pypdf' package ({exc})"
            ) from exc

        reader = PdfReader(str(path))
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001 - a bad page is not fatal
                log.warning("could not read page %d of %s: %s", number, path, exc)
                text = ""
            text = text.strip()
            if not text:
                continue
            yield text, number
