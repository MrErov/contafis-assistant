from __future__ import annotations

import argparse
from pathlib import Path

from contafis_assistant.config import Settings
from contafis_assistant.rag.ingest import (
    build_default_embedder,
    create_vector_collection,
    ingest_corpus,
)
from contafis_assistant.rag.manifest import manifest_path_for_bm25

# Raíz del repo: src/contafis_assistant/rag/ingest_cli.py -> parents[3].
# ``Settings(_env_file=None)`` desactivaba la lectura del .env y la CLI fallaba
# aunque el servidor API funcionara (este usa ``Settings()`` con CWD=repo root).
REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_settings() -> Settings:
    """Settings leyendo el .env del repo, sin depender del CWD actual."""
    env_file = REPO_ROOT / ".env"
    return Settings(_env_file=env_file if env_file.is_file() else None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingesta offline del corpus normativo")
    parser.add_argument("--manifest", help="Ruta del manifest.json")
    args = parser.parse_args(argv)

    settings = _load_settings()
    manifest_path = (
        Path(args.manifest)
        if args.manifest
        else manifest_path_for_bm25(settings.bm25_index_path)
    )
    collection = create_vector_collection(
        settings.chroma_path,
        settings.chroma_collection,
        dimensions=settings.embedding_dimensions,
        backend=settings.vector_backend,
    )
    result = ingest_corpus(
        manifest_path,
        bm25_output_path=settings.bm25_index_path,
        embedder=build_default_embedder(
            settings.embedding_model,
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            dimensions=settings.embedding_dimensions,
        ),
        collection=collection,
        collection_name=settings.chroma_collection,
        version=settings.bm25_index_version,
        dimensions=settings.embedding_dimensions,
    )
    print(
        f"documentos={len(result.document_ids)} chunks={result.chunks} "
        f"bm25_version={result.bm25_index_version} sha256={result.bm25_index_sha256}"
    )
    return 0
