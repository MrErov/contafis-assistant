# Plan 002 — Interfaz de Usuario y Autenticación por Token de Usuario

- **Spec fuente:** `specs/002-ui-auth/spec.md` (RF-101..RF-132, RNF-101..108, AC-101..AC-125, EC-101..EC-112).
- **Spec base:** `specs/001-chat-fiscal/plan.md` (arquitectura, módulos y estrategia de tests; este plan solo añade y modifica lo que la Spec 002 supersede, §14 de la spec).
- **Estado:** listo para implementación por tareas (máximo 10).
- **Objetivo:** autenticar cada request de `/chat` con la API key personal del usuario (`X-User-Token`), inyectar un cliente Odoo por request, verificar alcance por token con cache, segmentar la memoria de hilos por usuario, y entregar la UI Streamlit `app.py` con login, chat, panel de alcance y logout.
- **Alcance de este documento:** arquitectura, archivos, funciones puras, contratos, estrategia de pruebas y orden de implementación. No contiene código de producción.

## 1. Principios de diseño

1. **El ERP manda también en identidad:** el alcance de cada turno lo determina el token del usuario vía `ir.rule` + `scope_check`; la UI nunca simula ni cachea alcance propio.
2. **Stateless en el borde:** el token viaja por header en cada request; no hay cookies, JWT propios ni estado de sesión en el backend.
3. **Un cliente Odoo por request:** nace del token, se cierra al terminar la respuesta; nunca se reutiliza entre usuarios.
4. **Secretos nunca persisten:** el token vive en `st.session_state` (frontend) y en la pila del request (backend); los caches guardan solo `user_key` (SHA-256).
5. **Contrato intacto:** `ChatRequest`/`ChatResponse`, estados, precedencia y grafo de Spec 001 no cambian; la auth es una compuerta previa.
6. **Fallos amigables:** 401 → login con mensaje claro; 503/red → mensaje claro con sesión intacta; nunca un traceback ni un dato interno en pantalla.
7. **Tests sin red ni tokens reales:** UI con `AppTest` + monkeypatch de `httpx.request`; backend con `TestClient` + `httpx.MockTransport`; LLM/embeddings fake.

## 2. Arquitectura lógica

```text
Streamlit app.py (proceso UI, puerto 8501)
  st.form login ──► st.session_state["user_token"] + thread_id=uuid4()
  GET /scope  (X-User-Token, X-API-Key?) ───────────────┐
  POST /chat  (X-User-Token, X-API-Key?, {message,thread_id}) │
                                                          ▼
FastAPI (proceso API, puerto 8000, 1 worker)
  middleware X-API-Key (Spec 001, si API_SHARED_SECRET)
  Depends get_user_context(x_user_token: Header "X-User-Token")
    ├─ falta/vacío ───────────────► 401
    ├─ user_key = sha256(token)
    ├─ OdooClient(url, db, api_key=token, transport=opcional)   [por request, se cierra al responder]
    ├─ UserScopeCache.get_or_check(user_key, client)
    │    ├─ hit fresco ──────────► ScopeSnapshot + companies
    │    └─ miss ─────────────────► ScopeService.refresh(force=True)
    │         └─ OdooAuthError ──► 401 amigable
    │         └─ error transitorio ► 503
    └─ yield UserContext(token, user_key, client, snapshot, companies)

  POST /chat
    └─ gate de alcance (producción unsafe → 503; unsafe no-interno → partial sin tools)
    └─ deps por request: tools + entity_resolver con client del usuario
    └─ ThreadStore del usuario (UserThreadStores[user_key])
    └─ run_turn(..., scope_snapshot=snapshot, user_key=user_key)   [grafo Spec 001 intacto]
    └─ trace_turn_state(..., user_key=user_key)                    [LangSmith metadata-only]
    └─ ChatResponse (Spec 001)

  GET /scope ──► ScopeInfo(uid, companies, scope_state, scope_complete, tools_enabled)

  GET /health ──► como Spec 001; probe opcional (ODOO_API_KEY ausente ⇒ degraded)
```

