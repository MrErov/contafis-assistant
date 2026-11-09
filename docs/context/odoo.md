# CONTEXT: odoo

## Purpose
Integración read-only con Odoo 19 vía JSON/2 para consultar compañías, clientes, obligaciones, documentos y alertas fiscales del ERP.

## Responsibilities
- Validar cada RPC contra una allowlist de modelos, métodos, campos y dominios (`MODEL_POLICIES`).
- Ejecutar RPC JSON/2 con presupuesto por turno y contexto de compañías permitidas.
- Clasificar el alcance del usuario (`ScopeService`/`ScopeSnapshot`) y deshabilitar tools si el alcance es inseguro.
- Exponer tools de solo lectura para obligaciones, clientes, documentos, alertas, compañías y resolución de entidades.
- Traducir errores del ERP a excepciones tipadas.

## Public interface
- `OdooClient` — ejecuta `RpcCall` validados; `ToolBudget` limita las llamadas por turno.
- `ScopeService`, `ScopeSnapshot`, `ScopeReason`, `classify_scope` — alcance, gating y snapshot cacheado.
- `validate_rpc`, `has_company_filter`, `MODEL_POLICIES`, `RpcCall` — allowlist de modelos/campos/dominios.
- `query_obligations`, `query_clients`, `query_documents`, `query_alerts`, `query_companies`, `resolve_entity`, `sanitize_alert_message`.
- `OdooError`, `OdooAuthError`, `OdooTransientError`, `OdooConcurrencyError`, `OdooResponseError`, `InvalidToolArgs`, `NoBudgetError`.

## Dependencies
- Internal: `domain` (contratos, enums, tiempo).
- External: `httpx` (JSON/2), Odoo 19 (servidor externo), `pydantic`, `rapidfuzz` (resolución de entidades).

## Persistence / State
- Odoo PostgreSQL (externo, read-only) — fuente de verdad; este contexto no escribe en el ERP.
- `ScopeService` cachea `ScopeSnapshot` con TTL (`scope_check_ttl_seconds`, por defecto 300 s).
- `OdooClient` mantiene en memoria `allowed_company_ids`, `uid` y el lock de serialización de RPC.

## Commands
- Build: `pip install -r requirements.txt`
- Test: `pytest tests/unit/test_odoo_client.py tests/unit/test_odoo_allowlist.py tests/unit/test_scope.py tests/unit/test_entities.py tests/unit/test_documents.py tests/unit/test_obligations.py tests/unit/test_alerts.py tests/unit/test_clients_companies.py tests/integration/test_erp_tools.py -q`
- Run: `uvicorn contafis_assistant.main:app --app-dir src --host 0.0.0.0 --port 8000` (el contexto no tiene entrypoint propio)

## Gotchas / Observed tech debt
- `allowlist.py` (>450 líneas) concentra políticas de modelos, validación de dominios y filtros de compañía.
- `tools/documents.py` (>500 líneas) es el tool más grande del contexto.
- `OdooClient` serializa los RPC con un lock no bloqueante; el presupuesto se agota por turno.
- `ScopeSnapshot.tools_enabled` permite tools en desarrollo con API key admin/attestation insegura, pero las bloquea en producción.
- `classify_scope` es una función pura; el I/O y el cache viven en `ScopeService`.

## Last updated
2026-11-09 (skill 0.1.0)
