from __future__ import annotations

import argparse
from pathlib import Path

from contafis_assistant.config import Settings
from contafis_assistant.rag.ingest import (
    build_default_embedder,
    create_chroma_collection,
    extract_pdf_pages,
    ingest_corpus,
)
from contafis_assistant.rag.manifest import manifest_path_for_bm25


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingesta offline del corpus normativo")
    parser.add_argument("--manifest", help="Ruta del manifest.json")
    args = parser.parse_args(argv)

    settings = Settings(_env_file=None)
    manifest_path = (
        Path(args.manifest)
        if args.manifest
        else manifest_path_for_bm25(settings.bm25_index_path)
    )
    collection = create_chroma_collection(settings.chroma_path, settings.chroma_collection)
    result = ingest_corpus(
        manifest_path,
        bm25_output_path=settings.bm25_index_path,
        extractor=extract_pdf_pages,
        embedder=build_default_embedder(),
        collection=collection,
        collection_name=settings.chroma_collection,
        version=settings.bm25_index_version,
    )
    print(
        f"documentos={len(result.document_ids)} chunks={result.chunks} "
        f"bm25_version={result.bm25_index_version} sha256={result.bm25_index_sha256}"
    )
    return 0
