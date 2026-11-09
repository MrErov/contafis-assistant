---
template: spec
version: 0.1.1
phase: 1
consumed_by: [01-sdd-spec-writer, 02-egm-goldfish-audit, 03-tdd-cycle, 05-drift-control]
estimated_tokens_to_fill: 2000-4000
max_lines: 120
---

# SPEC: `US-001-api-fail-closed` — Arranque fail-closed sin `API_SHARED_SECRET` en producción

## 1. [M] User Story

**As a** operador del servicio,
**I want** que el servicio rechace arrancar cuando `API_SHARED_SECRET` no está configurado y `APP_ENV=production`, y que en los demás entornos emita un warning pero continúe *salvo* cuando `API_HOST` no sea loopback (p. ej. `0.0.0.0`), en cuyo caso también falle por la regla existente de host público sin secreto,
**so that** un despliegue mal configurado en producción no quede abierto al público sin que nadie lo note.

**Bounded context:** `api` (reference: `docs/context/api.md`); la validación de arranque vive en `config.py`.

**Explicit out of scope:**
- Autenticación de usuario (sesión Odoo/token legacy), nuevos mecanismos o roles.
- `/livez` (ya exento), lógica de negocio o shapes de `/chat`, `/scope`, `/health`, `/digitize`.
- Rate limiting, auditoría, refactor de `ApiKeyGuard`/`verify_api_key` o checks por request.

---

## 2. [M] Acceptance Criteria

| ID | Given (context) | When (action) | Then (observable result) |
|----|-----------------|---------------|--------------------------|
| AC-1 | `APP_ENV=production`, `API_SHARED_SECRET` ausente, vacío o solo espacios; `ODOO_READONLY_ATTESTED=true`; `API_HOST=127.0.0.1` | Se construye `Settings(_env_file=None)` o se invoca `create_app()` sin `Settings` explícito | El arranque falla con `pydantic.ValidationError` cuyo mensaje nombra `API_SHARED_SECRET`; no se sirve ninguna request |
| AC-2 | `APP_ENV=production`, `API_SHARED_SECRET` con valor no vacío; `ODOO_READONLY_ATTESTED=true` | Se construye `Settings(...)` y se crea la app | Arranca sin error y sin warning de secreto ausente; el secreto queda disponible para `ApiKeyGuard` |
| AC-3 | `APP_ENV` es `development`, `test` o no está definido (default `development`); `API_SHARED_SECRET` ausente; `API_HOST=127.0.0.1` | Se construye `Settings(...)` y se crea la app | Construye sin error, emite al menos un warning en **stdout** que nombra `API_SHARED_SECRET` y el `app_env` efectivo, y el servicio continúa |
| AC-4 | `APP_ENV=development`, `API_HOST=0.0.0.0`, `API_SHARED_SECRET` ausente | Se construye `Settings(...)` | Sigue lanzando `ValidationError` por la regla existente de host no loopback; no se abre el servicio sin secreto |

---

## 3. [M] Interface Contracts

### 3.1 Input contract

```
Settings(_env_file=None) -> Settings; create_app(settings: Settings | None = None) -> FastAPI
APP_ENV: Literal["development", "test", "production"] = "development"  # campo: Settings.app_env; default si ausente
API_SHARED_SECRET: str | unset — vacío/solo espacios -> None (validador existente)
API_HOST: str = "127.0.0.1" — no loopback sigue exigiendo secreto en todos los entornos
ODOO_READONLY_ATTESTED: bool = True  # inyectado por tests como mock; en producción requiere configuración real
```

Responses: `OK` = `Settings` construido / app creada; `FAIL` = `pydantic.ValidationError` (producción sin secreto, o host no loopback sin secreto).

### 3.2 Output contract / side effects

- `production` sin secreto: `pydantic.ValidationError` con `API_SHARED_SECRET` en el mensaje; `Settings()`/`create_app()` abortan antes de servir.
- `production` con secreto: `Settings()` construye; el secreto llega a `ApiKeyGuard` sin cambios.
- Otros entornos sin secreto (validaciones existentes en verde): warning en **stdout** (no stderr) nombrando `API_SHARED_SECRET` y `app_env`; nunca se imprime el secreto; `Settings()` construye y el servicio continúa.
- Sin cambios en endpoints, shapes, exención de `/livez`, modos de auth ni checks por request.

