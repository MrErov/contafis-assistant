# CONTEXT: rag

## Purpose
Ingesta y recuperación híbrida (dense + BM25) sobre el corpus de normativa fiscal venezolana, con control de manifiesto, versión y SHA-256.

## Responsibilities
- Extraer y fragmentar PDFs/Markdown del corpus en chunks con metadatos de documento y página.
- Construir y persistir embeddings en Chroma o en el backend vectorial local.
- Cargar el índice BM25 y el manifiesto; validar versión, SHA-256 y dimensiones.
- Recuperar candidatos dense y BM25, fusionarlos con RRF y emitir fuentes con warnings.
- Exponer la tool `query_normativa` para el grafo y degradar el resultado si el corpus no está disponible.

## Public interface
- `CorpusManager`, `CorpusState`, `open_chroma_collection`, `open_vector_collection` — ciclo de vida y estado del corpus.
- `RagRetriever`, `RagResult`, `fuse_results`, `DenseHit` — recuperación híbrida.
- `query_normativa`, `NormativeToolResult` — tool normativa para LangGraph.
- `ingest_corpus`, `chunk_pages`, `chunk_markdown`, `extract_pdf_pages`, `extract_markdown_pages`, `create_vector_collection`, `build_default_embedder`.
- `Bm25Index`, `Bm25Document`, `Bm25Hit`, `load_bm25_index`, `load_manifest`, `manifest_path_for_bm25`.
- `LocalVectorCollection` — backend vectorial local persistido en JSON.

## Dependencies
- Internal: `domain` (contratos, enums, warnings).
- External: `chromadb` (backend por defecto), `numpy` (backend local), `pypdf` (extracción PDF), `langchain-nvidia-ai-endpoints` (embeddings), `pydantic`.

## Persistence / State
- `data/chroma/` — colección Chroma persistente (`CHROMA_PATH`, `CHROMA_COLLECTION`).
- Backend local alternativo (`VECTOR_BACKEND=local`): índice vectorial JSON con distancia coseno.
- `data/bm25_index.json` — índice BM25 (`BM25_INDEX_PATH`).
- `data/manifest.json` — manifiesto de documentos, versión, SHA-256 y dimensiones.
- `CorpusManager` mantiene estado en memoria (`CorpusState`, colección abierta, índice BM25).

## Commands
- Build: `pip install -r requirements.txt`
- Test: `pytest tests/unit/test_corpus.py tests/unit/test_ingest.py tests/unit/test_local_vectors.py tests/unit/test_manifest.py tests/unit/test_retrieval.py tests/unit/test_tokenizer.py tests/integration/test_rag_tool.py -q`
- Run: `python -c "from contafis_assistant.rag.ingest_cli import main; raise SystemExit(main())"` (ingesta offline; no hay `[project.scripts]` declarado)

## Gotchas / Observed tech debt
- `rag/__init__.py` no reexporta símbolos; los consumidores importan módulos internos directamente.
- Conviven dos backends vectoriales (`chroma` y `local`) seleccionables por `VECTOR_BACKEND`.
- La ingesta exige que las dimensiones del manifiesto coincidan con las configuradas (`EMBEDDING_DIMENSIONS`); si no, falla con `IngestError`.
- El estado del corpus depende de la coincidencia entre manifiesto, índice BM25 y colección vectorial; un mismatch degrada la tool a `upstream_error` o `partial`.
- La CLI de ingesta lee el `.env` de la raíz del repo, no el CWD.

## Last updated
2026-11-09 (skill 0.1.0)
