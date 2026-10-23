# Memoria del Proyecto (LangChain RAG)

## Estado Actual
- Repo inicializado; Fase 0 (entorno y estructura SDD) completada.
- Núcleo de 8 skills LangChain instalado en `.agents/skills/` (lockfile: `skills-lock.json`): `ecosystem-primer`, `langchain-dependencies`, `langchain-fundamentals`, `langchain-rag`, `langgraph-fundamentals`, `langgraph-persistence` (oficiales) + `langchain-architecture`, `rag-implementation` (`wshobson/agents`).
- **Spec 001 completada y aprobada para plan** (`VEREDICTO: APTO PARA PLAN`): `specs/001-chat-fiscal/spec.md`, 863 líneas, 72 RF, 59 AC y 47 EC. Incluye 8 rondas de QA con los bloqueantes resueltos.
- **Plan y tareas generados:** `specs/001-chat-fiscal/plan.md` (38,887 bytes) y `specs/001-chat-fiscal/tasks.md` (26,711 bytes, 10 tareas TDD). Próxima tarea: **T01 — Base, configuración, contratos y dominio puro**.
- Agentes SDD configurados en `.opencode/agents/`: `coordinator`, `planner`, `implementer`, `reviewer`.

## Próximos Pasos
1. Implementar **T01** con `@implementer` (tests primero) y validarla con `@reviewer`.
2. Continuar T02–T10 en orden, una tarea por vez.
3. Configurar entorno virtual de Python e instalar dependencias (LangChain, LangGraph, Chroma, BM25).

## Decisiones y Notas
- Odoo 19 se consulta con **JSON/2** (`POST /json/2/<model>/<method>`), `Authorization: Bearer $ODOO_API_KEY` y `X-Odoo-Database: contea`. No se usa XML-RPC ni usuario/password.
- La API key debe ser dedicada y read-only; no se reutiliza la key admin del MCP. `ODOO_READONLY_ATTESTED=true` declara la atestación de despliegue.
- Alcance MVP: mono-tenant sobre `contea`, multi-company agregada por defecto, consultas read-only de obligaciones, clientes, documentos/cartelera y alertas; RAG con Chroma pre-ingestado y citas.
- Presupuesto de orquestación: máximo 3 tool calls por turno, independiente de `recursion_limit`; `sources_used` evita repetir fuentes.
- Fechas de negocio en `America/Caracas`; `effective_state` corrige el `overdue` prematuro del ERP.
- Se configuró `opencode.json` con la referencia al repo público `MrErov/contafis-odoo-19.0v` (branch `main`) para consultar modelos y APIs del ERP.