- **Sin CORS:** la UI llama al backend server-side (proceso Streamlit → FastAPI); el navegador nunca habla con la API.
- **Sin estado global mutable nuevo** salvo `UserScopeCache` y `UserThreadStores`, ambos con lock.
- **Funciones puras:** `user_key_for(token)`, clasificación de alcance (reusada), gate de alcance por snapshot, mapeo HTTP→mensaje UI, composición de mensaje con chip, validación de `ScopeInfo`/`CompanyRef`.

## 3. Estructura de archivos

### 3.1 Archivos nuevos

```text
app.py                                      # UI Streamlit single-file (login, chat, sidebar, logout)
src/contafis_assistant/api/user_auth.py     # header X-User-Token, UserContext, dependencia, 401/503
src/contafis_assistant/odoo/user_scope.py   # user_key_for, UserScopeCache (TTL + LRU), companies
src/contafis_assistant/memory/user_stores.py# UserThreadStores (registry LRU por user_key)
tests/api/test_user_auth.py                 # AC-101..AC-103, AC-105..AC-108 (gate + dependencia)
tests/api/test_scope_endpoint.py            # AC-109, AC-110 (GET /scope)
tests/ui/conftest.py                        # fake de httpx.request, builder de AppTest, env de test
tests/ui/test_app_login.py                  # AC-113, AC-114, AC-116, AC-117(login), AC-124
tests/ui/test_app_chat.py                   # AC-118, AC-119, AC-120, AC-122, AC-123
tests/ui/test_app_errors.py                 # AC-117, AC-121 (503/timeout/invalid_request)
tests/ui/test_app_scope_panel.py            # AC-115, AC-119, EC-103/105/110 (panel + chips)
tests/integration/test_user_token_turn.py   # AC-103, AC-104, AC-108 (turno completo con token)
tests/unit/test_user_scope.py               # RF-104 (cache TTL/LRU, sin token crudo)
tests/unit/test_user_stores.py              # RF-106 (aislamiento por user_key)
tests/unit/test_tracing_user.py             # AC-112 (user_key en traza, sin token)
```

### 3.2 Archivos modificados (edición quirúrgica)

| Archivo | Cambio |
|---|---|
| `src/contafis_assistant/config.py` | `odoo_api_key: SecretStr \| None = None`; nuevos `user_scope_cache_max`, `user_stores_max`; ajuste de validaciones (probe ausente no falla) |
| `src/contafis_assistant/domain/contracts.py` | añadir `CompanyRef` y `ScopeInfo` (Pydantic, `extra="forbid"`) |
| `src/contafis_assistant/observability/tracing.py` | `TraceEvent.user_key`, `TRACE_FIELDS`, `trace_turn_state(..., user_key=)` |
| `src/contafis_assistant/graph/build.py` | `run_turn(..., user_key: str \| None = None)` y propagación a `trace_turn_state` |
| `src/contafis_assistant/main.py` | `ChatRuntime` gana `user_transport`, `chat_with_user(...)`, `_user_deps(...)`, `_degraded_turn(..., store=...)`, `probe_enabled`, ajuste de `health()`, `build_runtime` con cliente ausente |
| `src/contafis_assistant/api/routes.py` | `POST /chat` y `GET /scope` usan `Depends(get_user_context)`; rutas sincrónicas (`def`) para correr en threadpool |
| `requirements.txt`, `.env.example`, `README.md` | `streamlit`; variables nuevas; comandos de arranque UI |
| `tests/api/*`, `tests/conftest.py`, `tests/unit/test_config.py`, `tests/integration/test_tracing.py` | migración §14 de la spec (header en `/chat`, probe ausente, `TraceEvent.user_key` default) |

## 4. Contratos y modelos Pydantic

### 4.1 request/response del chat

Sin cambios: `ChatRequest`/`ChatResponse` de Spec 001 §8.1/§8.2 (RF-110). La UI valida la respuesta con `ChatResponse.model_validate_json`.

