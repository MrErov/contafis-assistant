# Plan de Implementación — Spec 005: Autenticación por Session ID de Odoo

## Fases

| Fase | Descripción | Tareas | Estado |
|---|---|---|---|
| **F1** | Preparación y configuración | T01–T04 | ✅ Completo |
| **F2** | Core: OdooClient con soporte de cookie | T05–T07 | ✅ Completo |
| **F3** | Auth: Nueva dependencia `get_user_context_from_session` | T08–T16 | ✅ Completo |
| **F4** | Integración: routes, main, settings | T17–T19 | ✅ Completo |
| **F5** | Tests unitarios (TDD primero) | T20 | ✅ Completo |
| **F6** | Tests de API | T21–T27 | ✅ Completo |
| **F7** | Validación y regresión | T28–T30 | ✅ Completo |

---

## Detalle de tareas

### F1: Preparación y configuración

**T01** — Añadir `session_max_age_hours` a `Settings` (`config.py`)
- Default: 24
- Validación: >= 1 ✅

**T02** — Crear estructura de tests
- `tests/unit/test_session_auth.py` ✅
- `tests/api/test_session_auth.py` ✅

**T03** — Definir mocks de `get_session_info` en `tests/api/conftest.py`
- `mock_session_info_success(uid, company_id, allowed_company_ids, last_activity)` ✅
- `mock_session_info_expired()` ✅
- `mock_session_info_invalid()` ✅
- `mock_session_info_odoo_error(status)` ✅

**T04** — Añadir mocks en `tests/api/conftest.py` para session auth ✅

---

### F2: Core — OdooClient con soporte de cookie

**T05** — Modificar `OdooClient.__init__` para aceptar `session_id: str | None`
- `api_key` opcional si `session_id` presente ✅
- Guardar `self._session_id` ✅

**T06** — Modificar `OdooClient._headers()` 
- Si `self._session_id`: retornar `{"Cookie": f"session_id={self._session_id}", ...}` SIN `Authorization` ✅
- Else: comportamiento actual (`Authorization: Bearer <api_key>`) ✅

**T07** — Tests unitarios `test_odoo_client_headers_with_session` y `test_odoo_client_headers_without_session` (TDD) ✅

---

### F3: Auth — Nueva dependencia `get_user_context_from_session`

**T08** — Test unitario `test_validate_session_success` (TDD) ✅
**T09** — Test unitario `test_validate_session_expired` (TDD) ✅
**T10** — Test unitario `test_validate_session_invalid` (TDD) ✅
**T11** — Test unitario `test_validate_session_odoo_error` (TDD) ✅

**T12** — Implementar `_validate_session()` en `api/user_auth.py`
- POST a `{odoo_url}/web/session/get_session_info` con cookie ✅
- Parsear respuesta JSON-RPC ✅
- Validar `result.uid` no nulo ✅
- Validar `last_activity` < `session_max_age_hours` horas ✅
- Retornar dict con `uid`, `company_id`, `allowed_company_ids`, `session_id` ✅

**T13** — Implementar `_legacy_user_context()` extrayendo lógica actual ✅

**T14** — Implementar `get_user_context_from_session` como generador FastAPI
- Prioridad: `X-Odoo-Session` > `X-User-Token` ✅
- 401 si ninguno ✅
- Yield `UserContext` con nuevos campos: `auth_mode`, `session_uid`, `session_company_id` ✅

**T15** — Actualizar `UserContext` dataclass con campos nuevos ✅

**T16** — Manejo de errores en validación de sesión
- `OdooAuthError` / `uid` nulo → 401 "Sesión de Odoo expirada o inválida." ✅
- `last_activity` > 24h → 401 "Sesión de Odoo expirada (24h)." ✅
- `OdooTransientError` / timeout / 5xx → 503 "service_unavailable" ✅

---

### F4: Integración

**T17** — Actualizar `api/routes.py`: cambiar `Depends(get_user_context)` → `Depends(get_user_context_from_session)` ✅

**T18** — Verificar `main.py`: `clock` y `settings` accesibles desde `request.app.state.runtime` ✅

**T19** — Verificar `ScopeService` usa `client.uid` y `allowed_company_ids` de sesión ✅

---

### F5: Tests unitarios

**T20** — `pytest tests/unit/test_session_auth.py -v` → 11 passed ✅

---

### F6: Tests de API

**T21** — `test_chat_with_valid_session` ✅
**T22** — `test_chat_with_expired_session` ✅
**T23** — `test_chat_with_invalid_session` ✅
**T24** — `test_chat_with_legacy_token` ✅
**T25** — `test_chat_without_auth` ✅
**T26** — `test_session_priority_over_token` ✅
**T27** — `pytest tests/api/test_session_auth.py -v` → 9 passed ✅

---

### F7: Validación y regresión

**T28** — `pytest tests/api/test_user_auth.py -v` → 9 passed (legacy intacto) ✅
**T29** — `pytest tests/api/ tests/integration/ tests/acceptance/ -v` → 162 passed ✅
**T30** — Suite completa: 677+ tests pasando (errores pre-existentes de permisos de temp dir) ✅

---

## Criterios de done por fase

| Fase | Done cuando |
|---|---|
| F1 | Settings actualizado, archivos de test creados, mocks en conftest ✅ |
| F2 | `OdooClient` acepta `session_id`, headers correctos en ambos modos, tests unitarios pasan ✅ |
| F3 | `get_user_context_from_session` funciona en ambos modos, maneja errores, tests unitarios pasan ✅ |
| F4 | Endpoints usan nueva dependencia, runtime provee clock/settings, scope_check OK ✅ |
| F5 | Tests unitarios cubren todos los AC de sección 11 (unit level) ✅ |
| F6 | Tests API cubren todos los AC de sección 11 (integration level) ✅ |
| F7 | Suite completa verde, validación TDD pasa ✅ |