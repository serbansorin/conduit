"""Tests for the RAG subsystem: embeddings, vector store and knowledge bases.

Nothing here touches the network. The embedding model is swapped for a
deterministic hashing embedder, which is enough to prove that a query finds
the document it should — and it keeps the suite fast and offline.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from agentic_crew.config import Settings
from agentic_crew.embeddings import EmbeddingError, build_embeddings
from agentic_crew.models import AgentDefinition, RagConfig, RagSource
from agentic_crew.rag import (
    CONTEXT_TRUNCATION_MARKER,
    MAX_CONTEXT_CHARS,
    AgentKnowledgeBase,
    IngestReport,
    RetrievedChunk,
    chunk_id,
)
from agentic_crew.store import VectorStore

#: The width of the vectors used throughout the suite.
DIMENSIONS = 256


class HashingEmbeddings(Embeddings):
    """A deterministic, offline embedder.

    Every token is hashed into one bucket of a fixed width vector — the
    hashing trick — and the vector is normalised. Texts that share words end
    up with similar vectors, so a query finds the document it talks about
    without any model, download or network call.

    Attributes:
        dimensions: The width of the produced vectors.
    """

    def __init__(self, dimensions: int = DIMENSIONS) -> None:
        """Create the embedder.

        Args:
            dimensions: The width of the produced vectors.
        """
        self.dimensions = dimensions

    def _vector(self, text: str) -> List[float]:
        """Return the vector of a text.

        Args:
            text: The text to embed.

        Returns:
            A normalised vector.
        """
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[a-z0-9]+", str(text).lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[bucket] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of documents.

        Args:
            texts: The documents to embed.

        Returns:
            One vector per document.
        """
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> List[float]:
        """Embed a query.

        Args:
            text: The query text.

        Returns:
            The query vector.
        """
        return self._vector(text)


