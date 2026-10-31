# Tasks — Spec 005: Autenticación por Session ID de Odoo

> Checklist ejecutable. Marcar `[x]` al completar. Orden TDD: tests primero (F5), luego implementación (F2–F4), luego tests API (F6), validación final (F7).

---

## F1: Preparación y configuración

- [x] **T01** — Añadir `session_max_age_hours` a `Settings` (`src/contafis_assistant/config.py`)
- [x] **T02** — Crear `tests/unit/test_session_auth.py`
- [x] **T03** — Crear `tests/api/test_session_auth.py`
- [x] **T04** — Añadir mocks de `get_session_info` a `tests/api/conftest.py`

## F2: Core — OdooClient con soporte de cookie

- [x] **T05** — Modificar `OdooClient.__init__`: parámetro `session_id: str | None`, `api_key` opcional si hay session
- [x] **T06** — Modificar `OdooClient._headers()`: `Cookie` si session, `Authorization` si api_key
- [x] **T07** — Tests unitarios `test_odoo_client_headers_with_session` y `test_odoo_client_headers_without_session` (TDD)

## F3: Auth — Nueva dependencia `get_user_context_from_session`

- [x] **T08** — Test unitario `test_validate_session_success` (TDD)
- [x] **T09** — Test unitario `test_validate_session_expired` (TDD)
- [x] **T10** — Test unitario `test_validate_session_invalid` (TDD)
- [x] **T11** — Test unitario `test_validate_session_odoo_error` (TDD)
- [x] **T12** — Implementar `_validate_session()` en `api/user_auth.py`
- [x] **T13** — Implementar `_legacy_user_context()` extrayendo lógica actual
- [x] **T14** — Actualizar `UserContext` dataclass: `auth_mode`, `session_uid`, `session_company_id`
- [x] **T15** — Implementar `get_user_context_from_session` generator
- [x] **T16** — Manejo de errores: 401 (auth/expirada), 503 (transitorio)

## F4: Integración

- [x] **T17** — Actualizar `api/routes.py`: `Depends(get_user_context_from_session)`
- [x] **T18** — Verificar `main.py`: `clock` y `settings` accesibles desde `request.app.state.runtime`
- [x] **T19** — Verificar `ScopeService` usa `client.uid` y `allowed_company_ids` de sesión

## F5: Tests unitarios (ya escritos en F2–F3, verificar que pasan)

- [x] **T20** — `pytest tests/unit/test_session_auth.py -v` → verde

## F6: Tests de API

- [x] **T21** — `test_chat_with_valid_session` (TDD)
- [x] **T22** — `test_chat_with_expired_session` (TDD)
- [x] **T23** — `test_chat_with_invalid_session` (TDD)
- [x] **T24** — `test_chat_with_legacy_token` (TDD)
- [x] **T25** — `test_chat_without_auth` (TDD)
- [x] **T26** — `test_session_priority_over_token` (TDD)
- [x] **T27** — `pytest tests/api/test_session_auth.py -v` → verde

## F7: Validación y regresión

- [x] **T28** — `pytest tests/api/test_user_auth.py -v` → verde (legacy intacto)
- [x] **T29** — `pytest tests/api/ tests/integration/ tests/acceptance/ -v` → verde (suite completa)
- [x] **T30** — `/tdd-check` (script de validación TDD) → pasa

---

## Notas de implementación

### Mock de `get_session_info` en `tests/api/conftest.py`

```python
def mock_session_info_success(uid=7, company_id=1, allowed_company_ids=None, last_activity=None):
    """Mock transport handler para get_session_info exitoso."""
    if allowed_company_ids is None:
        allowed_company_ids = [1, 2]
    if last_activity is None:
        from datetime import datetime, timedelta
        last_activity = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    
    def handler(request, attempt):
        if "/web/session/get_session_info" in str(request.url):
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "uid": uid,
                    "is_system": False,
                    "is_admin": False,
                    "user_context": {
                        "lang": "es_VE",
                        "tz": "America/Caracas",
                        "uid": uid,
                        "allowed_company_ids": allowed_company_ids,
                        "company_id": company_id,
                        "partner_id": 12
                    },
                    "session_id": "test-session-id",
                    "last_activity": last_activity
                }
            })
        # ... resto de handlers para scope_check
    return handler
```

### Endpoint de validación de sesión

La llamada a `/web/session/get_session_info` es un JSON-RPC 2.0:
- Method: POST
- URL: `{odoo_url}/web/session/get_session_info`
- Headers: `Content-Type: application/json`, `Cookie: session_id=<value>`, `X-Odoo-Database: <db>`
- Body: `{"jsonrpc": "2.0", "method": "call", "params": {}}`

### Expiración 24h

```python
from datetime import datetime, timedelta

def _is_session_expired(last_activity_str: str, max_age_hours: int, clock: Clock) -> bool:
    last = datetime.strptime(last_activity_str, "%Y-%m-%d %H:%M:%S")
    # last_activity está en UTC (Odoo guarda en UTC)
    age = clock.now() - last.replace(tzinfo=timezone.utc)
    return age > timedelta(hours=max_age_hours)
```

---

## Referencias rápidas

- Spec: `specs/005-session-auth/spec.md`
- Plan: `specs/005-session-auth/plan.md`
- Archivos a modificar:
  - `src/contafis_assistant/config.py`
  - `src/contafis_assistant/odoo/client.py`
  - `src/contafis_assistant/api/user_auth.py`
  - `src/contafis_assistant/api/routes.py`
  - `src/contafis_assistant/main.py`
  - `tests/api/conftest.py`
  - `tests/unit/test_session_auth.py` (nuevo)
  - `tests/api/test_session_auth.py` (nuevo)