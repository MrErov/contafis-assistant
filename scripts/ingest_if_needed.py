#!/usr/bin/env python3
"""
Script idempotente de ingesta del corpus para Render.

Compara la versi\u00f3n y hash del manifiesto con el conteo de la colecci\u00f3n Chroma.
- Si coinciden \u2192 sale con c\u00f3digo 0 SIN embeber (idempotente)
- Si no coinciden \u2192 invoca contafis_assistant.rag.ingest_cli.main()

Uso:
    python scripts/ingest_if_needed.py [--manifest PATH] [--chroma-path PATH] [--collection NAME]

Variables de entorno requeridas (via Settings):
    CHROMA_PATH, CHROMA_COLLECTION, BM25_INDEX_PATH, BM25_INDEX_VERSION,
    EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, OPENAI_API_KEY, OPENAI_BASE_URL
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# A\u00f1adir src al path para imports
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from contafis_assistant.config import Settings
from contafis_assistant.domain.enums import CorpusStatus
from contafis_assistant.rag.corpus import CorpusManager
from contafis_assistant.rag.ingest_cli import main as ingest_main
from contafis_assistant.rag.manifest import manifest_path_for_bm25


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingesta idempotente del corpus: solo ejecuta si el manifiesto difiere de Chroma"
    )
    parser.add_argument("--manifest", help="Ruta al manifest.json (default: derivado de BM25_INDEX_PATH)")
    parser.add_argument("--chroma-path", help="Ruta a la base de datos Chroma (default: Settings.chroma_path)")
    parser.add_argument("--collection", help="Nombre de la colecci\u00f3n Chroma (default: Settings.chroma_collection)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Forzar ingesta ignorando la comprobaci\u00f3n de idempotencia",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo comprobar y reportar, no ejecutar ingesta",
    )
    return parser.parse_args()


def load_settings() -> Settings:
    """Carga Settings leyendo .env del repo root."""
    env_file = ROOT / ".env"
    return Settings(_env_file=env_file if env_file.is_file() else None)


def check_corpus_sync(settings: Settings, manifest_path: Path, chroma_path: Path, collection_name: str) -> tuple[bool, str]:
    """
    Verifica si el corpus est\u00e1 sincronizado.

    Returns:
        (is_synced, reason): is_synced=True si no hace falta ingesta,
        reason explica el estado.
    """
    manager = CorpusManager(
        settings=settings,
        manifest_path=manifest_path,
        chroma_path=chroma_path,
        collection_name=collection_name,
    )
    state = manager.open()

    # Si el manifiesto no est\u00e1 ok, hay que ingerir (o hay error de configuraci\u00f3n)
    if state.manifest_status != CorpusStatus.ok:
        return False, f"manifiesto: {state.manifest_status.value}"

    # Si Chroma est\u00e1 missing o empty, hay que ingerir
    if state.chroma_status in (CorpusStatus.missing, CorpusStatus.empty):
        return False, f"chroma: {state.chroma_status.value} (count={state.document_count})"

    # Si hay mismatch en BM25 o manifesto, hay que reingestar
    if state.bm25_status == CorpusStatus.mismatch or state.manifest_status == CorpusStatus.mismatch:
        return False, f"mismatch: bm25={state.bm25_status.value}, manifest={state.manifest_status.value}"

    # Verificar que el conteo de Chroma coincida con los documentos del manifiesto
    manifest = state.manifest
    if manifest is not None:
        expected_chunks_min = len(manifest.documents)  # al menos 1 chunk por documento
        actual_count = state.document_count
        if actual_count < expected_chunks_min:
            return False, f"chroma count {actual_count} < expected minimum {expected_chunks_min}"

    # Todo ok: versi\u00f3n y hash coinciden, Chroma tiene datos
    return True, f"ok: version={state.version}, chroma_count={state.document_count}"


def main() -> int:
    args = parse_args()
    settings = load_settings()

    # Resolver rutas
    manifest_path = Path(args.manifest) if args.manifest else manifest_path_for_bm25(settings.bm25_index_path)
    chroma_path = Path(args.chroma_path) if args.chroma_path else Path(settings.chroma_path)
    collection_name = args.collection or settings.chroma_collection

    print(f"[ingest_if_needed] manifest={manifest_path}")
    print(f"[ingest_if_needed] chroma_path={chroma_path}")
    print(f"[ingest_if_needed] collection={collection_name}")

    if not manifest_path.is_file():
        print(f"[ingest_if_needed] ERROR: Manifiesto no encontrado: {manifest_path}", file=sys.stderr)
        return 1

    # Comprobar sincronizaci\u00f3n
    synced, reason = check_corpus_sync(settings, manifest_path, chroma_path, collection_name)
    print(f"[ingest_if_needed] Estado: {reason}")

    if synced and not args.force:
        print("[ingest_if_needed] Corpus sincronizado \u2192 saltando ingesta (idempotente)")
        return 0

    if args.dry_run:
        print("[ingest_if_needed] DRY-RUN: se ejecutar\u00eda la ingesta")
        return 0

    print("[ingest_if_needed] Ejecutando ingesta...")
    # Invocar la CLI de ingesta existente
    original_argv = sys.argv
    try:
        sys.argv = ["ingest_corpus.py", "--manifest", str(manifest_path)]
        return ingest_main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())