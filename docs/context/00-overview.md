# Repository Overview

## Scope
- `repo_root`: `.`
- `optional_scope`: `src/contafis_assistant/`
- Tipo: brownfield en producción (versión pública con secciones redactadas por NDA).
- Fuera de alcance: `app.py` (UI Streamlit), `tests/`, `scripts/`, `data/`, `specs/`, `.agents/`, `.opencode/`, artefactos `pip-*` y `*.egg-info`.

## Stack
- Language(s): Python 3.10+ (CI y Docker usan 3.12)
- Frameworks: FastAPI, LangGraph, LangChain, Pydantic v2, Streamlit (UI fuera de scope), pytest
- Package manager: pip + setuptools (`pyproject.toml`, `requirements.txt`, `constraints.txt`)
- Orquestación: LangGraph (`StateGraph`)
- Persistencia: Odoo 19 (PostgreSQL externo), Chroma o índice vectorial JSON local, BM25 JSON, `ThreadStore` en memoria

## Bounded contexts
1. `odoo` — `src/contafis_assistant/odoo/` — integración read-only con Odoo 19 por JSON/2.
2. `rag` — `src/contafis_assistant/rag/` — ingesta y recuperación híbrida sobre normativa fiscal.
3. `graph` — `src/contafis_assistant/graph/` — orquestación LangGraph del turno.
4. `api` — `src/contafis_assistant/api/` — superficie HTTP, autenticación y digitalización.

> Regla aplicada: se detectaron 7 áreas; por la regla de `>5` del dispatch, se documentan con archivo los 4 bounded contexts de negocio y las 3 áreas restantes se consolidan aquí (sin archivo propio). `domain` se clasifica como Shared Kernel (DDD-lite), no como contexto de negocio.

## Áreas consolidadas (sin archivo propio)

| Área | Path | Clasificación | Propósito | Interfaz pública (símbolos clave) | Estado |
|------|------|---------------|-----------|-----------------------------------|--------|
| `domain` | `src/contafis_assistant/domain/` | Shared Kernel | Contratos, enums, tiempo Caracas, estados efectivos, agregación, respuesta y validación de grounding. | `ChatRequest`, `ChatResponse`, `Warning`, `Source`, `ToolResult*`, `AnswerComposer`, `render_answer`, `validate_grounding`, `reduce_status`, `effective_obligation_state`, `effective_document_state`, `Clock`, `validate_chat_request`, `validate_rif`, `validate_amounts` | Sin persistencia |
| `memory` | `src/contafis_assistant/memory/` | Soporte | Hilos de conversación in-process con LRU y límites. | `ThreadStore`, `ThreadTurn`, `ThreadResolution`, `ThreadFilters`, `TimeRange` | `OrderedDict` en memoria |
| `observability` | `src/contafis_assistant/observability/` | Cross-cutting | Redacción de PII, logging JSON y trazabilidad LangSmith. | `redact_text`, `redact_payload`, `redact_exception`, `wrap_untrusted`, `configure_logging`, `get_logger`, `LangSmithTracer`, `NullTracer`, `trace_turn_state`, `tracer_from_settings` | Logs a stdout; trazas a LangSmith |
| `composition-root` | `src/contafis_assistant/main.py`, `config.py` | Infraestructura | App factory, lifespan, `ChatRuntime`, wiring de dependencias y `Settings`. | `create_app`, `build_runtime`, `ChatRuntime`, `Settings`, `run`, `get_app` | Estado en `app.state` |

### Notas de las áreas consolidadas
- `domain` es importado por `odoo`, `rag`, `graph`, `api` y `memory`; a su vez `domain/answer.py` importa `observability.redaction`. Es el acoplamiento más transversal del repo.
- `memory.ThreadStore` no tiene persistencia externa; el `Dockerfile` fija `--workers 1` porque el estado vive en el proceso.
- `observability` no depende de otros contextos; es consumido por `domain`, `graph`, `api` y el composition root.
- `main.py` concentra el wiring y dos rutas de ensamblado de tools (`_build_tools` y `_build_user_deps`).

## Global commands
- Install: `pip install -r requirements.txt` (CI); desarrollo: `pip install -e ".[dev]"`
- Test: `pytest -q`; Docker: `RUN_DOCKER_TESTS=1 pytest tests/docker -m docker -v`
- Lint: `ruff check src tests` (configurado en `pyproject.toml`; CI no lo ejecuta)
- Run API: `uvicorn contafis_assistant.main:app --app-dir src --host 0.0.0.0 --port 8000`
- Run UI (fuera de scope): `streamlit run app.py`
- Ingesta de corpus: invocar `contafis_assistant.rag.ingest_cli.main()`; no hay `[project.scripts]` declarado.

## Referencias externas
- `AGENTS.md` — reglas del proyecto, stack y TDD.
- `docs/constitution.md` — 6 principios innegociables.
- `docs/memory.md` — estado, hitos, decisiones y OQs.
- `docs/DEPLOY.md` — runbook de despliegue.
- `docs/deployment/` — artefactos de despliegue.
- `specs/001-chat-fiscal/` … `specs/006-digitize/` — specs existentes; no se reprocesan en Phase 0.
- `README.md` — alcance, NDA y ecosistema.

## Phase 0 status
- [x] Reconnaissance
- [x] Contexts detected: 7 áreas → 4 bounded contexts documentados + 3 consolidados
- [x] Per-context documentation: `odoo.md`, `rag.md`, `graph.md`, `api.md`
- [x] Dependency map: `01-dependency-map.md`
- [x] Risk register: `02-risk-register.md`
- [x] G1–G5
- [ ] G6 con warning: alcance amplio; se priorizaron 4 contextos y se consolidaron 3 áreas para no exceder el presupuesto de una pasada.
- Nota G5: la instalación de `.meta-skills/` fue un paso previo solicitado; Phase 0 solo escribió `docs/context/`.

## Handoff → Phase 1
Mandatory reading for `01-sdd-spec-writer`:
- `docs/context/00-overview.md`
- `docs/context/01-dependency-map.md`
- `docs/context/<user-story-context>.md` — `odoo.md`, `rag.md`, `graph.md` o `api.md`
- Para historias que toquen `domain`, `memory` u `observability`, leer la sección consolidada de este documento y `01-dependency-map.md`.
