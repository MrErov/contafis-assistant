# Memoria del Proyecto (LangChain RAG)

## Estado Actual
- Repo inicializado; Fase 0 (entorno y estructura SDD) completada.
- Núcleo de 8 skills LangChain instalado en `.agents/skills/` (lockfile: `skills-lock.json`): `ecosystem-primer`, `langchain-dependencies`, `langchain-fundamentals`, `langchain-rag`, `langgraph-fundamentals`, `langgraph-persistence` (oficiales) + `langchain-architecture`, `rag-implementation` (`wshobson/agents`).
- **Spec 001 cerrada y completada al 100%**: `specs/001-chat-fiscal/spec.md` (863 líneas, 72 RF, 59 AC, 47 EC). Incluye 8 rondas de QA con todos los bloqueantes resueltos.
- **Suite de tests: 671 tests en verde** (T01–T10 completadas, 100% TDD, sin red/tokens/servicios reales).
  - Tests unitarios: 512
  - Tests de integración: 111
  - Tests de API: 62
  - Tests de aceptación E2E: 23 (9 estados de la matriz §8.3)
  - Tests de seguridad/auditoría: 24 (allowlist, PII, prompt injection, read-only)
- **Plan y tareas completados**: `specs/001-chat-fiscal/plan.md`, `specs/001-chat-fiscal/tasks.md` (T01–T10 todas `[x]`).
- Agentes SDD configurados en `.opencode/agents/`: `coordinator`, `planner`, `implementer`, `reviewer`.

## Próximos Pasos
1. **Spec 002**: Interfaz de Usuario (Streamlit o Web) — frontend para el asistente.
2. **Spec 003**: Autenticación Real — eliminar `scope_admin_key`, implementar auth multi-usuario real.
3. **Spec 004**: Ingesta dinámica de PDFs — Gacetas/Leyes, OCR, versionado, reindexado en runtime.

## Decisiones y Notas
- Odoo 19 se consulta con **JSON/2** (`POST /json/2/<model>/<method>`), `Authorization: Bearer $ODOO_API_KEY` y `X-Odoo-Database: contea`. No se usa XML-RPC ni usuario/password.
- La API key debe ser dedicada y read-only; no se reutiliza la key admin del MCP. `ODOO_READONLY_ATTESTED=true` declara la atestación de despliegue.
- Alcance MVP: mono-tenant sobre `contea`, multi-company agregada por defecto, consultas read-only de obligaciones, clientes, documentos/cartelera y alertas; RAG con Chroma pre-ingestado y citas.
- Presupuesto de orquestación: máximo 3 tool calls por turno, independiente de `recursion_limit`; `sources_used` evita repetir fuentes.
- Fechas de negocio en `America/Caracas`; `effective_state` corrige el `overdue` prematuro del ERP.
- Se configuró `opencode.json` con la referencia al repo público `MrErov/contafis-odoo-19.0v` (branch `main`) para consultar modelos y APIs del ERP.