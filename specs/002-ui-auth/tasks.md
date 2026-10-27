# Tareas 002 — Interfaz de Usuario y Autenticación por Token de Usuario

- **Spec:** `specs/002-ui-auth/spec.md`
- **Plan:** `specs/002-ui-auth/plan.md`
- **Máximo:** 10 tareas.
- **Regla:** cada tarea empieza por tests que fallan (TDD). No se avanza a la siguiente con tests rojos.
- **Prohibido en toda la implementación:** red real, tokens reales, Odoo real, OpenAI real, LangSmith real, archivos temporales fuera de `tmp_path`, escrituras en Odoo, secretos hardcodeados y mostrar/registrar el token de usuario.
- **Verificaciones de API ya hechas (no inventar):** `st.testing.v1.AppTest` vive dentro del paquete `streamlit` (el paquete PyPI `streamlit-testing` no existe). API usada: `AppTest.from_file(path, default_timeout=...)`, `.run()`, `at.session_state`, `at.chat_input[0].set_value(...)`, `at.form[0]` / `at.form_submit_button[0].click()`, `at.text_input[0].input(...)`, `at.error[0].value`, `at.sidebar.button[0].click()`, `at.chat_message[0]`, `at.exception` vacío. La UI usa `httpx.request(...)` (top-level) como único seam HTTP.

## Resumen y orden

| Orden | Tarea | Depende de |
|---|---|---|
| 1 | T01 — Header `X-User-Token`, cliente Odoo por request y errores 401/503 | — |
| 2 | T02 — Alcance por token con cache TTL/LRU y endpoint `GET /scope` | T01 |
| 3 | T03 — Turno con token: deps por request, gate de alcance y memoria segmentada | T01, T02 |
| 4 | T04 — Settings y salud sin probe (`ODOO_API_KEY` opcional) | T01 |
| 5 | T05 — Migración de la suite de Spec 001 (contrato intacto) | T01–T04 |
| 6 | T06 — UI: login y logout con API key | T02, T05 |
| 7 | T07 — UI: chat, render de respuesta y errores amigables | T06 |
| 8 | T08 — UI: panel de alcance y chips de compañía | T06, T07 |
| 9 | T09 — Observabilidad: `user_key` en trazas LangSmith | T03, T05 |
| 10 | T10 — Cierre: deps, `.env.example`, README y auditoría de aceptación | T01–T09 |

---

## T01 — Header `X-User-Token`, cliente Odoo por request y errores 401/503

**Objetivo:** que `/chat` y `/scope` exijan el token de usuario, construyan un cliente Odoo por request con `Bearer <token>` y devuelvan 401/503 amigables sin filtrar datos internos.

**Tests primero (rojo):**

- `tests/api/test_user_auth.py`
  - `POST /chat` sin header → 401 con `detail="Falta el token de usuario (X-User-Token)."` y cero llamadas a Odoo.
  - Header vacío o solo espacios → 401 (mismo detalle).
  - Token que Odoo rechaza (`context_get` → 401) → 401 con `detail="El token de Odoo no es válido o fue revocado."`; el payload no contiene el token ni la URL de Odoo.
  - Odoo con timeout/5xx en el scope check → 503 `service_unavailable`.
  - Token válido → el cliente del turno envía `Authorization: Bearer <token del usuario>` y `X-Odoo-Database` (capturado con `MockTransport`).
  - El cliente se cierra al terminar el request (transport fake cuenta `close`).
  - `X-API-Key` inválido sigue dando 401 antes que el header de usuario.

**Implementación:**

- `src/contafis_assistant/api/user_auth.py`: `USER_TOKEN_HEADER = "X-User-Token"`, `UserContext` (dataclass), `get_user_context` como generator dependency con `yield`/`finally: client.close()`; recorte del token; constantes de mensajes 401/503.
- `src/contafis_assistant/api/routes.py`: `POST /chat` y `GET /scope` con `Depends(get_user_context)`; rutas sincrónicas (`def`).
- Transporte inyectable para tests vía `runtime.user_transport`.