### 4.2 Modelos nuevos (frontera `/scope`)

**`CompanyRef`** (`extra="forbid"`):

| Campo | Tipo | Regla |
|---|---|---|
| `id` | `int` | `>= 1` |
| `name` | `str` | no vacío |

**`ScopeInfo`** (`extra="forbid"`):

| Campo | Tipo | Regla |
|---|---|---|
| `uid` | `int` | uid resuelto del token |
| `companies` | `list[CompanyRef]` | `user_company_ids` con nombre; orden por `id` |
| `scope_state` | `ScopeState` (enum de Spec 001) | `scope_ok`/`scope_partial`/`scope_unsafe` |
| `scope_complete` | `bool` | coherente con `scope_state is scope_ok` |
| `tools_enabled` | `bool` | del `ScopeSnapshot` |

### 4.3 `UserContext` (interno, dataclass)

| Campo | Tipo | Notas |
|---|---|---|
| `token` | `str` | solo en memoria del request; nunca en logs ni respuestas |
| `user_key` | `str` | SHA-256 hex del token |
| `client` | `OdooClient` | construido con el token; se cierra al terminar el request |
| `snapshot` | `ScopeSnapshot` | resultado del cache o del check fresco |
| `companies` | `list[CompanyRef]` | nombres visibles; `[]` si la consulta falló |
| `scope_service` | `ScopeService` | sobre el cliente del usuario (para refrescos puntuales) |

### 4.4 Dependencia FastAPI

- `get_user_context(request: Request, x_user_token: Annotated[str \| None, Header(alias="X-User-Token")] = None)` → generator dependency (`yield` + `finally: client.close()`).
- Lee `settings`/`runtime` de `request.app.state`; transporte inyectable desde `runtime.user_transport` (solo tests).
- Tokens se recortan (`.strip()`); vacío → 401.
- Se declara con `Depends` en rutas sincrónicas (`def`) para que el turno corra en threadpool.

## 5. Funciones puras y responsabilidades

| Módulo | Función | Entrada/salida | Regla |
|---|---|---|---|
| `api/user_auth.py` | `get_user_context` | header + app.state → `UserContext` | 401 sin token; 401 si Odoo rechaza; 503 si Odoo no responde |
| `odoo/user_scope.py` | `user_key_for` | token → SHA-256 hex | determinista; nunca expone el token |
| `odoo/user_scope.py` | `UserScopeCache.get_or_check` | (user_key, client, attested, app_env, ttl) → snapshot + companies | TTL + LRU con lock; sin token crudo |
| `odoo/user_scope.py` | `companies_for_ids` | client + ids → `list[CompanyRef]` | RPC allowlisted; fallo → `[]` |
| `odoo/user_scope.py` | `gate_for_snapshot` | snapshot + app_env → decisión tipada | réplica de las consecuencias de Spec 001 §8.3 por request |
| `memory/user_stores.py` | `UserThreadStores.get` | user_key → `ThreadStore` | LRU con `USER_STORES_MAX`; lock |
| `app.py` | `_compose_message` | chip + texto → mensaje final | `f"Compañía {chip}: {texto}"` o texto |
| `app.py` | `_friendly_error` | status HTTP/tipo de excepción → mensaje es-VE | tabla §8.4 de la spec; sin detalles internos |
| `app.py` | `_scope_chip` | `ScopeInfo` → chips (máx. 20 + "y N más") | EC-105 |

## 6. Backend — Fase 1 (detalle)

### 6.1 Dependencia y ciclo de vida