---

## 4. [M] Test Plan (TDD seed)

| Test ID | Covered AC | Type | Short description |
|---------|------------|------|-------------------|
| T-1 | AC-1 | unit | `APP_ENV=production`, `API_SHARED_SECRET` sin definir, `API_HOST=127.0.0.1`, `ODOO_READONLY_ATTESTED=true` → `Settings(_env_file=None)` lanza `ValidationError` cuyo mensaje contiene `API_SHARED_SECRET` |
| T-2 | AC-1 | unit | Igual que T-1 con secreto `""` y `"   "` (parametrizado) → mismo fallo; cubre la normalización a `None` |
| T-3 | AC-1 | integration | Producción sin secreto, `cwd` temporal sin `.env`, `ODOO_READONLY_ATTESTED=true` → `create_app()` lanza `ValidationError` antes de devolver la app |
| T-4 | AC-2 | unit | `APP_ENV=production`, secreto `"s3cr3t"`, `ODOO_READONLY_ATTESTED=true` → `Settings` construye; `capsys` no captura warning de secreto ausente en stdout; el valor se conserva |
| T-5 | AC-2 | integration | `create_app(Settings(... production + secreto ...))` → app creada; `app.state.settings.api_shared_secret` coincide y `GET /health` sin `X-API-Key` devuelve 401 |
| T-6 | AC-3 | unit | Parametrizado `APP_ENV` en {`development`, `test`, sin definir} × secreto en {ausente, `""`, `"   "`}, loopback → `Settings` construye; `capsys` captura warning en **stdout** con `API_SHARED_SECRET` y `app_env`; verifica que el warning se emite también cuando el secreto es string vacío o solo espacios |
| T-7 | AC-3 | integration | `APP_ENV=development`, secreto ausente, loopback → `create_app()` devuelve app; `capsys` captura el warning y `GET /livez` sin `X-API-Key` responde 200 |
| T-8 | AC-4 | unit | `APP_ENV=development`, `API_HOST=0.0.0.0`, secreto ausente → `ValidationError` por la regla existente de host no loopback |

**Expected initial state:** T-1, T-2, T-3, T-6 y T-7 en **RED**; T-4, T-5 y T-8 son guards de regresión y deben permanecer **GREEN**.

---

## 5. [O] Dependencies and Risks

- **Depends on:** `Settings.app_env`, `Settings.api_shared_secret`, el validador `_empty_secret_is_none` y el patrón de `_validate_startup`.
- **Main risk:** la validación existente de `API_HOST` no loopback ya exige secreto en todos los entornos; puede enmascarar la regla nueva o impedir el "continuar" en desarrollo con `API_HOST=0.0.0.0`.
- **Mitigation:** preservar esa validación (AC-4); aislar los tests con `API_HOST=127.0.0.1`; documentar que en no-producción solo se continúa si las validaciones existentes pasan.

---

## 7. [M] Exit Gate (self-check before Goldfish Audit)

| Gate | Criterion | Status |
|------|-----------|--------|
| G1 | User Story in As-a/I-want/So-that format | [x] |
| G2 | Explicit out-of-scope list declared | [x] |
| G3 | 3–7 verifiable acceptance criteria | [x] |
| G4 | Every AC has at least one test in the plan | [x] |
| G5 | Interface contracts complete or "N/A" justified | [x] |
| G6 | Total length ≤ 120 lines | [x] |
| G7 | No sections added outside this template | [x] |
| G8 | Section 8 metadata fully filled | [x] |

**If any gate fails, the spec is NOT sent to Goldfish Audit.**
Return to the Navigator with the failing gate and the reason.

---

## 8. [M] Traceability Metadata

```yaml
us_id: US-001-api-fail-closed
spec_version: 0.1.1
created_by: 01-sdd-spec-writer
created_at: 2026-11-09
status: certified
goldfish_audit: specs/US-001-api-fail-closed.audit.2.md
drift_report: specs/US-001-api-fail-closed.drift.1.md
```