**Hecho cuando:**

- [x] `pytest tests/api/test_user_auth.py` pasa sin red real (solo `MockTransport`/fakes).
- [x] Los asserts de 401/503 usan los textos exactos de la spec §8.4 y ningún payload incluye token, URL interna ni cuerpo de error de Odoo.
- [x] Existe un test que verifica que el cliente Odoo del request se cierra (sin fugas de transporte).
- [x] El orden de compuertas (middleware `X-API-Key` → `X-User-Token` → validez en Odoo) está cubierto por tests.

**Trazabilidad:** RF-101, RF-102, RF-103; RNF-102; AC-101, AC-102, AC-103; EC-101.

---

## T02 — Alcance por token con cache TTL/LRU y endpoint `GET /scope`

**Objetivo:** verificar el alcance de cada token con la clasificación de Spec 001, cachearlo por `user_key` y exponer las compañías visibles en `GET /scope`.

**Tests primero (rojo):**

- `tests/unit/test_user_scope.py`
  - `user_key_for` es SHA-256 hex determinista; dos tokens distintos → claves distintas; el token no aparece en la clave.
  - Cache: segundo `get_or_check` dentro del TTL no ejecuta RPC (transport fake cuenta requests).
  - Cache: expirado por TTL → re-chequeo; LRU con `USER_SCOPE_CACHE_MAX` expulsa la entrada menos usada.
  - `companies_for_ids`: dominio allowlisted `[("id","in",ids)]`, campos `id`/`name`; fallo → `[]` sin romper.
  - `gate_for_snapshot`: réplica de §8.3 (unsafe producción → 503; unsafe no-interno → sin tools; admin/atestación dev/test → `scope_admin_key`; partial → normal).
- `tests/api/test_scope_endpoint.py`
  - `GET /scope` con token válido y alcance completo → 200 con `uid`, `companies` (id+name ordenados), `scope_state=scope_ok`, `scope_complete=true`, `tools_enabled=true`.
  - Alcance parcial → `companies` = subconjunto visible; `scope_partial`, `scope_complete=false`.
  - Sin token → 401; token inválido → 401; Odoo caído → 503.
  - Payload valida contra `ScopeInfo` Pydantic (`extra="forbid"`).

**Implementación:**

- `src/contafis_assistant/domain/contracts.py`: `CompanyRef`, `ScopeInfo`.
- `src/contafis_assistant/odoo/user_scope.py`: `user_key_for`, `UserScopeCache` (lock, TTL desde `SCOPE_CHECK_TTL_SECONDS`, LRU `USER_SCOPE_CACHE_MAX`), `companies_for_ids`, `gate_for_snapshot`.
- `routes.py`: `GET /scope` devolviendo `ScopeInfo`.

**Hecho cuando:**

- [x] `pytest tests/unit/test_user_scope.py tests/api/test_scope_endpoint.py` pasa.
- [x] El cache almacena solo `user_key`/snapshots (test que afirma que el token crudo no está en ninguna entrada inspeccionable).
- [x] `classify_scope` de Spec 001 se reutiliza sin modificaciones (diff mínimo; sin duplicar la lógica de clasificación).
- [x] El segundo turno con el mismo token dentro del TTL ejecuta cero RPC de scope adicionales.

**Trazabilidad:** RF-104, RF-105, RF-107, RF-111; RNF-105; AC-104, AC-105, AC-106, AC-107, AC-109, AC-110; EC-102, EC-104.

---

## T03 — Turno con token: deps por request, gate de alcance y memoria segmentada

**Objetivo:** ejecutar el turno con las tools y el resolvedor de entidades ligados al cliente del usuario, aplicando el gate de alcance y aislando la memoria de hilos por `user_key`.

**Tests primero (rojo):**

- `tests/integration/test_user_token_turn.py`
  - Turno completo con token de usuario válido: `ChatResponse.status=ok`; los RPC de Odoo llevan `Authorization: Bearer <token del usuario>` y `context.allowed_company_ids` del usuario (fakes de Odoo + LLM).
  - Usuario no interno → turno `partial`, `tools_used=[]`, sin `scope_admin_key`.
  - Admin/atestación falsa en dev/test → turno con warning `scope_admin_key` y tools habilitadas.
  - Producción + snapshot `scope_unsafe` → 503 sin ejecutar tools.
  - `scope_partial` → turno normal con warning `scope_partial`.
