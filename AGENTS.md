# AGENTS.md - Reglas del Proyecto (LangChain / RAG)

## Contexto
Este repositorio contiene el Asistente Fiscal basado en IA (RAG) para el proyecto ContaFis. Se conecta a Odoo 19 (repo separado) mediante APIs y usa LangChain, LangGraph y LangSmith.

## Stack
- Python 3.10+
- LangChain, LangGraph, LangSmith
- FastAPI (para exponer el servicio)
- Pytest (testing)
- OpenAI (LLM/embeddings) + Chroma local (vector store) + BM25 (decisión Spec 001; AWS queda fuera del MVP)

## Reglas Estrictas para el Agente (IA)
1. **NO inventar código de LangChain:** Si no conocés la API exacta de la versión actual, buscá en la documentación oficial o usá `langchain-cli`. No uses sintaxis de versiones viejas.
2. **TDD es obligatorio:** Antes de escribir una cadena o un agente, escribe el test (usando Pytest y mocks de LLM).
3. **Tipado estricto:** Usá Pydantic para todas las entradas y salidas del agente (Structured Outputs).
4. **NO crear archivos temporales** (como `test_agent.py`, `check_rag.py`).
5. **Edición quirúrgica:** Editá usando herramientas que reemplacen texto exacto, no reescribas el archivo entero si solo cambias una línea.
6. **Observabilidad:** Todo agente debe tener `LangSmith` habilitado por defecto en desarrollo.

## Entorno y Secretos
- NUNCA commitees el archivo `.env`.
- Las API Keys (OpenAI/Odoo) deben leerse con `os.getenv()`.

## Keel Nav Workflow (SDD + TDD + ODD + EGM)

Metodología obligatoria del proyecto. Cinco fases (0-4); cada fase es un **meta-skill** con objetivo, entradas, salidas, presupuesto de tokens y criterios de "Hecho cuando". Todos los meta-skills ya están formalizados en `.meta-skills/` y responden a la única pregunta que gobierna las 5 fases: *¿qué fase sigue y qué artefacto debo producir?*

| Fase | Meta-skill | Artefacto / Salida | Presupuesto tokens | Orquestador |
|------|------------|--------------------|--------------------|-------------|
| 0 — Context bootstrap | `.meta-skills/00-context-bootstrap.md` | `docs/context/*` + mapa de dependencias + registro de riesgos | 3000-8000 | subagente `@explore` |
| 1 — SDD spec + EGM audit | `.meta-skills/01-sdd-spec-writer.md` + `.meta-skills/02-egm-goldfish-audit.md` | `specs/NNN-*/spec.md` + `plan.md` + `*.audit.*.md` | 2000-4000 + 1000-2000 | `@planner` (spec) · `/keel-goldfish` → `@reviewer` (auditoría a ciegas) |
| 2 — TDD cycle | `.meta-skills/03-tdd-cycle.md` | test primero (rojo-verde-refactor), luego implementación mínima | 2000-6000 | `@implementer` · `/tdd-check` antes de commitear |
| 3 — ODD vertical slice | `.meta-skills/04-odd-vertical-slice.md` | slice vertical E2E + observación humana | 4000-10000 | `@implementer` + gate de observación humana |
| 4 — Drift control | `.meta-skills/05-drift-control.md` | `*.drift.*.md` (trazabilidad spec ↔ código ↔ tests) | 2000-3000 | `/keel-drift` → `@reviewer` |

**Estado de fases:** ejecutá `/keel-status` para ver la spec activa, en qué fase está y qué artefacto falta. El pipeline completo lo coordina el subagente `@coordinator`.

### Reglas duras de Keel Nav
- **No test, no feature:** ningún requerimiento (RF) se considera implementado sin un test que lo cubra primero. Un commit que introduzca código de feature sin su test se rechaza.
- **Presupuesto de tokens por fase:** cada meta-skill declara `estimated_tokens` (ver tabla). Si una fase excede su presupuesto, se divide o se escala antes de continuar; no se improvisa "quemando" contexto.
- **Trazabilidad:** spec → tests → código. El subagente `@reviewer` es la única autoridad para emitir veredictos de auditoría (EGM) y de deriva.
- **Comandos de solo lectura** (`/keel-status`, `/keel-goldfish`, `/keel-drift`): auditan y reportan; nunca modifican código.
