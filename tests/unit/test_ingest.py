"""Embedder NVIDIA de la ingesta, semántica query/passage y dimensiones."""

from __future__ import annotations

import json
import sys
import types
from hashlib import sha256

import pytest

from contafis_assistant.rag import ingest_cli
from contafis_assistant.rag.ingest import (
    IngestError,
    IngestPage,
    build_default_embedder,
    create_chroma_collection,
    extract_markdown_pages,
    chunk_markdown,
    ingest_corpus,
)


class _FakeNVIDIAEmbeddings:
    """Doble de `NVIDIAEmbeddings` que solo guarda los kwargs recibidos."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


@pytest.fixture
def fake_langchain_nvidia(monkeypatch):
    """Evita el cliente real de NVIDIA (no hay credenciales ni red)."""
    module = types.ModuleType("langchain_nvidia_ai_endpoints")
    module.NVIDIAEmbeddings = _FakeNVIDIAEmbeddings
    monkeypatch.setitem(sys.modules, "langchain_nvidia_ai_endpoints", module)
    return _FakeNVIDIAEmbeddings


class _RecordingEmbedder:
    """Registra si la ingesta pide `passage` o `query`."""

    def __init__(self, vectors: list[list[float]] | None = None) -> None:
        self.passages: list[str] = []
        self.queries: list[str] = []
        self._vectors = vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.passages.extend(texts)
        if self._vectors is not None:
            return self._vectors
        return [[0.1, 0.2] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1, 0.2]


class _FakeCollection:
    def __init__(self) -> None:
        self.added: list[dict] = []

    def add(self, **kwargs: object) -> None:
        self.added.append(kwargs)


def _manifest(tmp_path, *, dimensions: int = 2, bm25_sha: str = "a" * 64):
    pdf = tmp_path / "doc-1.pdf"
    content = b"%PDF-1.4 contenido de prueba"
    pdf.write_bytes(content)
    payload = {
        "bm25_index_version": 1,
        "bm25_index_sha256": bm25_sha,
        "dimensions": dimensions,
        "documents": [
            {
                "id": "doc-1",
                "title": "Norma IVA",
                "version": "2024.1",
                "file": pdf.name,
                "sha256": sha256(content).hexdigest(),
                "pages": 1,
                "published_at": "2024-01-01",
                "language": "es",
                "origin": "Gaceta Oficial",
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _extractor(_path):
    return [IngestPage(page=1, text="iva impuesto al valor agregado " * 20)]


# --- build_default_embedder -------------------------------------------------


def test_embedder_uses_nvidia_embeddings_and_declares_dimensions(fake_langchain_nvidia):
    embedder = build_default_embedder("nvidia/nemotron-3-embed-1b", dimensions=2048)

    assert embedder.kwargs["model"] == "nvidia/nemotron-3-embed-1b"
    assert embedder.kwargs["dimensions"] == 2048


def test_embedder_forwards_credentials_from_settings(fake_langchain_nvidia):
    embedder = build_default_embedder(
        "nvidia/nemotron-3-embed-1b",
        api_key="clave-desde-settings",
        base_url="https://integrate.api.nvidia.com/v1",
        dimensions=2048,
    )

    assert embedder.kwargs["api_key"] == "clave-desde-settings"
    assert embedder.kwargs["base_url"] == "https://integrate.api.nvidia.com/v1"


def test_embedder_omits_optional_kwargs_when_not_supplied(fake_langchain_nvidia):
    embedder = build_default_embedder("nvidia/nemotron-3-embed-1b")

    assert embedder.kwargs == {"model": "nvidia/nemotron-3-embed-1b"}


def test_embedder_requires_a_model(fake_langchain_nvidia):
    with pytest.raises(RuntimeError, match="EMBEDDING_MODEL es obligatorio"):
        build_default_embedder()


def test_embedder_reports_missing_nvidia_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "langchain_nvidia_ai_endpoints", None)

    with pytest.raises(RuntimeError, match="langchain-nvidia-ai-endpoints"):
        build_default_embedder("nvidia/nemotron-3-embed-1b")


# --- semántica de ingesta (passage) ----------------------------------------


def test_ingest_embeds_documents_as_passages_never_as_queries(tmp_path):
    manifest = _manifest(tmp_path)
    embedder = _RecordingEmbedder()
    collection = _FakeCollection()

    ingest_corpus(
        manifest,
        extractor=_extractor,
        embedder=embedder,
        collection=collection,
        collection_name="col",
        version=1,
    )

    assert embedder.passages, "la ingesta debe usar embed_documents (passage)"
    assert embedder.queries == [], "la ingesta nunca debe usar embed_query"
    assert collection.added


def test_ingest_rejects_manually_configured_dimension_mismatch(tmp_path):
    manifest = _manifest(tmp_path, dimensions=2048)

    with pytest.raises(IngestError, match="dimensiones"):
        ingest_corpus(
            manifest,
            extractor=_extractor,
            embedder=_RecordingEmbedder(),
            collection=_FakeCollection(),
            dimensions=1024,
        )


def test_ingest_rejects_embedder_output_with_wrong_dimensions(tmp_path):
    manifest = _manifest(tmp_path, dimensions=2048)

    with pytest.raises(IngestError, match="dimensiones"):
        ingest_corpus(
            manifest,
            extractor=_extractor,
            embedder=_RecordingEmbedder(vectors=[[0.1, 0.2]]),
            collection=_FakeCollection(),
        )


# --- dimensiones en Chroma --------------------------------------------------


def test_create_chroma_collection_declares_dimensions(monkeypatch):
    recorded: dict[str, object] = {}

    class _Client:
        def __init__(self, path: str) -> None:
            recorded["path"] = path

        def get_or_create_collection(self, *, name: str, metadata: dict) -> object:
            recorded["name"] = name
            recorded["metadata"] = metadata
            return object()

    module = types.ModuleType("chromadb")
    module.PersistentClient = _Client
    monkeypatch.setitem(sys.modules, "chromadb", module)

    create_chroma_collection("data/test-chroma", "contafis_normativa_v1", dimensions=2048)

    assert recorded["metadata"] == {"hnsw:space": "cosine", "dimensions": 2048}


# --- .env de la CLI ---------------------------------------------------------


def test_load_settings_reads_env_file_from_repo_root(settings_env, monkeypatch, tmp_path):
    """La CLI lee el `.env` del repo aunque el CWD sea otro."""
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    (tmp_path / ".env").write_text(
        "ODOO_URL=http://127.0.0.1:8069\n"
        "ODOO_API_KEY=clave-dotenv\n"
        "OPENAI_API_KEY=openai-dotenv\n"
        "OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1\n"
        "LLM_MODEL=llm-dotenv\n"
        "EMBEDDING_MODEL=nvidia/nemotron-3-embed-1b\n"
        "CHROMA_PATH=data/test-chroma\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ingest_cli, "REPO_ROOT", tmp_path)

    settings = ingest_cli._load_settings()

    assert settings.embedding_model == "nvidia/nemotron-3-embed-1b"
    assert settings.embedding_dimensions == 2048


def test_load_settings_without_env_file_still_works(settings_env, monkeypatch, tmp_path):
    """Si no hay `.env` se sigue construyendo Settings (sin romper la CLI)."""
    monkeypatch.setattr(ingest_cli, "REPO_ROOT", tmp_path)

    settings = ingest_cli._load_settings()

    assert settings.embedding_model == "embedding-test"


# --- procesamiento de Markdown ---------------------------------------------------


def _markdown_manifest(tmp_path, *, dimensions: int = 2, bm25_sha: str = "a" * 64):
    md = tmp_path / "doc-1.md"
    content = "# Título Principal\n\n## Sección 1\n\nTexto de la sección 1.\n\n## Sección 2\n\nTexto de la sección 2."
    md.write_text(content, encoding="utf-8")
    # Calcular el sha256 real del contenido
    content_sha = sha256(content.encode("utf-8")).hexdigest()
    payload = {
        "bm25_index_version": 1,
        "bm25_index_sha256": "b" * 64,
        "dimensions": dimensions,
        "documents": [
            {
                "id": "doc-md-1",
                "title": "Norma Markdown",
                "version": "2024.1",
                "file": "doc-1.md",
                "sha256": content_sha,
                "pages": 1,
                "published_at": "2024-01-01",
                "language": "es",
                "origin": "Test",
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_extract_markdown_pages_loads_content(tmp_path):
    """extract_markdown_pages carga el contenido de un archivo .md."""
    md = tmp_path / "test.md"
    md.write_text("# Hola\n\nContenido.", encoding="utf-8")
    
    pages = extract_markdown_pages(md)
    
    assert len(pages) == 1
    assert "Hola" in pages[0].text
    assert "Contenido" in pages[0].text


def test_ingest_markdown_file_is_chunked_by_headers(tmp_path):
    """Un archivo .md se trocea por headers y se indexa con metadata de headers."""
    md = tmp_path / "doc-1.md"
    # Create the markdown file first
    content = "# Título Principal\n\n## Sección 1\n\nTexto de la sección 1.\n\n## Sección 2\n\nTexto de la sección 2."
    md.write_text(content, encoding="utf-8")
    
    pages = extract_markdown_pages(md)
    chunks = chunk_markdown(
        "doc-md-1",
        "Norma Markdown",
        pages,
        chunk_chars=1000,
        chunk_overlap=200,
        collection="col",
    )
    
    # Verify chunks have header metadata
    assert len(chunks) > 0
    for chunk in chunks:
        assert "document_id" in chunk.metadata
        assert "header1" in chunk.metadata or "header2" in chunk.metadata or "header3" in chunk.metadata