- `tests/unit/test_user_stores.py`
  - Dos `user_key` distintos con el mismo `thread_id`: sin cruce de historial/entidad/filtro; `thread_id` devuelto intacto.
  - LRU con `USER_STORES_MAX`: al expulsar, el usuario recrea store limpio sin error.

**Implementación:**

- `src/contafis_assistant/memory/user_stores.py`: `UserThreadStores` (registry LRU con lock; `get(user_key) -> ThreadStore`).
- `src/contafis_assistant/main.py`: `ChatRuntime.chat_with_user(...)` (gate vía `gate_for_snapshot`, warnings reusando `_scope_warnings`, `_degraded_turn(..., store=...)`, `GraphDependencies` reconstruido con tools/entity_resolver/store del usuario) y `run_turn(..., user_key=...)`.
- `GraphDependencies` compartido: classifier, planner, clock, límites, answer_composer, tracer.

**Hecho cuando:**

- [x] `pytest tests/integration/test_user_token_turn.py tests/unit/test_user_stores.py` pasa sin red.
- [x] Ningún RPC del turno usa `ODOO_API_KEY`; test que falla si el header `Authorization` no es el token del usuario.
- [x] El grafo, el presupuesto de tools y la precedencia de estados no cambian (diff de `graph/` limitado a `run_turn`/`user_key`).
- [x] Los stores de usuario se bound por `USER_STORES_MAX` con test de expulsión.

**Trazabilidad:** RF-102, RF-105, RF-106, RF-110; RNF-005; AC-103, AC-105, AC-106, AC-107, AC-108; EC-106.

---

## T04 — Settings y salud sin probe (`ODOO_API_KEY` opcional)

**Objetivo:** permitir arrancar sin API key global (modo token de usuario) con salud coherente, manteniendo el comportamiento de Spec 001 cuando la key existe.

**Tests primero (rojo):**

- `tests/unit/test_config.py` (ampliación)
  - `ODOO_API_KEY` ausente → settings válidos; nuevos `user_scope_cache_max=500`, `user_stores_max=50`.
  - `ODOO_API_KEY` presente → validaciones existentes sin cambios.
- `tests/api/test_startup.py`, `tests/api/test_health.py` (ampliación)
  - Arranque sin probe: lifespan OK, `ready=true`, sin RPC de scope.
  - `/health` sin probe → 200 `degraded`, `scope_state=null`, `scope_complete=false`, `odoo_available=false`; campos de Spec 001 presentes.
  - `/health` sin probe en producción → 200 `degraded` (no 503).
  - Con probe y `scope_unsafe` en producción → fail-fast al arrancar (Spec 001 intacto).

**Implementación:**

- `config.py`: `odoo_api_key: SecretStr | None = None`, `user_scope_cache_max`, `user_stores_max`.
- `main.py`: `build_runtime` con cliente ausente (`odoo_client=None`, `scope=None`); `probe_enabled`; ajuste quirúrgico de `health()` y `startup()`.

**Hecho cuando:**

- [x] Los tests ampliados pasan y la suite de Spec 001 correspondiente sigue verde.
- [x] Sin probe, el arranque no ejecuta ningún RPC (transport fake cuenta cero requests).
- [x] `/health` no expone token ni `user_key`; mantiene exactamente los campos de Spec 001.
- [x] Con probe, el fail-fast de producción por `scope_unsafe` se conserva (test existente sigue pasando).

**Trazabilidad:** RF-108, RF-112, RF-113, RF-114; RNF-103; AC-111; EC-102.

---

## T05 — Migración de la suite de Spec 001 (contrato intacto)

**Objetivo:** adaptar los tests de Spec 001 al nuevo régimen de auth de `/chat` sin tocar contratos, estados ni el grafo.