1. `X-API-Key` (middleware existente) → 401 si aplica.
2. `X-User-Token` ausente/vacío → 401 (`detail` fijo).
3. Construir `OdooClient(url=settings.odoo_url, database=settings.odoo_db, api_key=token, timeout=settings.odoo_timeout_seconds, transport=runtime.user_transport)`.
4. `UserScopeCache`: `user_key_for(token)`; si hay entrada fresca → reusar snapshot/companies (cerrando el cliente igualmente al final del request); si no → `ScopeService(client, app_env, attested, ttl, clock).refresh(force=True)` y `companies_for_ids`.
   - `OdooAuthError` → 401 amigable.
   - `OdooTransientError`/`OdooResponseError`/`OdooConcurrencyError` → 503 `service_unavailable`.
   - `classify_scope` reusada sin cambios (Spec 001 RF-062): no-interno, admin, atestación, cobertura.
5. `yield UserContext`; `finally`: `client.close()` (nunca en el camino feliz antes de responder).

### 6.2 Turno con token (`ChatRuntime.chat_with_user`)

- `gate_for_snapshot`: producción + `scope_unsafe` → `ServiceUnavailable` (503, sin ejecutar tools).
- `snapshot.tools_enabled=false` → `_degraded_turn(..., store=store_del_usuario)` → `partial` sin tools (misma respuesta canónica de Spec 001).
- `deps = self.deps.model_copy(update={tools=..., entity_resolver=..., store=...})` (dataclas; `model_copy` no existe en dataclass → se reconstruye `GraphDependencies` con los campos compartidos: classifier, planner, clock, límites, answer_composer, tracer).
- `run_turn(message, deps=deps, thread_id=..., request_id=..., scope_snapshot=snapshot, warnings=..., user_key=user_key)`.
- Warnings de alcance (`scope_partial`/`scope_admin_key`) se emiten igual que en `ChatRuntime.chat` (reusar `_scope_warnings`).

### 6.3 `GET /scope`

- Misma dependencia. Respuesta `ScopeInfo` validada. 401/503 iguales. `companies` del cache o frescas.

### 6.4 Health y arranque sin probe

- `Settings.odoo_api_key` opcional; `build_runtime` con key ausente → `odoo_client=None`, `scope=None`.
- `ChatRuntime.probe_enabled = odoo_client is not None`.
- `health()`: `unhealthy` solo si hay probe y refresca a unsafe en producción; sin probe → `degraded` (200) con `scope_state=null`, `scope_complete=false`, `odoo_available=false`.
- `startup()` con probe ausente no ejecuta scope check ni falla (el fail-fast de producción solo aplica al probe).

## 7. Frontend Streamlit — Fase 2 (detalle)

### 7.1 `app.py` (single-file)

- `st.set_page_config` + título; **sin** `st.navigation`, sin multipágina, sin BD, sin componentes externos.
- Estado de sesión (claves exactas, para tests): `user_token`, `thread_id`, `messages`, `scope_info`, `scope_loading`, `scope_error`, `scope_chip`, `auth_error`, `chip_refresh_nonce`.
- `_init_state()` al inicio: define las claves ausentes.
- Rama de login (`"user_token" not in session_state`): muestra `auth_error` si existe; `st.form("login_form")` con `st.text_input("Odoo API Token", type="password", key="token_input")` y `st.form_submit_button("Iniciar sesión")`; al enviar con token recortado no vacío → grabar token, `thread_id=str(uuid.uuid4())`, `messages=[]`, limpiar chip/scope; **cero requests** (RF-121).
- Rama de chat:
  - Sidebar: `ScopeInfo` (cacheado en sesión al entrar; botón "Refrescar"); chips de compañías (máx. 20 + "y N más"); chip activo visible con botón de desactivar; botón "Cerrar Sesión" (borra todo + `st.rerun()`).
  - Historial: `for m in messages: st.chat_message(m["role"])...`.
  - `prompt = st.chat_input("Escribe tu consulta")`; si hay → `message = _compose_message(chip, prompt)`; si vacío tras componer → no enviar (RF-132).
  - POST vía `_api_post("/chat", payload)`; 200 → `ChatResponse.model_validate`; append user/assistant a `messages`; render de `answer` + `st.caption(status)` + warnings + fuentes.
  - 401 → `_friendly_error` + borrar token + `st.rerun()` (RF-124). 503/timeout/red → `st.error` sin borrar (RF-130).
  - 200 con `status=invalid_request` → se renderiza como mensaje del asistente (EC-112).
