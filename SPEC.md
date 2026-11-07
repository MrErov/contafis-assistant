# Proyecto: ContaFis AI Assistant 🧠📊
Asistente fiscal conversacional basado en RAG para contadores venezolanos.

## Stack
- Python 3.10+ / FastAPI
- LangChain / LangGraph / LangSmith
- NVIDIA NIM (OpenAI-compatible)
- ChromaDB (Vector Store)
- Odoo 19 (ERP externo vía JSON/2 API)

## Flujo de Desarrollo (Híbrido Pragmático)
- **SDD (Spec-Driven)**: Specs acotadas en `specs/NNN-nombre/` antes de codear.
- **TDD (Test-Driven)**: Tests primero (en rojo), luego código. 841 tests actuales.
- **ODD (Observation-Driven)**: Trazas visuales en LangSmith para validar alucinaciones.

## Índice de Especificaciones
- [001-chat-fiscal](specs/001-chat-fiscal/spec.md) — MVP del Asistente (Conversacional + RAG + Odoo).
  - Estado: ✅ COMPLETADA (671 tests, 59 AC, 48 acceptance/security tests).
- [002-ui-auth](specs/002-ui-auth/spec.md) — Interfaz de Usuario y Autenticación (Streamlit + FastAPI).
  - Estado: ✅ COMPLETADA (686 tests, 125 AC, UI tests verdes).
- [004-deployment](specs/004-deployment/spec.md) — Dockerización y Deployment (Render, Docker, CI/CD).
  - Estado: ✅ COMPLETADA (841 tests, 100% TDD).

## Roadmap
- ☑ Spec 001: Chat Fiscal MVP (671 tests, 100% TDD).
- ☑ Spec 002: Interfaz de Usuario y Autenticación (686 tests, 100% TDD).
- ☐ Spec 003: Autenticación Real (eliminar `scope_admin_key`).
- ☑ Spec 004: Dockerización y Deployment (841 tests, 100% TDD).
- ☐ Spec 005: Ingesta dinámica de PDFs (Gacetas/Leyes).

## Comandos Disponibles
- `/commit`: Commitea y pushea.
- `/test`: Corre pytest.
- `/sdd-status`: Estado de la spec activa.
- `/trace`: Ejecuta el smoke test y muestra el link a LangSmith.