**Tests primero (rojo):**

- `tests/api/test_chat_api.py` (ampliación)
  - Request sin `X-User-Token` → 401 (nuevo comportamiento explícito).
  - Request con header + runtime de usuario → mismos asserts de contrato/estados que antes (202/`invalid_request`/`thread_id`).
- `tests/conftest.py`: fixture de runtime de usuario con transporte inyectable.
- `tests/integration/test_tracing.py`: `TraceEvent` sin `user_key` sigue emitiendo el mismo payload (default `None`).

**Implementación:**

- Ajuste de tests (no de producción salvo fixtures): helper `client_with_user_token` en `tests/conftest.py`; actualización de llamadas a `/chat`.
- Verificación de que `ChatRequest`/`ChatResponse`, `ToolResult`, estados y precedencia no cambian (diff de `domain/` = solo `contracts.py` aditivo).

**Hecho cuando:**

- [x] La suite completa de Spec 001 migrada pasa en verde.
- [x] Existe un test que documenta el 401 sin header (AC-101) y otro que confirma que el contrato de respuesta no cambió (AC-125).
- [x] Ningún archivo de `specs/001-chat-fiscal/` fue modificado.
- [x] `git diff --stat` de `src/` en esta tarea muestra solo cambios de test/fixtures más lo aditivo autorizado.

**Trazabilidad:** RF-110; AC-125; §14 de la spec.

---

## T06 — UI: login y logout con API key

**Objetivo:** la pantalla de bienvenida con formulario de token, la carga en `session_state` sin validación client-side y el cierre de sesión con rerun.

**Tests primero (rojo):**

- `tests/ui/conftest.py`
  - `ui_env` (monkeypatch `ASSISTANT_API_URL`, `UI_API_TIMEOUT_SECONDS`, `API_SHARED_SECRET`), `fake_api` (monkeypatch de `httpx.request` con cola de respuestas en memoria y registro de llamadas), `open_app()` (AppTest de `app.py` con `default_timeout` alto).
- `tests/ui/test_app_login.py`
  - Estado inicial: `at.form` existe; `at.text_input` de tipo password existe; **no** existe `at.chat_input`; `fake_api.calls == []`.
  - Envío con token → `at.session_state["user_token"] == token`; `thread_id` UUID4 generado; **cero** llamadas HTTP (sin validación client-side).
  - Envío con token vacío/espacios → no se guarda token; sigue el formulario.
  - 401 del backend al cargar el panel → `at.error[0].value` amigable; token borrado; visible el formulario de login.
  - Click "Cerrar Sesión" (sidebar) → token borrado; formulario visible de nuevo; `at.chat_input` ausente.
  - Ningún elemento renderizado contiene el token (`at.markdown`, `at.error`, `at.caption`).

**Implementación:**

- `app.py`: `st.set_page_config`, `_init_state()`, rama de login con `st.form("login_form")` + `st.text_input("Odoo API Token", type="password", key="token_input")` + `st.form_submit_button`; carga en `session_state` (`user_token`, `thread_id=uuid4`, `messages=[]`).
- Rama de chat mínima (placeholder de panel/chat) para que el flujo login→chat sea navegable.
- Botón "Cerrar Sesión" en sidebar: borra claves de sesión + `st.rerun()`.

**Hecho cuando:**

- [x] `pytest tests/ui/test_app_login.py` pasa con `at.exception` vacío en todos los casos.
- [x] Los tests afirman cero requests HTTP al enviar el formulario (AC-114).
- [x] El 401 borra el token y vuelve al login con mensaje amigable (AC-117 en su parte de login).
- [x] El token no aparece en ningún elemento renderizado ni en los registros de `fake_api` (AC-124).

**Trazabilidad:** RF-120, RF-121, RF-122, RF-123, RF-124, RF-129, RF-132; RNF-106, RNF-107; AC-113, AC-114, AC-116, AC-124; EC-101.

---

## T07 — UI: chat, render de respuesta y errores amigables

