# Proyecto: ContaFis AI Assistant 🧠📊
Asistente fiscal conversacional basado en RAG para contadores venezolanos.

## Stack
- Python 3.10+ / FastAPI
- LangChain / LangGraph / LangSmith
- NVIDIA NIM (OpenAI-compatible)
- ChromaDB (Vector Store)
- Odoo 19 (ERP externo vía JSON/2 API)

## Metodología: Keel Nav (SDD + TDD + ODD + EGM)

Este proyecto sigue **Keel Nav**, una metodología híbrida de 5 fases con trazabilidad completa spec → tests → código. Cada fase es un meta-skill formal en `.meta-skills/`:

| Fase | Meta-skill | Presupuesto tokens | Artefacto |
|------|------------|--------------------|-----------|
| 0 — Context Bootstrap | `00-context-bootstrap.md` | 3000-8000 | `docs/context/` |
| 1 — SDD Spec + EGM Audit | `01-sdd-spec-writer.md` + `02-egm-goldfish-audit.md` | 2000-4000 + 1000-2000 | `specs/NNN-*/spec.md`, `*.audit.*.md` |
| 2 — TDD Cycle | `03-tdd-cycle.md` | 2000-6000 | Tests rojos → verdes |
| 3 — ODD Vertical Slice | `04-odd-vertical-slice.md` | 4000-10000 | Slice E2E + observación humana |
| 4 — Drift Control | `05-drift-control.md` | 2000-3000 | `*.drift.*.md` |

**Reglas duras**: No test, no feature. Presupuesto de tokens por fase. Trazabilidad spec→tests→código. El subagente `@reviewer` es la única autoridad para auditorías y veredictos de deriva.

## Índice de Especificaciones
- [001-chat-fiscal](specs/001-chat-fiscal/spec.md) — MVP del Asistente (Conversacional + RAG + Odoo).
  - Estado: ✅ COMPLETADA (671 tests, 59 AC, 48 acceptance/security tests).
- [002-ui-auth](specs/002-ui-auth/spec.md) — Interfaz de Usuario y Autenticación (Streamlit + FastAPI).
  - Estado: ✅ COMPLETADA (686 tests, 125 AC, UI tests verdes).
- [004-deployment](specs/004-deployment/spec.md) — Dockerización y Deployment (Render, Docker, CI/CD) + **PDF-to-Markdown (T11)**.
  - Estado: ✅ COMPLETADA (897 tests, 100% TDD).

## Roadmap
- ☑ Spec 001: Chat Fiscal MVP (671 tests, 100% TDD).
- ☑ Spec 002: Interfaz de Usuario y Autenticación (686 tests, 100% TDD).
- ☐ Spec 003: Autenticación Real (eliminar `scope_admin_key`).
- ☑ Spec 004: Dockerización y Deployment + Ingesta PDF (897 tests, 100% TDD).
- ☐ Spec 005: Ingesta dinámica de PDFs (Gacetas/Leyes).

## Comandos Disponibles
- `/commit`: Commitea y pushea.
- `/test`: Corre pytest.
- `/sdd-status`: Estado de la spec activa.
- `/trace`: Ejecuta el smoke test y muestra el link a LangSmith.
- `/keel-status`: Fase activa de la spec y artefactos faltantes.
- `/keel-goldfish`: Auditoría EGM a ciegas (delega a @reviewer).
- `/keel-drift`: Control de deriva spec↔código (delega a @reviewer).
