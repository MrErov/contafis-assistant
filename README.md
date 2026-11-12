# ContaFis AI Assistant 🤖📊

> Asistente fiscal conversacional con RAG y LangGraph para el ecosistema ContaFis.
> Demo pública de la arquitectura. El código de producción está disponible bajo NDA.

[![Tests](https://img.shields.io/badge/Tests-927+-brightgreen)]()
[![Python](https://img.shields.io/badge/Python-3.10+-blue)]()
[![LangChain](https://img.shields.io/badge/LangChain-1.x-green)]()
[![LangGraph](https://img.shields.io/badge/LangGraph-1.x-orange)]()
[![Keel Nav](https://img.shields.io/badge/Keel%20Nav-SDD%20%2B%20TDD%20%2B%20ODD-purple)]()
[![License](https://img.shields.io/badge/License-Proprietary-red)]()

## 🎯 ¿Qué es?

ContaFis AI Assistant es el "cerebro" del ecosistema ContaFis. Permite a contadores
venezolanos consultar su estado fiscal conversando en lenguaje natural, conectando:
- **Odoo 19** como fuente de datos (ERP).
- **RAG sobre normativas** (SENIAT, ISLR, IVA) con ChromaDB.
- **LangGraph** como orquestador de agentes.
- **LangSmith** como observabilidad.

## ⚙️ Metodología: Keel Nav (SDD + TDD + ODD)

Este proyecto se desarrolla bajo **Keel Nav**, una metodología híbrida de 5 fases
con trazabilidad completa spec → tests → código:

| Fase | Meta-skill | Artefacto |
|------|------------|-----------|
| 0 — Context Bootstrap | `00-context-bootstrap.md` | `docs/context/` |
| 1 — SDD Spec + EGM Audit | `01-sdd-spec-writer.md` + `02-egm-goldfish-audit.md` | `specs/NNN-*/spec.md`, `*.audit.*.md` |
| 2 — TDD Cycle | `03-tdd-cycle.md` | Tests rojos → verdes |
| 3 — ODD Vertical Slice | `04-odd-vertical-slice.md` | Slice E2E + observación humana |
| 4 — Drift Control | `05-drift-control.md` | `*.drift.*.md` |

> **Regla dura**: "No test, no feature". El subagente `@reviewer` es la única autoridad
> para auditorías y veredictos de deriva.

## 🏗️ Arquitectura

```
+------------------+         +------------------+         +------------------+
|  Odoo 19 (ERP)   |<------->|  FastAPI Backend |<------->|  LangGraph Agent |
|  Bot en Discuss  |  JSON-2 |  927+ tests TDD (927 passed, 14 skipped)  |         |  RAG + Tool Call |
+------------------+         +------------------+         +------------------+
                                                                    |
                                                                    v
                                                          +------------------+
                                                          |  ChromaDB (RAG)  |
                                                          |  LangSmith       |
                                                          +------------------+
```

## 🔬 AI Pipeline: PDF → Markdown → ChromaDB → LLM Composer

```
Gacetas/Leyes (PDF) --> pdf-to-md (Spec 004 T11) --> Markdown -->
Chunking + Embeddings --> ChromaDB (dense + BM25) -->
RAG Retriever (RRF fusion) --> Answer Composer (LLM + grounding)
```

## ✨ Características

- 🧠 **Clasificador de intenciones**: enruta cada pregunta a ERP, RAG o mixto (modelo rápido dedicado: nemotron-3.5-bolt-e4b).
- 🔍 **RAG híbrido**: búsqueda semántica + BM25 sobre normativas fiscales con fusión RRF (k=60).
- 📝 **Answer Composer con grounding**: LLM redacta respuesta usando SOLO fragmentos recuperados; `validate_grounding` descarta alucinaciones (cae a determinista).
- 🛡️ **Seguridad**: redacción de PII, defensa contra prompt injection, tokens por usuario, fail-closed en producción.
- 📊 **Observabilidad**: trazabilidad completa en LangSmith (habilitado por defecto en desarrollo).
- ✅ **927+ tests (927 passed, 14 skipped)**: TDD estricto con cobertura de casos límite y acceptance/security tests. *Conteo reportado por la suite pytest; el badge puede variar según inclusiones de acceptance/security.*
- 🐳 **Dockerizado**: ver [Runbook de despliegue](docs/DEPLOY.md).

## 📚 Documentación técnica

- [Especificaciones SDD](specs/) — Spec-Driven Development
- [Constitución del proyecto](docs/constitution.md) — 6 principios innegociables
- [Guía del agente](AGENTS.md) — Reglas para desarrollo con IA (incluye Keel Nav Workflow)
- [Runbook de despliegue](docs/DEPLOY.md) — Staging, producción, rollback, observabilidad
- [Meta-skills Keel Nav](.meta-skills/) — 5 fases formales con presupuestos de tokens

## 🔒 Acceso al código completo

Este repositorio es una **versión pública de demostración**. Los prompts del
sistema, la lógica de clasificación y la configuración de producción están
redactados (no eliminados) y disponibles bajo NDA para:

- Clientes interesados en implementación.
- Reclutadores técnicos en proceso de evaluación.
- Partners comerciales.

**Contacto**: eurickramiro.ospinovelasquez@gmail.com

## 🌐 Ecosistema ContaFis

- 🏢 **[Módulo Odoo 19](https://github.com/MrErov/contafis-odoo)** — Gestión fiscal, calendario SENIAT, ISLR, conciliación.
- 🤖 **ContaFis AI Assistant** (este repo) — Asistente conversacional con IA.

## 👤 Autor

**Eurick Ospino** — Backend & AI Engineer
[GitHub](https://github.com/MrErov) · [LinkedIn](https://www.linkedin.com/in/eurick-ospino-18a75a134/)

## 📄 Licencia

**Proprietary — All Rights Reserved**. Ver [LICENSE](LICENSE).