**Objetivo:** enviar mensajes al backend con los headers correctos, renderizar `answer`/warnings/fuentes, reutilizar el `thread_id` y mapear errores sin perder la sesión.

**Tests primero (rojo):**

- `tests/ui/test_app_chat.py`
  - Login + turno: `fake_api` recibe `POST {ASSISTANT_API_URL}/chat` con headers `X-User-Token` (y `X-API-Key` cuando hay secreto), body `{message, thread_id}`; respuesta 200 → `at.chat_message` con el `answer`; `st.caption` con el `status`; segundo turno reutiliza el mismo `thread_id` (AC-118, AC-122).
  - Respuesta con `warnings` y `sources` → se renderizan (documento + página) (AC-120).
  - Respuesta 200 con `status=invalid_request` → se renderiza como mensaje del asistente (EC-112).
  - Mensaje vacío/solo espacios → no se envía request (AC-123).
  - `ChatResponse` inválido (JSON basura) → `st.error` controlado, sin excepción.
- `tests/ui/test_app_errors.py`
  - 503 → `st.error` amigable; token permanece; chat visible (AC-121).
  - `httpx.TimeoutException`/`httpx.ConnectError` → `st.error` amigable de red; sesión mantenida (EC-108).
  - 401 en `/chat` (no en `/scope`) → `st.error` + token borrado + login (AC-117).
  - Mensaje que excede `MAX_MESSAGE_CHARS` → backend `invalid_request` → render como asistente (EC-112).

**Implementación:**

- `app.py`: `_api_post` con `httpx.request("POST", url, headers=..., json=..., timeout=...)`; `_friendly_error` (tabla §8.4); `_compose_message`; render de turnos; historial en `st.session_state["messages"]`; validación con `ChatResponse.model_validate`.

**Hecho cuando:**

- [x] `pytest tests/ui/test_app_chat.py tests/ui/test_app_errors.py` pasa.
- [x] Los tests afirman el header `X-User-Token` en cada request y el cuerpo `{message, thread_id}` (AC-118).
- [x] El `thread_id` no cambia entre turnos de la misma sesión y se descarta al hacer logout (AC-122, RF-126).
- [x] Ningún camino de error deja `at.exception` poblado ni muestra detalles internos (URL, token, cuerpos crudos).

**Trazabilidad:** RF-124, RF-125, RF-126, RF-128, RF-130, RF-131, RF-132; RNF-101; AC-117, AC-118, AC-120, AC-121, AC-122, AC-123; EC-108, EC-112.

---

## T08 — UI: panel de alcance y chips de compañía

**Objetivo:** mostrar las compañías visibles del token en el sidebar y permitir acotar el próximo mensaje con un chip.

**Tests primero (rojo):**

- `tests/ui/test_app_scope_panel.py`
  - Login + `/scope` 200 → sidebar muestra los nombres de `companies`; `st.session_state["scope_info"]` cacheado; una sola llamada a `/scope` tras varios turnos.
  - Click en chip → próximo mensaje enviado con prefijo `Compañía <nombre>: ` (assert del body capturado) (AC-119).
  - Desactivar chip → el mensaje siguiente va sin prefijo.
  - `/scope` con `scope_partial` → se muestran solo las compañías visibles y un aviso de alcance incompleto.
  - `/scope` 503 → aviso no bloqueante en sidebar; chat sigue operativo (EC-102).
  - Más de 20 compañías → se muestran 20 chips + texto "y N más" (EC-105).
  - Botón "Refrescar" → nueva llamada a `/scope` y actualización del `ScopeInfo` en sesión (EC-110).
  - Sin compañías visibles → panel sin chips y mensaje explicativo (EC-103).

**Implementación:**

- `app.py`: carga de `ScopeInfo` al entrar (cacheada en `session_state`, botón "Refrescar"); `_scope_chip` (máx. 20 + "y N más"); chips con `st.button(key=...)`; chip activo visible con control de desactivado; `_compose_message(chip, prompt)`.

**Hecho cuando:**