- Config por entorno (`os.getenv`): `ASSISTANT_API_URL`, `UI_API_TIMEOUT_SECONDS`, `API_SHARED_SECRET` (opcional).
- Cliente HTTP: `httpx.request("POST", url, headers=..., json=..., timeout=...)` (API top-level; sin cliente global). Excepciones: `httpx.HTTPError` → error amigable de red.
- Headers por request: `X-User-Token`, `X-API-Key` (si `API_SHARED_SECRET`), `Content-Type`.
- Prohibido: mostrar el token, escribir logs con el token, guardar el token fuera de `session_state`.

### 7.2 Render de respuesta

- `answer` en `st.chat_message("assistant")` (markdown).
- `st.caption(f"Estado: {status}")`.
- Warnings: `st.caption("⚠ " + w.message)` por cada uno (o `st.warning` para el primero).
- `sources`: lista "Fuentes:" con `document` + `page`/`section`/`locator`.
- `options` (aclaración): nombres como texto (sin chips interactivos en el MVP).

## 8. Integración y pruebas — Fase 3 (detalle)

### 8.1 Estrategia pytest

- `tests/ui/conftest.py`: fixture `ui_env` (monkeypatch de `ASSISTANT_API_URL`, `UI_API_TIMEOUT_SECONDS`, `API_SHARED_SECRET`), fixture `fake_api` que monkeyparchea `httpx.request` con una cola de respuestas `httpx.Response` construidas en memoria (request real para `.json()`), y helper `run_login(at)`.
- Bloqueo de red existente (`tests/conftest.py`) se mantiene: cualquier socket real falla el test.
- UI: `AppTest.from_file(<repo>/app.py, default_timeout=10)`; asserts sobre `at.form`, `at.text_input`, `at.form_submit_button`, `at.session_state`, `at.chat_input`, `at.chat_message`, `at.error`, `at.sidebar.button`, `at.exception` (debe estar vacío salvo tests de error).
- Backend: `TestClient(create_app(settings, runtime=build_runtime(settings, odoo_client=None, corpus=fake_corpus, classifier=fake)))`; el transporte del cliente de usuario se inyecta con `httpx.MockTransport` vía `runtime.user_transport` (o parámetro de `build_runtime`).
- Integración turno completo: fake de Odoo JSON/2 (context_get/has_group/search_read/read/search_count), LLM fake, corpus fake; asserts sobre headers `Authorization: Bearer <token de usuario>` y sobre `ChatResponse`.

### 8.2 Pruebas de contrato

- `ScopeInfo`/`CompanyRef`: campos obligatorios, `extra="forbid"`, `companies` vacío permitido, `scope_complete` coherente.
- Headers: `X-User-Token` canónico (case-insensitive en HTTP; constante `USER_TOKEN_HEADER = "X-User-Token"`).
- Errores: payloads exactos de §8.4 (sin token ni URL).

### 8.3 Migración de la suite de Spec 001 (§14 de la spec)

- Actualizar tests de API para enviar el header (o esperar 401), lifespan/startup/health sin probe, `test_config.py` con la key opcional y settings nuevos, `test_tracing.py` con `user_key` default.
- Criterio de cierre: la suite completa (001 migrada + 002) en verde.

## 9. Observabilidad — Fase 4 (detalle)

- `TraceEvent` gana `user_key: str | None = None`; `as_payload()` lo incluye; `TRACE_FIELDS` lo acepta.
- `trace_turn_state(tracer, state, latency_ms=..., thread_id=..., user_key=...)`.
- `run_turn(..., user_key=...)` lo propaga; `chat_with_user` lo pasa.
- Redacción: `redact_payload` existente se aplica igual; `user_key` es un hash, no PII. Test: payload contiene `user_key` esperado y **no** contiene el token crudo (ni como subcadena).
- `LANGSMITH_HIDE_INPUTS/OUTPUTS` sin cambios (Spec 001).

