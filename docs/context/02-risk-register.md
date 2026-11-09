# Risk Register

| ID | Context | Risk | Evidence | Severity |
|----|---------|------|----------|----------|
| R1 | `composition-root` | `main.py` concentra app factory, lifespan, runtime, clasificador y dos rutas de ensamblado de tools. | `src/contafis_assistant/main.py` (1068 líneas); `_build_tools` y `_build_user_deps` | high |
| R2 | `graph` / `composition-root` | El prompt del clasificador está redactado por NDA; la lógica completa de clasificación no es visible. | `main.py` líneas 78–89 (`[PROMPT REDACTADO EN VERSIÓN PÚBLICA]`) | high |
| R3 | `memory` / `api` | `ThreadStore` vive en memoria del proceso y el despliegue fija un solo worker; reinicios pierden hilos y no escala horizontalmente. | `memory/threads.py`; `Dockerfile` (comentario H3, `--workers 1`); `main.py` `UVICORN_WORKERS = 1` | high |
| R4 | `odoo` | Contexto más grande y acoplado al ERP; allowlist y tools de documentos concentran gran cantidad de reglas. | 13 archivos Python; `allowlist.py` >450 líneas; `tools/documents.py` >500 líneas | medium |
| R5 | `api` | Autenticación dual (sesión Odoo + token legacy) y digitalización con LLM Vision en archivos grandes. | `api/user_auth.py` 482 líneas; `api/digitize.py` 379 líneas | medium |
| R6 | `domain` | Shared Kernel importado por todos; `answer.py` mezcla composición, render y validación de grounding; además depende de `observability.redaction`. | `domain/answer.py` 564 líneas; imports de `domain` en todos los contextos | medium |
| R7 | `graph` | `TurnState` concentra 35 campos y el grafo depende directamente de tipos de `odoo`. | `graph/state.py`; `graph/nodes.py` 467 líneas; imports de `odoo.scope`/`odoo.tools.entities` | medium |
| R8 | `rag` | Dos backends vectoriales y acoplamiento estricto a manifiesto/versión/SHA; un mismatch degrada la recuperación. | `rag/corpus.py`, `rag/local_vectors.py`, `rag/manifest.py`; `config.vector_backend` | medium |
| R9 | Dependencies | `pyproject.toml` declara cotas inferiores amplias (`langchain>=0.1.0`, `langgraph>=0.0.40`); CI instala `requirements.txt`; el Dockerfile copia `constraints.txt` pero el comando `pip wheel` no lo referencia con `-c`. | `pyproject.toml`, `requirements.txt`, `constraints.txt`, `Dockerfile`, `.github/workflows/ci.yml` | medium |
| R10 | `odoo` / producción | El arranque en producción exige `ODOO_READONLY_ATTESTED=true`; sin atestación el servicio no inicia. | `config.py` `_validate_startup`; `main.py` `ChatRuntime.startup` | high |
| R11 | Repo root | Cientos de directorios `pip-*` y `*.egg-info` contaminan la raíz y pueden confundir herramientas de escaneo. | Listado de raíz; instrucción del dispatch de ignorarlos | low |

## Uncovered zones
- `app.py` — UI Streamlit, fuera de `optional_scope`.
- `tests/` — mapeado estructuralmente (suites y nombres), no leído en detalle.
- `scripts/` — no inspeccionado.
- `notebooks/`, `data/` — artefactos y corpus; no se leyeron contenidos.
- `specs/001-chat-fiscal/` … `specs/006-digitize/` — referenciadas, no reprocesadas.
- `main.py` — internals del prompt del clasificador redactados por NDA.
- `.agents/`, `.opencode/` — configuración del harness, fuera del mapa de producto.
- `src/contafis_assistant.egg-info/` y `pip-*` — artefactos de build.

## Change hotspots (if git available)
| File | Commits touching it |
|------|---------------------|
| `src/contafis_assistant/main.py` | 14 |
| `src/contafis_assistant/config.py` | 9 |
| `src/contafis_assistant/api/routes.py` | 5 |
| `pyproject.toml` | 5 |
| `.github/workflows/ci.yml` | 5 |
| `requirements.txt` | 8 |

> Conteo sobre el historial git disponible; excluye `specs/`, `docs/` y `.env.example` para enfocar hotspots de código.