- [x] `pytest tests/ui/test_app_scope_panel.py` pasa.
- [x] El mensaje compuesto con chip respeta exactamente el formato `Compañía <nombre>: <texto>` (AC-119).
- [x] El panel hace una llamada a `/scope` por sesión salvo refresco explícito (RNF-105).
- [x] La UI no decide alcance propio: solo renderiza `ScopeInfo` del backend.

**Trazabilidad:** RF-127, RF-107, RF-111 (consumo); RNF-105; AC-115, AC-119; EC-103, EC-105, EC-110.

---

## T09 — Observabilidad: `user_key` en trazas LangSmith

**Objetivo:** identificar los turnos por usuario pseudónimo en LangSmith sin filtrar el token ni PII.

**Tests primero (rojo):**

- `tests/unit/test_tracing_user.py`
  - `TraceEvent(user_key="abc")` → payload contiene `user_key`; `TRACE_FIELDS` lo incluye.
  - `trace_turn_state(..., user_key=...)` deriva el evento con el `user_key` recibido.
  - El payload no contiene el token crudo (ni como subcadena) para un token de prueba.
- `tests/integration/test_tracing.py` (ampliación)
  - Turno con token de usuario → el sink fake recibe exactamente un evento con `user_key` = SHA-256 del token y sin `message`/resultados.

**Implementación:**

- `observability/tracing.py`: `TraceEvent.user_key: str | None = None`, `as_payload()`, `TRACE_FIELDS`, `trace_turn_state(..., user_key=None)`.
- `graph/build.py`: `run_turn(..., user_key: str | None = None)` propagando a `trace_turn_state`.
- `main.py`: `chat_with_user` pasa `user_key`.

**Hecho cuando:**

- [x] `pytest tests/unit/test_tracing_user.py tests/integration/test_tracing.py` pasa.
- [x] Los tests existentes de tracing siguen verdes (default `None` no rompe el payload).
- [x] Un test adversario confirma que el token de usuario no aparece en el payload emitido.
- [x] LangSmith permanece deshabilitado en tests/CI (sin cliente real).

**Trazabilidad:** RF-109; RNF-104; AC-112.

---

## T10 — Cierre: deps, `.env.example`, README y auditoría de aceptación

**Objetivo:** dejar el repositorio operable y verificable: dependencias, variables, documentación y auditoría de trazabilidad AC↔test.

**Tests primero (rojo):**

- `tests/acceptance/test_ui_auth_acceptance.py`
  - Matriz parametrizada AC-101..AC-125 que ejecuta los flujos clave (401 sin token, 401 token inválido, turno con Bearer del usuario, cache de scope, panel/chips, chat render, logout, trazas con `user_key`) usando los fakes ya construidos.
  - Test de auditoría: cada AC de la spec tiene al menos un test que lo referencia por nombre/marcador.
- Verificación estática:
  - `requirements.txt` contiene `streamlit` (y **no** `streamlit-testing`).
  - `.env.example` contiene las variables nuevas sin secretos.
  - `app.py` no contiene llamadas a Odoo ni imports de `contafis_assistant.odoo` (la UI solo habla HTTP).

**Implementación:**

- `requirements.txt` (+ `streamlit`), `.env.example` (`USER_SCOPE_CACHE_MAX`, `USER_STORES_MAX`, `ASSISTANT_API_URL`, `UI_API_TIMEOUT_SECONDS`), `README.md` (generar API key personal en Odoo 19, `streamlit run app.py`, `uvicorn ... --workers 1`, variables de UI, nota de `ODOO_API_KEY` opcional).

**Hecho cuando:**

- [x] `pytest tests/acceptance/test_ui_auth_acceptance.py` y la suite completa pasan (001 migrada + 002).
- [x] La auditoría de trazabilidad no encuentra AC sin prueba.
- [x] `requirements.txt`/`.env.example`/README actualizados y sin secretos; `streamlit-testing` ausente.
- [x] Verificación manual documentada: login → chat → logout contra backend local con token real de solo lectura (registro en el cierre de aceptación, fuera de CI).

**Trazabilidad:** AC-101..AC-125; RNF-103, RNF-106; §15/§18 de la spec.