## 10. Orden de implementación y dependencias

| Fase | Tarea(s) | Depende de | Entrega principal |
|---|---|---|---|
| 1 | T01, T02 | — | Dependencia de token, cliente por request, cache de alcance, `GET /scope` |
| 1 | T03, T04 | T01, T02 | Turno con token (deps por request, gate, memoria segmentada), settings/health sin probe |
| 1 | T05 | T01–T04 | Migración de la suite de Spec 001 + contrato intacto |
| 2 | T06, T07, T08 | T02, T05 | `app.py`: login/logout, chat + errores, panel de alcance + chips |
| 4 | T09 | T03, T05 | `user_key` en trazas |
| Cierre | T10 | T01–T09 | Suite completa, deps, `.env.example`, README, verificación manual |

**Justificación:** primero el borde autenticado y el alcance por token (es el riesgo de seguridad); después el turno con deps por request y la migración de tests; luego la UI sobre un contrato estable; al final observabilidad y cierre. Ninguna tarea requiere servicios reales.

## 11. Trazabilidad de aceptación por tarea

| Tarea | AC cubiertos |
|---|---|
| T01 | AC-101, AC-102 (dependencia + 401/503) |
| T02 | AC-104, AC-109, AC-110 (cache + `/scope`) |
| T03 | AC-103, AC-105, AC-106, AC-107, AC-108 (turno + gate + aislamiento) |
| T04 | AC-111 (health/settings sin probe) |
| T05 | AC-125 (migración + contrato intacto) |
| T06 | AC-113, AC-114, AC-116, AC-124 (login/logout) |
| T07 | AC-117, AC-118, AC-120, AC-121, AC-122, AC-123 (chat + errores) |
| T08 | AC-115, AC-119 (panel + chips) |
| T09 | AC-112 (trazas con `user_key`) |
| T10 | AC-101..AC-125 (auditoría final y cierre) |

La matriz se complementa con tests unitarios por RF. T10 falla si algún AC no tiene al menos una prueba que lo nombre.

## 12. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| El token del usuario se filtra a logs/trazas | `user_key` hash; tests que afirman ausencia del token en payloads y elementos renderizados |
| Cache de alcance sirve un snapshot viejo tras cambiar compañías en Odoo | TTL `SCOPE_CHECK_TTL_SECONDS` + botón "Refrescar" en el panel; test de frescura |
| Cliente Odoo por request sin cerrar (fuga de sockets) | `yield` + `finally: close()`; test con transporte fake que cuenta cierres |
| `/chat` sin fallback rompe tests de Spec 001 | Migración explícita (§14) como tarea dedicada |
| `st.rerun()` en `AppTest` se comporta distinto a producción | Verificado en fuente oficial de `AppTest`/`LocalScriptRunner` (rerun interno soportado); tests afirman el estado post-rerun |
| Monkeypatch de `httpx.request` insuficiente si la UI usa otro cliente | La UI usa exclusivamente `httpx.request` top-level; tests afirman cero sockets reales (bloqueo de red) |
| Producción sin probe key con health confuso | RF-112/RNF: `degraded` documentado; test de health sin probe |
| Memoria de hilos por usuario sin Bound | `USER_STORES_MAX` LRU; test de expulsión |

## 13. Definición de terminado del plan

1. `plan.md` y `tasks.md` existen; `spec.md` no se modificó durante la planificación.
2. Las 10 tareas respetan el máximo y cada una declara "Hecho cuando:".
3. Cada tarea empieza por tests que fallan (TDD), sin red ni tokens reales.
4. AC-101..AC-125 cubiertos por pruebas y suite completa (001 migrada + 002) en verde.
5. `requirements.txt`, `.env.example` y README actualizados; `streamlit` añadido; sin secretos.
6. Verificación manual documentada: `streamlit run app.py` + `uvicorn --workers 1` con un token real de solo lectura (login → chat → logout).
