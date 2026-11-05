from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.enums import CorpusStatus
from contafis_assistant.rag.bm25 import Bm25Document, Bm25Index
from contafis_assistant.rag.manifest import load_manifest


class IngestError(RuntimeError):
    pass


class IngestPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    text: str = ""


class IngestChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    document_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[str] = Field(default_factory=list)
    chunks: int = 0
    bm25_index_version: int = 1
    bm25_index_sha256: str = ""


def extract_pdf_pages(path: str | Path) -> list[IngestPage]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf no está disponible; instale la dependencia para la ingesta") from exc

    reader = PdfReader(str(path))
    pages: list[IngestPage] = []
    for index, page in enumerate(reader.pages, start=1):
        pages.append(IngestPage(page=index, text=page.extract_text() or ""))
    return pages


def chunk_pages(
    document_id: str,
    title: str,
    pages: Sequence[IngestPage],
    *,
    chunk_chars: int = 1000,
    chunk_overlap: int = 200,
    collection: str | None = None,
) -> list[IngestChunk]:
    if chunk_chars <= 0:
        raise ValueError("chunk_chars debe ser positivo")
    if chunk_overlap < 0 or chunk_overlap >= chunk_chars:
        raise ValueError("chunk_overlap debe estar entre 0 y chunk_chars")

    chunks: list[IngestChunk] = []
    step = chunk_chars - chunk_overlap
    for page in pages:
        text = " ".join(page.text.split())
        if not text:
            continue
        position = 0
        chunk_index = 0
        while position < len(text):
            fragment = text[position : position + chunk_chars].strip()
            if fragment:
                metadata: dict[str, Any] = {
                    "document_id": document_id,
                    "document": title,
                    "page": page.page,
                    "locator": f"p.{page.page}",
                }
                if collection:
                    metadata["collection"] = collection
                chunks.append(
                    IngestChunk(
                        chunk_id=f"{document_id}:p{page.page}:c{chunk_index}",
                        document_id=document_id,
                        text=fragment,
                        metadata=metadata,
                    )
                )
                chunk_index += 1
            if position + chunk_chars >= len(text):
                break
            position += step
    return chunks


def create_chroma_collection(
    path: str | Path,
    collection_name: str,
    *,
    dimensions: int | None = None,
) -> Any:
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError(
            "chromadb no está disponible; instale la dependencia para ejecutar la ingesta"
        ) from exc
    client = chromadb.PersistentClient(path=str(path))
    metadata: dict[str, Any] = {"hnsw:space": "cosine"}
    if dimensions is not None:
        metadata["dimensions"] = dimensions
    return client.get_or_create_collection(
        name=collection_name,
        metadata=metadata,
    )


def build_default_embedder(
    model: str | None = None,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    dimensions: int | None = None,
) -> Any:
    """Embedder NVIDIA oficial, alimentado por ``Settings``.

    Usa ``NVIDIAEmbeddings`` para respetar la semántica ``query``/``passage``
    (``embed_query`` vs ``embed_documents``) y para declarar ``dimensions``.
    ``pydantic-settings`` lee el ``.env`` pero no puebla ``os.environ``, por eso
    el modelo y las credenciales llegan explícitos desde ``Settings``.
    """
    try:
        from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings
    except ImportError as exc:
        raise RuntimeError(
            "langchain-nvidia-ai-endpoints no está disponible; "
            "instálelo con `pip install langchain-nvidia-ai-endpoints`"
        ) from exc
    if not model:
        raise RuntimeError("EMBEDDING_MODEL es obligatorio para la ingesta real")
    kwargs: dict[str, Any] = {"model": model}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    if dimensions is not None:
        kwargs["dimensions"] = dimensions
    return NVIDIAEmbeddings(**kwargs)


def _embed_documents(embedder: Any, texts: list[str]) -> list[list[float]]:
    """Embeber pasajes: acepta un embedder con ``embed_documents`` o un callable."""
    if hasattr(embedder, "embed_documents"):
        return list(embedder.embed_documents(texts))
    return list(embedder(texts))


def ingest_corpus(
    manifest_path: str | Path,
    *,
    bm25_output_path: str | Path | None = None,
    extractor: Callable[[Path], Sequence[IngestPage | str]] | None = None,
    embedder: Any | None = None,
    collection: Any | None = None,
    chroma_path: str | Path | None = None,
    collection_name: str = "contafis_normativa_v1",
    version: int | None = None,
    dimensions: int | None = None,
    chunk_chars: int = 1000,
    chunk_overlap: int = 200,
) -> IngestResult:
    path = Path(manifest_path)
    state = load_manifest(path)
    if state.status != CorpusStatus.ok or state.manifest is None:
        raise IngestError(f"Manifiesto inválido para ingesta: {state.status.value}")

    manifest = state.manifest
    if dimensions is not None and dimensions != manifest.dimensions:
        raise IngestError(
            f"dimensiones del manifiesto ({manifest.dimensions}) distintas a las "
            f"configuradas ({dimensions}); reingiera con el modelo correcto"
        )
    expected_dimensions = manifest.dimensions
    index_version = version if version is not None else manifest.bm25_index_version
    extract = extractor or extract_pdf_pages

    if collection is None and chroma_path is not None:
        collection = create_chroma_collection(
            chroma_path, collection_name, dimensions=expected_dimensions
        )

    all_chunks: list[IngestChunk] = []
    for document in manifest.documents:
        document_path = path.parent / document.file
        raw_pages = list(extract(document_path))
        pages: list[IngestPage] = []
        for position, raw_page in enumerate(raw_pages, start=1):
            if isinstance(raw_page, IngestPage):
                pages.append(raw_page)
            elif isinstance(raw_page, str):
                pages.append(IngestPage(page=position, text=raw_page))
            else:
                raise IngestError("El extractor devolvió un tipo de página no soportado")

        chunks = chunk_pages(
            document.id,
            document.title,
            pages,
            chunk_chars=chunk_chars,
            chunk_overlap=chunk_overlap,
            collection=collection_name,
        )
        all_chunks.extend(chunks)

        if collection is not None and chunks:
            add_kwargs: dict[str, Any] = {
                "ids": [chunk.chunk_id for chunk in chunks],
                "documents": [chunk.text for chunk in chunks],
                "metadatas": [chunk.metadata for chunk in chunks],
            }
            if embedder is not None:
                texts = [chunk.text for chunk in chunks]
                vectors = _embed_documents(embedder, texts)
                if any(len(vector) != expected_dimensions for vector in vectors):
                    raise IngestError(
                        f"dimensiones del embedder ({len(vectors[0])}) distintas a las "
                        f"declaradas ({expected_dimensions}); revise EMBEDDING_MODEL y "
                        f"EMBEDDING_DIMENSIONS"
                    )
                add_kwargs["embeddings"] = vectors
            collection.add(**add_kwargs)

    index = Bm25Index.build(
        [
            Bm25Document(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                text=chunk.text,
                metadata=chunk.metadata,
            )
            for chunk in all_chunks
        ],
        version=index_version,
        collection=collection_name,
    )
    if bm25_output_path is not None:
        index.save(bm25_output_path)

    return IngestResult(
        document_ids=[document.id for document in manifest.documents],
        chunks=len(all_chunks),
        bm25_index_version=index.version,
        bm25_index_sha256=index.sha256 or "",
    )
