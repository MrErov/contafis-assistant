# ContaFis AI Assistant 🧠📊

Asistente fiscal inteligente basado en RAG (Retrieval-Augmented Generation) para el ecosistema ContaFis (Odoo 19).

Permite a los contadores consultar su estado de cumplimiento fiscal, vencimientos y normativas usando lenguaje natural.

## Estado del Proyecto: ✅ MVP Funcional (Spec 001 Completa)

**Spec 001 — Chat Fiscal MVP** cerrada formalmente con **671 tests** (100% TDD, sin red, sin tokens reales, sin servicios externos).

| Métrica | Valor |
|---|---|
| Tests totales | 671 (670 passed + 1 skipped) |
| Tests de aceptación (E2E) | 23 |
| Tests de seguridad (auditoría) | 24 |
| Criterios de Aceptación (AC) | 59 / 59 cubiertos |
| Tareas TDD completadas | T01–T10 (10/10) |
| Cobertura de matriz de estados | 9/9 estados (§8.3) |

### Capacidades implementadas
- **Consultas ERP read-only** vía Odoo JSON/2: obligaciones, clientes, compañías, documentos/cartelera, alertas
- **RAG híbrido** (Chroma + BM25 en memoria, RRF k=60, top-5) con corpus normativo venezolano (ISLR, IVA, SENIAT)
- **Orquestación LangGraph** con presupuesto de 3 tool calls/turno, source-aware, sin alucinaciones
- **Multi-company** con agregación por defecto y filtro explícito por entidad nombrada
- **Zona horaria** `America/Caracas` para todos los cálculos de negocio
- **Seguridad**: redacción automática de PII (RIF, email, teléfono, nombres, documentos), defensa anti-prompt injection, allowlist cerrada sin métodos de escritura
- **Observabilidad**: LangSmith habilitado en desarrollo con metadata-only (sin PII), deshabilitado en tests/CI
- **Arranque seguro**: validación de configuración, `scope_check` con fail-fast en producción, health endpoint operativo

## API

El servicio se ejecuta en un único proceso. No uses más de un worker: la memoria de conversaciones, el caché de alcance y el corpus viven en el proceso.

```powershell
uvicorn contafis_assistant.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Para operación local o detrás de un reverse proxy, mantené `API_HOST=127.0.0.1`. Si configurás un bind remoto sin `API_SHARED_SECRET`, la aplicación no arranca.

- `POST /chat` recibe `{"message": "...", "thread_id": "..."}`. Los pedidos inválidos responden HTTP 200 con estado `invalid_request`.
- `GET /health` devuelve el estado agregado de alcance, corpus, Chroma/BM25 y Odoo. Responde HTTP 503 solo cuando el runtime es inseguro en producción.
- Si `API_SHARED_SECRET` está definido, todas las rutas exigen el header `X-API-Key`.

## Ejecución de Tests

```powershell
# Suite completa (671 tests, ~4s)
pytest

# Solo tests de aceptación (E2E + seguridad)
pytest tests/acceptance/

# Solo tests unitarios y de integración
pytest tests/unit/ tests/integration/ tests/api/
```

## Comandos Disponibles

- `/commit`: Commitea y pushea (solo si tests pasan)
- `/test`: Corre pytest
- `/sdd-status`: Estado de la spec activa
- `/trace`: Ejecuta el smoke test y muestra el link a LangSmith

## Próximas Especificaciones (Roadmap)

1. **Spec 002**: Interfaz de Usuario (Streamlit o Web)
2. **Spec 003**: Autenticación Real (eliminar `scope_admin_key`, multi-usuario)
3. **Spec 004**: Ingesta dinámica de PDFs (Gacetas/Leyes, OCR, versionado)