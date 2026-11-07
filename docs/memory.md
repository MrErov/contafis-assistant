# Memoria del Proyecto (LangChain RAG)

## Estado Actual
- Repo inicializado; Fase 0 (entorno y estructura SDD) completada.
- Núcleo de 8 skills LangChain instalado en `.agents/skills/` (lockfile: `skills-lock.json`): `ecosystem-primer`, `langchain-dependencies`, `langchain-fundamentals`, `langchain-rag`, `langgraph-fundamentals`, `langgraph-persistence` (oficiales) + `langchain-architecture`, `rag-implementation` (`wshobson/agents`).
- **Spec 001 cerrada y completada al 100%**: `specs/001-chat-fiscal/spec.md` (863 líneas, 72 RF, 59 AC, 47 EC). Incluye 8 rondas de QA con todos los bloqueantes resueltos.
- **Suite de tests: 841 tests en verde** (Spec 001: 671 + Spec 002: 15 + Spec 004: 155 nuevos, 100% TDD, sin red/tokens/servicios reales).
  - Tests unitarios: 512
  - Tests de integración: 111
  - Tests de API: 62
  - Tests de aceptación E2E: 23 (9 estados de la matriz §8.3)
  - Tests de seguridad/auditoría: 24 (allowlist, PII, prompt injection, read-only)
  - Tests de UI (login/logout): 6
  - Tests de Docker/contenedor: 12
  - Tests de liveness: 4
  - Tests de contrato de entorno: 4
- **Plan y tareas completados**: `specs/001-chat-fiscal/plan.md`, `specs/001-chat-fiscal/tasks.md` (T01–T10 todas `[x]`); `specs/002-ui-auth/plan.md`, `specs/002-ui-auth/tasks.md` (T01–T10 todas `[x]`); `specs/004-deployment/plan.md`, `specs/004-deployment/tasks.md` (T01–T10 todas `[x]`).
- Agentes SDD configurados en `.opencode/agents/`: `coordinator`, `planner`, `implementer`, `reviewer`.

## Hito: Deployment Completado (Spec 004)
- ✅ Dockerfile multi-stage (`python:3.12-slim`), usuario no-root (UID 10001)
- ✅ `docker-compose.yml` solo desarrollo local con bind mount `./data:/app/data`
- ✅ `.dockerignore` excluye `.env`, `.venv`, `tests/`, `data/`, `specs/`, `docs/`
- ✅ `constraints.txt` para build reproducible (pins exactos de todas las deps)
- ✅ CI/CD: jobs `test`, `image` (smoke test contenedor), `deploy` (hook Render solo en `push` a `main`)
- ✅ `GET /livez` sin auth para health check de plataforma (Render/Railway)
- ✅ `render.yaml` Blueprint: staging (Free) + prod comentado (Starter+ con disco)
- ✅ Corpus en disco persistente `/var/data` (no en imagen); ingesta idempotente one-off
- ✅ Staging con `APP_ENV=development` (D3); producción exige `ODOO_READONLY_ATTESTED=true`
- ✅ 841 tests pasando (671 Spec 001 + 15 Spec 002 + 155 Spec 004)

## Próximos Pasos
1. **Spec 003**: Autenticación Real — eliminar `scope_admin_key`, implementar auth multi-usuario real.
2. **Spec 005**: Ingesta dinámica de PDFs — Gacetas/Leyes, OCR, versionado, reindexado en runtime.
3. **Spec 006**: Despliegue UI Streamlit — `app.py` en Render/Railway con `ASSISTANT_API_URL`.

## Decisiones y Notas
- Odoo 19 se consulta con **JSON/2** (`POST /json/2/<model>/<method>`), `Authorization: Bearer $ODOO_API_KEY` y `X-Odoo-Database: contea`. No se usa XML-RPC ni usuario/password.
- La API key debe ser dedicada y read-only; no se reutiliza la key admin del MCP. `ODOO_READONLY_ATTESTED=true` declara la atestación de despliegue.
- Alcance MVP: mono-tenant sobre `contea`, multi-company agregada por defecto, consultas read-only de obligaciones, clientes, documentos/cartelera y alertas; RAG con Chroma pre-ingestado y citas.
- Presupuesto de orquestación: máximo 3 tool calls por turno, independiente de `recursion_limit`; `sources_used` evita repetir fuentes.
- Fechas de negocio en `America/Caracas`; `effective_state` corrige el `overdue` prematuro del ERP.
- Se configuró `opencode.json` con la referencia al repo público `MrErov/contafis-odoo-19.0v` (branch `main`) para consultar modelos y APIs del ERP.
- **D3**: Staging usa `APP_ENV=development` + `API_SHARED_SECRET` (no `production` para evitar crash loop por H2).
- **D5**: `GET /livez` endpoint sin auth para Render health check; `/health` sigue protegido con `X-API-Key`.
- **H6**: PDFs en disco persistente `/var/data` (no en imagen); manifiesto valida SHA-256 y páginas.
- **constraints.txt**: pins exactos para build reproducible; reconciliado `requirements.txt` + `pyproject.toml`.
- **Pendientes conocidos (OQs):**
  - **OQ-2**: Canal para depositar PDFs en disco (bucket privado, repo privado, job con descarga firmada).
  - **OQ-4**: Odoo producción público accesible desde Render (TLS, allowlist IP egress).
  - **OQ-6**: Despliegue UI Streamlit (`app.py`) en Render/Railway.
  - **OQ-7**: Tracing LangSmith en staging (¿`HIDE_CONTENT=true` o desactivado?).