class BrokenEmbeddings(Embeddings):
    """An embedder that always fails, standing in for a missing model."""

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Fail on every call.

        Args:
            texts: The documents to embed.

        Raises:
            EmbeddingError: Always.
        """
        raise EmbeddingError(
            "the model 'BAAI/bge-small-en-v1.5' could not be downloaded"
        )

    def embed_query(self, text: str) -> List[float]:
        """Fail on every call.

        Args:
            text: The query text.

        Raises:
            EmbeddingError: Always.
        """
        raise EmbeddingError(
            "the model 'BAAI/bge-small-en-v1.5' could not be downloaded"
        )


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Build settings pointing at a temporary data directory.

    Args:
        tmp_path: The temporary directory of the test.
        **overrides: Settings fields to override.

    Returns:
        The settings.
    """
    values: Dict[str, object] = {
        "data_dir": tmp_path / "data",
        "embedding_dimensions": DIMENSIONS,
        "embedding_model": "test-hashing-model",
        "embedding_backend": "fastembed",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def make_definition(
    name: str = "biologist",
    sources: Optional[Sequence[RagSource]] = None,
    **rag: object,
) -> AgentDefinition:
    """Build an agent definition with a RAG section.

    Args:
        name: The agent name.
        sources: The RAG sources.
        **rag: Fields of the RAG configuration.

    Returns:
        The definition.
    """
    config = RagConfig(
        collection=rag.get("collection"),  # type: ignore[arg-type]
        sources=list(sources or []),
        chunk_size=int(rag.get("chunk_size", 300)),  # type: ignore[arg-type]
        chunk_overlap=int(rag.get("chunk_overlap", 50)),  # type: ignore[arg-type]
        top_k=int(rag.get("top_k", 3)),  # type: ignore[arg-type]
    )
    return AgentDefinition(name=name, description="A test agent", rag=config)


def write_pdf(path: Path, pages: Sequence[str]) -> None:
    """Write a small PDF with one line of text per page.

    The file is assembled by hand so the suite does not need a PDF writer
    with fonts. pypdf reads it back as plain text.

    Args:
        path: The PDF to write.
        pages: The text of each page.
    """
    objects: List[bytes] = []
    page_count = len(pages)
    font_id = 3
    page_ids = [4 + 2 * index for index in range(page_count)]
    content_ids = [5 + 2 * index for index in range(page_count)]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for index, text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_ids[index]} 0 R >>"
            ).encode()
        )
        objects.append(
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode()
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: List[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    """Build a small folder of Markdown documents.

    Args:
        tmp_path: The temporary directory of the test.

    Returns:
        The folder holding the documents.
    """
    folder = tmp_path / "corpus"
    folder.mkdir()
    (folder / "alpha.md").write_text(
        "The mitochondria is the powerhouse of the cell. "
        "It produces the adenosine triphosphate the cell runs on.\n" * 12,
        encoding="utf-8",
    )
    (folder / "beta.md").write_text(
        "Photosynthesis converts light energy into chemical energy. "
        "Chlorophyll captures photons in the thylakoid membrane.\n" * 12,
        encoding="utf-8",
    )
    return folder


@pytest.fixture()
def knowledge(tmp_path: Path, corpus: Path) -> AgentKnowledgeBase:
    """Build a knowledge base over the Markdown corpus.

    Args:
        tmp_path: The temporary directory of the test.
        corpus: The Markdown corpus.

    Returns:
        The knowledge base, with a hashing embedder.
    """
    settings = make_settings(tmp_path)
    definition = make_definition(sources=[RagSource(path=corpus)])
    return AgentKnowledgeBase(settings, definition, embeddings=HashingEmbeddings())


# -- the store ------------------------------------------------------------


def test_store_creates_a_collection_with_the_configured_size(tmp_path: Path) -> None:
    """The collection uses the vector size from the settings."""
    settings = make_settings(tmp_path, embedding_dimensions=384)
    store = VectorStore(settings, embeddings=HashingEmbeddings(384))

    store.collection("agent-one")

    assert store.has("agent-one")
    client = store._client  # noqa: SLF001 - reading the raw store
    info = client.get_collection("agent-one")
    vectors = info.config.params.vectors
    assert vectors.size == 384
    assert vectors.distance == "Cosine"


def test_store_upsert_search_count_and_delete(tmp_path: Path) -> None:
    """Documents can be written, found, counted and removed."""
    store = VectorStore(make_settings(tmp_path), embeddings=HashingEmbeddings())
    documents = [
        Document(page_content="apples and oranges", metadata={"source": "a.md"}),
        Document(page_content="bananas and pears", metadata={"source": "b.md"}),
    ]

    store.upsert("notes", documents, ["id-one", "id-two"])

    assert store.count("notes") == 2
    assert store.has("notes")

    found = store.search("notes", "bananas", 1)
    assert len(found) == 1
    assert found[0][0].metadata["source"] == "b.md"
    assert found[0][0].page_content == "bananas and pears"

    store.delete("notes")
    assert store.count("notes") == 0
    assert not store.has("notes")


def test_store_search_of_a_missing_collection_is_empty(tmp_path: Path) -> None:
    """Searching a collection that was never written returns nothing."""
    store = VectorStore(make_settings(tmp_path), embeddings=HashingEmbeddings())

    assert store.search("nothing-here", "hello", 5) == []


def test_store_rejects_mismatched_ids(tmp_path: Path) -> None:
    """A document without an id is a programming error."""
    store = VectorStore(make_settings(tmp_path), embeddings=HashingEmbeddings())

    with pytest.raises(ValueError):
        store.upsert("notes", [Document(page_content="text")], [])


# -- ingestion ------------------------------------------------------------


def test_ingest_counts_documents_and_chunks(knowledge: AgentKnowledgeBase) -> None:
    """Every document is read and split into chunks."""
    report = knowledge.ingest()

    assert isinstance(report, IngestReport)
    assert report.collection == "biologist"
    assert report.documents == 2
    assert report.chunks > 2
    assert report.skipped == 0
    assert report.errors == []
    assert knowledge.store.count("biologist") == report.chunks


def test_ingest_is_idempotent(knowledge: AgentKnowledgeBase) -> None:
    """A second ingest leaves the collection alone."""
    first = knowledge.ingest()
    second = knowledge.ingest()

    assert second.chunks == first.chunks
    assert second.documents == 0
    assert second.skipped == 1
    assert second.errors == []
    assert knowledge.store.count("biologist") == first.chunks


def test_forced_ingest_replaces_the_collection(
    tmp_path: Path, corpus: Path
) -> None:
    """A forced ingest clears the collection and rebuilds it."""
    settings = make_settings(tmp_path)
    definition = make_definition(sources=[RagSource(path=corpus)])
    knowledge = AgentKnowledgeBase(settings, definition, embeddings=HashingEmbeddings())

    knowledge.ingest()
    (corpus / "gamma.md").write_text("Meiosis halves the chromosome number.\n" * 12)
    stale = knowledge.store.count("biologist")

    report = knowledge.ingest(force=True)

    assert report.documents == 3
    assert report.chunks > stale
    assert knowledge.store.count("biologist") == report.chunks


def test_forced_ingest_drops_chunks_of_removed_files(
    tmp_path: Path, corpus: Path
) -> None:
    """A forced ingest does not keep chunks of a file that is gone."""
    settings = make_settings(tmp_path)
    definition = make_definition(sources=[RagSource(path=corpus)])
    knowledge = AgentKnowledgeBase(settings, definition, embeddings=HashingEmbeddings())

    knowledge.ingest()
    (corpus / "beta.md").unlink()
    knowledge.ingest(force=True)

    sources = {
        str(chunk.source)
        for chunk in knowledge.search("photosynthesis chlorophyll", 20)
    }
    assert not any(source.endswith("beta.md") for source in sources)


def test_ingest_records_a_missing_source(tmp_path: Path) -> None:
    """A source that does not exist is reported, not raised."""
    settings = make_settings(tmp_path)
    definition = make_definition(
        sources=[RagSource(path=tmp_path / "nowhere")],
    )
    knowledge = AgentKnowledgeBase(settings, definition, embeddings=HashingEmbeddings())

    report = knowledge.ingest()

    assert report.errors
    assert "does not exist" in report.errors[0]
    assert report.documents == 0
    assert report.chunks == 0


def test_ingest_continues_after_a_broken_source(
    tmp_path: Path, corpus: Path
) -> None:
    """One broken source does not stop the good ones."""
    settings = make_settings(tmp_path)
    definition = make_definition(
        sources=[
            RagSource(path=tmp_path / "nowhere"),
            RagSource(path=corpus),
        ],
    )
    knowledge = AgentKnowledgeBase(settings, definition, embeddings=HashingEmbeddings())

    report = knowledge.ingest()

    assert len(report.errors) == 1
    assert report.documents == 2
    assert report.chunks > 0


def test_ingest_records_an_embedding_failure(tmp_path: Path, corpus: Path) -> None:
    """A model that cannot be loaded is reported, not raised."""
    settings = make_settings(tmp_path)
    definition = make_definition(sources=[RagSource(path=corpus)])
    knowledge = AgentKnowledgeBase(settings, definition, embeddings=BrokenEmbeddings())

    report = knowledge.ingest()

    assert report.errors
    assert "bge-small-en-v1.5" in report.errors[0]
    assert report.chunks == 0


def test_chunk_ids_are_stable(tmp_path: Path, corpus: Path) -> None:
    """The same chunk always gets the same id."""
    settings = make_settings(tmp_path)
    definition = make_definition(sources=[RagSource(path=corpus)])
    knowledge = AgentKnowledgeBase(settings, definition, embeddings=HashingEmbeddings())

    first = knowledge.ingest()
    ids_before = _point_ids(knowledge, "biologist")

    knowledge.ingest(force=True)
    ids_after = _point_ids(knowledge, "biologist")

    assert ids_before == ids_after
    assert len(ids_before) == first.chunks
    first = chunk_id("biologist", "/tmp/a.md", 0)
    assert first == chunk_id("biologist", "/tmp/a.md", 0)
    assert first != chunk_id("biologist", "/tmp/a.md", 1)
    assert first != chunk_id("other", "/tmp/a.md", 0)


def test_directory_source_honours_extensions_and_include(tmp_path: Path) -> None:
    """Only the files that match the source are ingested."""
    folder = tmp_path / "mixed"
    (folder / "nested").mkdir(parents=True)
    (folder / "keep.md").write_text("Alpha document body.\n" * 20, encoding="utf-8")
    (folder / "skip.txt").write_text("Beta document body.\n" * 20, encoding="utf-8")
    (folder / "nested" / "deep.md").write_text(
        "Gamma document body.\n" * 20, encoding="utf-8"
    )

    settings = make_settings(tmp_path)
    definition = make_definition(
        sources=[
            RagSource(path=folder, include="*.md", extensions=[".md"]),
        ],
    )
    knowledge = AgentKnowledgeBase(settings, definition, embeddings=HashingEmbeddings())

    report = knowledge.ingest()

    assert report.documents == 1
    assert report.errors == []


def test_single_file_source_is_ingested(tmp_path: Path, corpus: Path) -> None:
    """A source may point at one file instead of a folder."""
    settings = make_settings(tmp_path)
    definition = make_definition(sources=[RagSource(path=corpus / "alpha.md")])
    knowledge = AgentKnowledgeBase(settings, definition, embeddings=HashingEmbeddings())

    report = knowledge.ingest()

    assert report.documents == 1
    assert report.chunks > 0


def test_pdf_pages_are_cited(tmp_path: Path) -> None:
    """A PDF passage knows the page it came from."""
    folder = tmp_path / "lectures"
    folder.mkdir()
    write_pdf(
        folder / "physics.pdf",
        [
            "Quantum mechanics lecture notes about entanglement",
            "Relativity lecture notes about spacetime curvature",
        ],
    )

    settings = make_settings(tmp_path)
    definition = make_definition(sources=[RagSource(path=folder)])
    knowledge = AgentKnowledgeBase(settings, definition, embeddings=HashingEmbeddings())

    report = knowledge.ingest()
    assert report.errors == []
    assert report.documents == 1
    assert report.chunks == 2

    chunks = knowledge.search("spacetime curvature", 2)
    assert chunks
    assert "physics.pdf" in chunks[0].source
    assert "page 2" in chunks[0].source
    assert "[page 2]" in chunks[0].text


def test_html_tags_are_stripped(tmp_path: Path) -> None:
    """HTML is read as text, not as markup."""
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "page.html").write_text(
        "<html><head><style>p { color: red; }</style></head>"
        "<body><h1>Cellular respiration</h1><p>Glycolysis happens in the "
        "cytoplasm.</p></body></html>",
        encoding="utf-8",
    )

    settings = make_settings(tmp_path)
    definition = make_definition(sources=[RagSource(path=folder)])
    knowledge = AgentKnowledgeBase(settings, definition, embeddings=HashingEmbeddings())

    report = knowledge.ingest()

    assert report.errors == []
    assert report.documents == 1
    chunks = knowledge.search("glycolysis cytoplasm", 1)
    assert chunks
    assert "<h1>" not in chunks[0].text
    assert "Cellular respiration" in chunks[0].text


# -- retrieval ------------------------------------------------------------


def test_search_finds_the_right_source(knowledge: AgentKnowledgeBase) -> None:
    """A query about one document does not return the other."""
    knowledge.ingest()

    chunks = knowledge.search("mitochondria adenosine triphosphate", 3)

    assert chunks
    assert isinstance(chunks[0], RetrievedChunk)
    assert chunks[0].source.endswith("alpha.md")
    assert chunks[0].score > 0
    assert "mitochondria" in chunks[0].text.lower()


def test_search_of_a_missing_collection_is_empty(tmp_path: Path) -> None:
    """An agent that was never ingested into has nothing to offer."""
    settings = make_settings(tmp_path)
    definition = make_definition(sources=[RagSource(path=tmp_path / "nowhere")])
    knowledge = AgentKnowledgeBase(settings, definition, embeddings=HashingEmbeddings())

    assert knowledge.search("anything at all", 5) == []
    assert knowledge.context("anything at all", 5) == ""


def test_search_uses_the_configured_top_k(tmp_path: Path, corpus: Path) -> None:
    """The agent's own top_k is the default."""
    settings = make_settings(tmp_path)
    definition = make_definition(sources=[RagSource(path=corpus)], top_k=1)
    knowledge = AgentKnowledgeBase(settings, definition, embeddings=HashingEmbeddings())
    knowledge.ingest()

    assert len(knowledge.search("cell energy")) == 1
    assert len(knowledge.search("cell energy", 5)) >= 1


def test_context_formats_the_passages(knowledge: AgentKnowledgeBase) -> None:
    """The context names the file every passage came from."""
    knowledge.ingest()

    context = knowledge.context("photosynthesis chlorophyll", 2)

    assert context.startswith("### ")
    assert "### " in context
    assert ".md" in context
    assert "Photosynthesis" in context


def test_context_is_truncated(knowledge: AgentKnowledgeBase) -> None:
    """A long context is cut, and says so."""
    knowledge.ingest()

    context = knowledge.context("mitochondria cell energy", 20)

    assert len(context) <= MAX_CONTEXT_CHARS + len(CONTEXT_TRUNCATION_MARKER)
    if CONTEXT_TRUNCATION_MARKER in context:
        assert len(context) == MAX_CONTEXT_CHARS + len(CONTEXT_TRUNCATION_MARKER)


def test_context_of_nothing_is_empty(knowledge: AgentKnowledgeBase) -> None:
    """An empty knowledge base gives an empty context."""
    assert knowledge.context("anything") == ""


# -- embeddings -----------------------------------------------------------


def test_build_embeddings_rejects_an_unknown_backend(tmp_path: Path) -> None:
    """A typo in the backend name is reported clearly."""
    settings = make_settings(tmp_path, embedding_backend="word2vec")

    with pytest.raises(EmbeddingError) as error:
        build_embeddings(settings)

    assert "word2vec" in str(error.value)


def test_build_embeddings_caches_the_model(tmp_path: Path) -> None:
    """The same backend and model share one instance."""
    settings = make_settings(tmp_path)

    first = build_embeddings(settings)
    second = build_embeddings(settings)

    assert first is second


def test_build_embeddings_openai_backend(tmp_path: Path) -> None:
    """The OpenAI-compatible backend is built without a network call."""
    settings = make_settings(
        tmp_path,
        embedding_backend="openai",
        embedding_model="text-embedding-3-small",
        embedding_base_url="http://localhost:8080/v1",
        embedding_api_key="secret",
    )

    embeddings = build_embeddings(settings)

    assert isinstance(embeddings, Embeddings)
    assert embeddings.model == "text-embedding-3-small"  # type: ignore[attr-defined]


def _point_ids(knowledge: AgentKnowledgeBase, collection: str) -> List[str]:
    """Return the point ids of a collection, sorted.

    Args:
        knowledge: The knowledge base holding the collection.
        collection: The collection name.

    Returns:
        The point ids.
    """
    client = knowledge.store._client  # noqa: SLF001 - reading the raw store
    points, _ = client.scroll(
        collection_name=collection,
        limit=1000,
        with_payload=False,
        with_vectors=False,
    )
    return sorted(str(point.id) for point in points)
