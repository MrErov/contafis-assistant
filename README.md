# ContaFis AI Assistant 🤖📊

> Asistente fiscal conversacional con RAG y LangGraph para el ecosistema ContaFis.
> Demo pública de la arquitectura. El código de producción está disponible bajo NDA.

[![Tests](https://img.shields.io/badge/Tests-680+-brightgreen)]()
[![Python](https://img.shields.io/badge/Python-3.10+-blue)]()
[![LangChain](https://img.shields.io/badge/LangChain-1.x-green)]()
[![License](https://img.shields.io/badge/License-Proprietary-red)]()

## 🎯 ¿Qué es?

ContaFis AI Assistant es el "cerebro" del ecosistema ContaFis. Permite a contadores 
venezolanos consultar su estado fiscal conversando en lenguaje natural, conectando:
- **Odoo 19** como fuente de datos (ERP).
- **RAG sobre normativas** (SENIAT, ISLR, IVA) con ChromaDB.
- **LangGraph** como orquestador de agentes.
- **LangSmith** como observabilidad.

## 🏗️ Arquitectura

```
┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│  Odoo 19 (ERP)   │◄───────►│  FastAPI Backend │◄───────►│  LangGraph Agent │
│  Bot en Discuss  │  JSON-2 │  680+ tests TDD  │         │  RAG + Tool Call │
└──────────────────┘         └──────────────────┘         └──────────────────┘
                                                                    │
                                                                    ▼
                                                          ┌──────────────────┐
                                                          │  ChromaDB (RAG)  │
                                                          │  LangSmith       │
                                                          └──────────────────┘
```

## ✨ Características

- 🧠 **Clasificador de intenciones**: enruta cada pregunta a ERP, RAG o mixto.
- 🔍 **RAG híbrido**: búsqueda semántica + BM25 sobre normativas fiscales.
- 🛡️ **Seguridad**: redacción de PII, defensa contra prompt injection, tokens por usuario.
- 📊 **Observabilidad**: trazabilidad completa en LangSmith.
- ✅ **680+ tests**: TDD estricto con cobertura de casos límite.
- 🐳 **Dockerizado**: listo para desplegar.

## 📚 Documentación técnica

- [Especificaciones SDD](specs/) — Spec-Driven Development
- [Constitución del proyecto](docs/constitution.md) — 6 principios innegociables
- [Guía del agente](AGENTS.md) — Reglas para desarrollo con IA

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