# Dependency Map

| Context | Depends on | Depended on by | Coupling |
|---------|-----------|----------------|----------|
| `odoo` | `domain` | `graph`, `api`, `composition-root` | medium |
| `rag` | `domain` | `graph` (vía tool inyectada), `composition-root` | low |
| `graph` | `domain`, `memory`, `odoo`, `observability` | `api` (vía runtime), `composition-root` | high |
| `api` | `domain`, `odoo`, `graph` (vía runtime), `config` | `composition-root` | high |
| `domain` (shared kernel) | `observability` | `odoo`, `rag`, `graph`, `api`, `memory` | high |
| `memory` (soporte) | `domain` | `graph`, `api`, `composition-root` | low |
| `observability` (cross-cutting) | — | `domain`, `graph`, `api`, `composition-root` | low |
| `composition-root` (`main.py`, `config.py`) | all contexts | — | high |

> Coupling estimado a partir de imports estáticos y objetos compartidos en runtime; no se midió acoplamiento dinámico.

## External services
- Odoo 19 JSON/2 — ERP read-only — `config.py` y `odoo/client.py`; variables `ODOO_URL`, `ODOO_DB`, `ODOO_API_KEY`, `ODOO_READONLY_ATTESTED`.
- LLM/embeddings compatibles con OpenAI (NVIDIA NIM por defecto) — clasificación, respuestas, visión de `/digitize` y embeddings — `main.py`, `api/digitize.py`, `rag/ingest.py`; variables `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`.
- LangSmith — trazas de turnos — `observability/tracing.py`; variables `LANGSMITH_*`.
- Chroma — almacén vectorial por defecto — `rag/corpus.py`; variables `CHROMA_PATH`, `CHROMA_COLLECTION`, `VECTOR_BACKEND`.
- Render — deploy hook de producción — `.github/workflows/ci.yml`; secreto `RENDER_DEPLOY_HOOK_URL`.
- Streamlit — UI fuera de `optional_scope` — `app.py`; variable `ASSISTANT_API_URL`.

## Data stores
- Odoo PostgreSQL — `odoo`, `api`, `graph` (lectura indirecta vía tools).
- Chroma collection — `rag`.
- Índice vectorial JSON local (`VECTOR_BACKEND=local`) — `rag`.
- `data/bm25_index.json` — `rag`.
- `data/manifest.json` — `rag`.
- `ThreadStore` in-process (`OrderedDict`) — `graph`, `api`, `memory`.

## Composition root
`src/contafis_assistant/main.py` importa y ensambla todos los contextos: `OdooClient`, `ScopeService`, `CorpusManager`, `RagRetriever`, tools ERP/RAG, `ThreadStore`, clasificador LLM, tracer y `AnswerComposer`. `config.py` define `Settings` y las validaciones de arranque.
