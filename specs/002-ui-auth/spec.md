# Spec 002 — Interfaz de Usuario y Autenticación por Token de Usuario

| Campo | Valor |
|---|---|
| Estado | Borrador — pendiente de aprobación |
| Fecha | 2026-10-26 |
| Revisión | r1 |
| Autor | Planner SDD |
| Repo | `contafis-assistant` |
| Referencias | `docs/constitution.md`, `AGENTS.md`, `docs/memory.md`, `specs/001-chat-fiscal/spec.md` |
| Spec base | `specs/001-chat-fiscal/spec.md` (cerrada; esta spec la extiende y la supersede solo donde se indica en §14) |

> **Aviso de coexistencia:** `specs/001-chat-fiscal` sigue siendo la fuente de verdad para el dominio, el contrato `ChatRequest`/`ChatResponse`, el grafo LangGraph, el RAG, el alcance (`scope_check`) y los estados de respuesta. Esta spec **no reescribe** esos requisitos: los referencia. Solo supersede la autenticación de `/chat` (key global por entorno → token de usuario por request), hace `ODOO_API_KEY` opcional y añade la UI Streamlit y el endpoint `GET /scope`. La lista exacta de puntos superseded y los tests afectados están en §14.

---

## 1. Contexto

La Spec 001 entregó el asistente fiscal como API FastAPI sin interfaz: el backend hablaba con Odoo 19 por JSON/2 usando **una única API key global** (`ODOO_API_KEY`) y verificaba su alcance al arrancar. Eso validaba la orquestación y el RAG, pero dejaba el asistente inhabilitable para el caso real: un contador (o varios) queriendo conversar desde una pantalla, con su propia identidad de Odoo.

El repo de Odoo (`MrErov/contafis-odoo-19.0v`, Odoo 19) permite generar **API keys personales por usuario** que se envían como `Authorization: Bearer <token>` en JSON/2 y que **heredan las `ir.rule`** del usuario (cartera por `accountant_id`, compañías por `company_ids`). Es decir: la seguridad por registro ya existe en el ERP; el asistente solo necesita autenticar cada request con el token del usuario y respetar el alcance que ese token impone.

Esta spec define la primera interfaz del asistente: una app Streamlit de archivo único donde el usuario inicia sesión con su API key de Odoo, ve en un panel lateral las compañías que su token alcanza, y conversa. El backend deja de ser "mono-key": cada request a `/chat` viaja con el header `X-User-Token`, y FastAPI inyecta un cliente Odoo específico de ese token para el turno.

## 2. Objetivo y métricas de éxito

**Objetivo:** que un contador abra la app, inicice sesión con su API key de Odoo 19, vea su alcance real de compañías y mantenga una conversación en español sobre vencimientos, documentos, alertas y normativa, sin que el frontend conozca nada de Odoo y sin que un token inválido rompa la experiencia.

**Métricas de éxito:**

- 100% de los criterios de aceptación (§12) pasan en CI con mocks: sin red, sin tokens reales, sin Odoo real y sin LLM real.
- 0 tokens de usuario en logs, trazas LangSmith, respuestas HTTP o pantalla (RF-109, RF-129).
- 0 llamadas a Odoo desde el navegador o desde el frontend: todo el tráfico ERP sale del backend con el token del request.
- Un token inválido produce exactamente un `st.error` amigable y la vuelta al login; nunca un traceback ni un campo de token expuesto.
- El alcance mostrado en el panel lateral coincide con el `scope_check` del backend para el mismo token (misma clasificación `scope_ok`/`scope_partial`/`scope_unsafe`).
- La suite complete de Spec 001 (671 tests) sigue verde tras la migración de §14, y la suite nueva cubre los AC de esta spec.

## 3. Usuarios y casos de uso

**Usuario principal:** contador del despacho con API key personal de Odoo 19 (usuario interno, no admin, con una o varias compañías asignadas).
**Usuario secundario:** auxiliar contable con su propia key y alcance posiblemente más acotado.
**Fuera de alcance:** portal del contribuyente final, login con usuario/contraseña de Odoo, OAuth, roles propios del asistente y multi-tenant (varias bases Odoo).

| ID | Caso de uso | Ejemplo |
|---|---|---|
| UC-UI-01 | Iniciar sesión con API key | El contador pega su token de Odoo y entra al chat |
| UC-UI-02 | Ver alcance antes de preguntar | El panel lateral lista las compañías que su token alcanza |
| UC-UI-03 | Preguntar acotado a una compañía | Click en chip "Distribuidora El Sol" + "¿qué vence esta semana?" |
| UC-UI-04 | Conversar con seguimiento | "¿Y cuáles están vencidas?" reusa entidad/filtro del turno previo |
| UC-UI-05 | Recibir error amigable | Token revocado → mensaje claro y vuelta al login |
| UC-UI-06 | Cerrar sesión | Botón en sidebar → sin token → pantalla de login |
| UC-UI-07 | Ver fuentes normativas | Respuesta normativa muestra documento y página citados |

## 4. Glosario

| Término | Significado en esta spec |
|---|---|
| Token de usuario | API key personal de Odoo 19 del usuario. Se envía en `X-User-Token` (frontal→backend) y en `Authorization: Bearer` (backend→Odoo JSON/2). |
| `user_key` | SHA-256 hexadecimal del token de usuario. Identificador pseudónimo para cache de alcance, memoria de hilos y metadata de LangSmith. Nunca el token crudo. |
| Chip | Botón del panel lateral con el nombre de una compañía visible; al activarse se convierte en prefijo del próximo mensaje. |
| `ScopeInfo` | Respuesta Pydantic de `GET /scope`: uid, compañías visibles y clasificación de alcance del token. |
| `CompanyRef` | Par `id`/`name` de una compañía visible. |
| Alcance visible | `user_company_ids` del snapshot de alcance del token (las compañías que el token alcanza), con nombre. |
| Probe de arranque | Chequeo de alcance opcional al arrancar usando `ODOO_API_KEY` (modelo de Spec 001). Si no hay key configurada, no hay probe. |
| Turno de degradado | Respuesta `partial` sin tools cuando el snapshot es `scope_unsafe` por usuario no interno (RF-105). |

## 5. Alcance del MVP

1. App Streamlit de archivo único (`app.py`) con login por API key, chat, panel de alcance y logout.
2. `POST /chat` autenticado por header `X-User-Token`, con cliente Odoo por request y `scope_check` por token (con cache).
3. `GET /scope` para el panel de alcance (compañías visibles + clasificación).
4. Aislamiento de memoria conversacional por usuario (`user_key`).
5. Errores HTTP mapeados a mensajes amigables en la UI (401, 503, timeout, red).
6. Observabilidad LangSmith con `user_key` derivado del token, sin PII.
7. Migración controlada de los tests de Spec 001 afectados por el nuevo régimen de auth (§14).
8. Contratos `ChatRequest`/`ChatResponse` **sin cambios** (Spec 001 §8.1/§8.2).

## 6. Fuera de alcance del MVP

- Login con usuario/contraseña de Odoo, OAuth2/OIDC, SSO, MFA y recuperación de tokens.
- Roles propios del asistente (admin de despacho, auxiliar, solo-lectura forzada): los roles son los de Odoo vía `ir.rule`.
- Multi-tenant (varias bases Odoo), persistencia durable de conversaciones y despliegue multi-worker.
- Renderizado enriquecido de `data` (tablas de obligaciones/documentos): la UI muestra `answer`, `warnings`, `sources` y `status`; `data` estructurado se consume por API.
- Portal del contribuyente final, subida de PDFs, escrituras en Odoo y cualquier cálculo de montos (Spec 001 §6).
- CORS: la UI llama al backend **server-side** (proceso Streamlit → FastAPI), no desde el navegador; no se añade configuración CORS.
- Selección de modelo LLM desde la UI: el modelo es fijo en el backend (decisión D15).

## 7. Decisiones confirmadas

| ID | Decisión | Origen |
|---|---|---|
| D12 | **Auth contra Odoo por API token personal (Bearer).** El usuario genera su key en Odoo 19 (scope nulo; en Odoo 19 el scope `rpc` no es real). El backend la envía como `Authorization: Bearer <token>` en JSON/2 con `X-Odoo-Database: $ODOO_DB`. El token hereda las `ir.rule` del usuario: no se replican reglas de cartera en el asistente. | Clarificación 1 |
| D13 | **Transporte stateless del token por header `X-User-Token`.** `st.session_state["user_token"]` → header por request → `Depends` en FastAPI → cliente Odoo por request. Sin cookies, sin JWT propio, sin estado de sesión en el backend. | Clarificación 2 |
| D14 | **Multi-company en UI: panel de alcance + chips.** El sidebar lista las compañías visibles del token (`GET /scope`); un click fija un chip que pre-llenan el mensaje. La resolución de entidad por mensaje (Spec 001 D2) queda intacta. | Clarificación 3 |
| D15 | **Layout mínimo viable: `app.py` single-file.** `st.chat_message`, `st.chat_input`, `st.sidebar`, `st.form`. Sin BD, sin routing multipágina, sin componentes externos. Historial en `st.session_state`. Modelo LLM fijo en backend. | Clarificación 4 |
| D16 | **Login con API Key.** Arranque en "No autenticado" → `st.form` con `st.text_input(label="Odoo API Token", type="password")` → token en `st.session_state["user_token"]` → chat. "Cerrar Sesión" borra el token + `st.rerun()`. **Sin validación client-side del token**: token inválido → backend 401 → `st.error` amigable. | Clarificación 5 |
| D17 | **Alcance por request con cache.** `scope_check` se ejecuta por token con la misma clasificación de Spec 001 (RF-062), cacheado por `user_key` con TTL (`SCOPE_CHECK_TTL_SECONDS`) y LRU (`USER_SCOPE_CACHE_MAX`). La memoria de hilos se segmenta por `user_key` (`USER_STORES_MAX`). | Derivada de D13 |
| D18 | **`ODOO_API_KEY` opcional.** Solo alimenta el probe de arranque/salud (modelo Spec 001). Si está ausente: no hay probe, health reporta `odoo_available=false` (`degraded`, HTTP 200) y `/chat` funciona con tokens de usuario. | Derivada de D12 |

**Regla de precedencia de auth:** middleware `X-API-Key` (si `API_SHARED_SECRET` está configurado, Spec 001 RF-076) → header `X-User-Token` (401 si falta) → 401 de Odoo al validar el token → 503 solo por fallo transitorio de Odoo al verificar alcance.

## 8. Flujo funcional y contrato

### 8.1 Diagrama de secuencia (login → chat → query → respuesta)

```mermaid
sequenceDiagram
    actor U as Usuario
    participant ST as Streamlit app.py
    participant FA as FastAPI /chat /scope
    participant SC as ScopeService + Cache(user_key)
    participant GR as LangGraph (Spec 001)
    participant OD as Odoo 19 JSON/2 (Bearer token de usuario)
    participant RG as RAG (Chroma + BM25)
    participant LLM as LLM (clasificador/redacción)
    participant LS as LangSmith

    U->>ST: Abre la app
    ST-->>U: Pantalla "No autenticado" (st.form + token password)
    U->>ST: Pega API key y envía
    Note over ST: Sin validación client-side:<br/>token → st.session_state["user_token"]<br/>thread_id = uuid4()
    ST->>FA: GET /scope (X-User-Token, X-API-Key?)
    FA->>SC: user_key = sha256(token); cache hit?
    alt cache miss
        SC->>OD: context_get / has_group / company_ids / companies
        OD-->>SC: uid, grupos, compañías
        SC-->>FA: ScopeSnapshot + companies (TTL)
    end
    FA-->>ST: 200 ScopeInfo | 401 token inválido
    ST-->>U: Sidebar: compañías visibles (chips) + "Cerrar Sesión"
    U->>ST: Escribe mensaje (opcionalmente con chip activo)
    ST->>FA: POST /chat {message, thread_id} (X-User-Token, X-API-Key?)
    FA->>OD: (si hace falta) scope_check por token
    alt Odoo rechaza token (401/403)
        FA-->>ST: 401 {"detail": "token no válido o revocado"}
        ST-->>U: st.error amigable + borra token + rerun a login
    else Odoo no disponible
        FA-->>ST: 503 {"detail":"service_unavailable"}
        ST-->>U: st.error amigable (sesión se mantiene)
    else token válido
        FA->>GR: run_turn(scope_snapshot del usuario, user_key)
        GR->>LLM: clasificación (fake en tests)
        GR->>OD: tools ERP read-only (allowed_company_ids del usuario)
        GR->>RG: search_normativa (si aplica)
        GR-->>FA: ChatResponse (answer, data, sources, warnings, status)
        FA->>LS: traza metadata-only + user_key
        FA-->>ST: 200 ChatResponse
        ST-->>U: st.chat_message(answer) + warnings + fuentes
    end
    U->>ST: "Cerrar Sesión"
    ST-->>U: Borra user_token + thread_id + rerun → login
```

### 8.2 `POST /chat` — request

Request **idéntico al de Spec 001 §8.1** (sin cambios de contrato):

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `message` | `Any` | Sí | Validación manual de Spec 001 RF-002 (`str` no vacío, 1..`MAX_MESSAGE_CHARS`). |
| `thread_id` | `Any` | No | `str` de 1..64 `[A-Za-z0-9_-]`; falta/inálido → UUID4 generado y devuelto. |

Headers obligatorios/condicionales:

| Header | Obligatorio | Notas |
|---|---|---|
| `X-User-Token` | **Sí** en `/chat` | Token personal de Odoo del usuario. Falta → HTTP 401. |
| `X-API-Key` | Si `API_SHARED_SECRET` está configurado | Middleware existente de Spec 001 RF-076; aplica a todas las rutas. |
| `Content-Type` | Sí | `application/json`. |

Reglas:

- El body se parsea y valida igual que en Spec 001: request inválido → `status=invalid_request`, HTTP 200, sin tools ni LLM. **La validación de negocio ocurre después de la autenticación HTTP**: sin token válido no se ejecuta el grafo.
- `request_id` UUID4 por turno; se propaga a logs y LangSmith.
- El backend **nunca** usa `ODOO_API_KEY` para atender `/chat`.
- Response: `ChatResponse` de Spec 001 §8.2, sin cambios (§10 RF-110).

### 8.3 `GET /scope` — response `ScopeInfo`

Nuevo endpoint de solo lectura para el panel lateral. Mismo header `X-User-Token` (y `X-API-Key` si aplica). HTTP 200 con:

| Campo | Tipo | Descripción |
|---|---|---|
| `uid` | `int` | uid de Odoo resuelto del token (para mostrar "sesión: usuario #N" si se desea; no es PII sensible). |
| `companies` | `list[CompanyRef]` | Compañías visibles del token (`user_company_ids`) con nombre, ordenadas por `id`. Vacío si no se pudieron resolver nombres. |
| `scope_state` | `enum` | `scope_ok` \| `scope_partial` \| `scope_unsafe` (misma clasificación de Spec 001 §8.3). |
| `scope_complete` | `bool` | `true` solo si `scope_state=scope_ok`. |
| `tools_enabled` | `bool` | Si el snapshot permite ejecutar tools (Spec 001 `ScopeSnapshot.tools_enabled`). |

**`CompanyRef` (Pydantic):**

| Campo | Tipo | Regla |
|---|---|---|
| `id` | `int` | ID de `res.company`. |
| `name` | `str` | Nombre visible; no vacío. |

Reglas:

- `companies` sale de `user_company_ids` del snapshot (no de todas las activas): un usuario con alcance parcial ve solo su subconjunto.
- Si la consulta de nombres (`res.company search_read` allowlisted: campos `id`, `name`) falla, `companies=[]` y el endpoint sigue siendo 200: la UI muestra un aviso no bloqueante y el chat sigue operativo.
- `scope_state=scope_unsafe` → 200 con el estado (la UI muestra aviso); **no** es 401 salvo que el token sea inválido.
- El payload nunca incluye token, API keys, RIF ni nombres de clientes.

### 8.4 Errores HTTP y mensajes amigables de UI

| Situación | HTTP | Payload backend | Mensaje UI |
|---|---|---|---|
| Falta `X-User-Token` en `/chat` o `/scope` | 401 | `{"detail": "Falta el token de usuario (X-User-Token)."}` | `st.error("Inicia sesión con tu API key de Odoo.")` + vuelta a login |
| `X-API-Key` inválido (Spec 001) | 401 | `{"detail": "Unauthorized"}` | Igual que arriba (no se distingue al usuario) |
| Odoo rechaza el token (401/403 en el scope check) | 401 | `{"detail": "El token de Odoo no es válido o fue revocado."}` | `st.error("El token no es válido o fue revocado. Inicia sesión de nuevo.")` + borra token + rerun a login |
| Odoo no responde/timeout al verificar alcance | 503 | `{"detail": "service_unavailable"}` | `st.error("El asistente no está disponible en este momento. Intenta de nuevo.")` (sesión se mantiene) |
| Producción con snapshot `scope_unsafe` (refresh) | 503 | `{"detail": "service_unavailable"}` | Igual que arriba |
| Backend devuelve 200 con `status=invalid_request` | 200 | `ChatResponse` | Se renderiza como mensaje del asistente con el `answer` canónico |
| Timeout/red de la UI hacia el backend | — | — | `st.error("No se pudo contactar al asistente. Verifica la conexión e intenta de nuevo.")` (sesión se mantiene) |

Los mensajes son texto fijo en español (es-VE), sin detalles internos, sin URL, sin credenciales (RF-051 de Spec 001).

### 8.5 Matriz de estados de autenticación y alcance

| Condición | HTTP | Efecto |
|---|---|---|
| `/chat` sin `X-User-Token` | 401 | No se ejecuta nada |
| Token con formato vacío/solo espacios | 401 | Se trata como falta de token |
| Token válido, `scope_ok` | 200 | Turno normal (Spec 001) |
| Token válido, `scope_partial` | 200 | Turno normal con warning `scope_partial`; `scope_complete=false` |
| Token válido, `scope_unsafe` por admin/atestación, dev/test | 200 | Turno con warning `scope_admin_key`; tools permitidas |
| Token válido, `scope_unsafe` por no interno (`base.group_user=false`), dev/test | 200 | Turno `partial` sin tools (igual que Spec 001) |
| Token válido, `scope_unsafe` (cualquier razón), producción | 503 | No se ejecutan tools |
| Token inválido/revocado | 401 | Mensaje amigable + login |
| Odoo caído al verificar alcance | 503 | Mensaje amigable; sesión se mantiene |

**Precedencia de gateos por request:** `X-API-Key` → `X-User-Token` presente → validez del token en Odoo → disponibilidad de Odoo (503) → alcance (`scope_*`) → grafo.

## 9. Requisitos funcionales (EARS)

### 9.1 Autenticación y transporte del token

- **RF-101** El sistema DEBERÁ exigir el header `X-User-Token` en `POST /chat` y `GET /scope`; CUANDO el header falte, esté vacío o contenga solo espacios, ENTONCES responderá HTTP 401 con `{"detail": "Falta el token de usuario (X-User-Token)."}` sin ejecutar tools ni el LLM.
- **RF-102** CUANDO el header esté presente, el sistema DEBERÁ construir por request un `OdooClient` con `Authorization: Bearer <token>` y `X-Odoo-Database: $ODOO_DB`, ejecutar `scope_check` con la misma clasificación de Spec 001 (RF-062) e inyectar el snapshot resultante en `run_turn` vía `scope_snapshot`.
- **RF-103** CUANDO Odoo rechace el token (HTTP 401/403 en cualquier RPC del scope check), el sistema DEBERÁ responder HTTP 401 con `{"detail": "El token de Odoo no es válido o fue revocado."}`; el mensaje NO deberá incluir el token, la URL de Odoo ni el cuerpo del error de Odoo.
- **RF-104** El sistema DEBERÁ cachear el resultado del scope check por `user_key` (SHA-256 del token) con TTL `SCOPE_CHECK_TTL_SECONDS` y límite LRU `USER_SCOPE_CACHE_MAX`; el cache NO deberá almacenar el token crudo en ningún momento.
- **RF-105** El sistema DEBERÁ aplicar, por token, las mismas consecuencias de alcance de Spec 001 §8.3: `scope_partial` → turno normal con `scope_partial`; `scope_unsafe` por admin/atestación en dev/test → turno con `scope_admin_key` y tools habilitadas; `scope_unsafe` por usuario no interno → turno `partial` sin tools; `scope_unsafe` en producción → HTTP 503 antes de ejecutar tools.
- **RF-106** El sistema DEBERÁ segmentar la memoria conversacional por `user_key`; CUANDO dos usuarios distintos usen el mismo `thread_id`, ENTONCES ningún historial, entidad ni filtro deberá cruzarse entre ellas.
- **RF-107** El sistema DEBERÁ exponer `GET /scope` que devuelva `ScopeInfo` validado por Pydantic con `uid`, `companies` (`list[CompanyRef]` ordenado por `id`), `scope_state`, `scope_complete` y `tools_enabled`.
- **RF-108** El sistema NO DEBERÁ usar `ODOO_API_KEY` para atender `/chat`; la variable de entorno DEBERÁ ser opcional y alimentar únicamente el probe de arranque y `/health` (D18).
- **RF-109** CUANDO `LANGSMITH_TRACING` esté habilitado, el sistema DEBERÁ incluir `user_key` en la metadata de la traza del turno y NUNCA el token crudo, el mensaje completo ni resultados de tools (política de redacción de Spec 001 RF-052/RF-078 intacta).
- **RF-110** El sistema NO DEBERÁ modificar los contratos `ChatRequest`/`ChatResponse` de Spec 001 §8.1/§8.2 ni la precedencia de estados de §8.3; la autenticación es una compuerta previa, no un estado nuevo del contrato.
- **RF-111** `ScopeInfo.companies` DEBERÁ contener las compañías de `user_company_ids` del snapshot con nombre obtenido por RPC allowlisted (`res.company`, campos `id`/`name`); CUANDO la consulta de nombres falle, `companies` será `[]` y el endpoint seguirá respondiendo 200.

### 9.2 Interfaz de usuario (Streamlit)

- **RF-120** La app DEBERÁ arrancar en estado "No autenticado" y mostrar una pantalla de bienvenida con un `st.form` que contenga `st.text_input(label="Odoo API Token", type="password")` y botón de envío; NO deberá mostrar `st.chat_input` ni datos de conversación en ese estado.
- **RF-121** CUANDO el usuario envíe el formulario con un token no vacío, la app DEBERÁ guardarlo en `st.session_state["user_token"]` SIN realizar ninguna llamada a Odoo ni al backend (sin validación client-side del token).
- **RF-122** CUANDO exista `st.session_state["user_token"]`, la app DEBERÁ renderizar el chat: `st.sidebar` con panel de alcance y botón "Cerrar Sesión", más `st.chat_input` en el área principal.
- **RF-123** El botón "Cerrar Sesión" DEBERÁ borrar `user_token` (y el resto del estado de sesión: `thread_id`, historial, chip y `ScopeInfo`) y ejecutar `st.rerun()`, volviendo a la pantalla de login.
- **RF-124** CUANDO el backend responda HTTP 401, la app DEBERÁ mostrar un `st.error` con mensaje amigable, borrar `user_token` y volver a la pantalla de login vía `st.rerun()`.
- **RF-125** La app DEBERÁ enviar el header `X-User-Token` con el token de sesión en cada request a `/chat` y `/scope`, y el header `X-API-Key` CUANDO `API_SHARED_SECRET` esté configurado en el entorno de la UI.
- **RF-126** La app DEBERÁ generar un `thread_id` UUID4 al iniciar sesión, reutilizarlo en todos los turnos de esa sesión y descartarlo al cerrar sesión.
- **RF-127** El panel de alcance DEBERÁ renderizar las compañías visibles como chips; CUANDO el usuario active un chip, el próximo mensaje enviado DEBERÁ pre-llenarse con la compañía seleccionada (prefijo `Compañía <nombre>: `), y un control permitirá desactivarlo.
- **RF-128** La app DEBERÁ renderizar cada respuesta como `st.chat_message` con el `answer`, el `status` como epígrafe, los `warnings` (código + mensaje) y la lista de `sources` (documento y página/sección) cuando existan.
- **RF-129** La app NO DEBERÁ mostrar el token en pantalla después del login ni escribirlo en logs; el campo de token DEBERÁ ser `type="password"` y el valor solo existirá en `st.session_state`.
- **RF-130** La app DEBERÁ mapear HTTP 503, timeout de red y errores de transporte a un `st.error` amigable, manteniendo la sesión abierta (sin borrar el token).
- **RF-131** La app DEBERÁ mantener el historial de la conversación en `st.session_state` (roles user/assistant) y renderizarlo al reejecutarse.
- **RF-132** La app NO DEBERÁ enviar un mensaje vacío o compuesto solo de espacios (guarda de experiencia de uso; no constituye validación del token).

### 9.3 Configuración y salud

- **RF-112** El sistema DEBERÁ validar al arrancar, con `ODOO_API_KEY` ausente, que no se requiere probe: el arranque prosigue, `ScopeService` queda deshabilitado y `/health` reporta `odoo_available=false` y `scope_state=null` con estado `degraded` (HTTP 200).
- **RF-113** El sistema DEBERÁ mantener, con `ODOO_API_KEY` presente, el comportamiento de arranque de Spec 001 (probe, fail-fast en producción con `scope_unsafe`) sin cambios.
- **RF-114** `GET /health` NO DEBERÁ exponer el header de token ni el `user_key`; DEBERÁ mantener los campos de Spec 001 (`status`, `scope_state`, `scope_complete`, `corpus_status`, `corpus_version`, `chroma_available`, `bm25_available`, `odoo_available`, `worker_count`, `app_version`).

## 10. Requisitos no funcionales

- **RNF-101 Tipado:** `ScopeInfo`, `CompanyRef` y la UI usan Pydantic/Streamlit tipado; la UI valida toda respuesta del backend con los modelos compartidos (`ChatResponse` de `contafis_assistant.domain.contracts`). Prohibido consumir JSON crudo sin validar.
- **RNF-102 Tests sin costo ni red:** toda prueba nueva corre sin red, sin Odoo real, sin OpenAI real y sin Streamlit server. La UI se prueba con `st.testing.v1.AppTest` y el backend con `TestClient` + `httpx.MockTransport`; el tráfico HTTP de la UI se intercepta monkeyparcheando `httpx.request`.
- **RNF-103 Configuración (nuevas variables):** backend `USER_SCOPE_CACHE_MAX=500`, `USER_STORES_MAX=50`; UI `ASSISTANT_API_URL=http://127.0.0.1:8000`, `UI_API_TIMEOUT_SECONDS=30`, `API_SHARED_SECRET=` (opcional, solo si el backend la exige). Todo se lee con `os.getenv()`; `.env` nunca se commitea.
- **RNF-104 Observabilidad:** `user_key` en metadata de trazas; prohibido el token crudo, mensajes completos y resultados de tools (Spec 001 RNF-015).
- **RNF-105 Rendimiento:** el cache de alcance por `user_key` evita repetir 4+ RPC de Odoo por turno; construir deps por request no hace I/O adicional más allá de las tools del turno.
- **RNF-106 Dependencias:** `streamlit` (runtime + framework de test `st.testing.v1`, que **viene incluido en el paquete**). Ver §15.1: el paquete PyPI `streamlit-testing` no existe (404); no se agrega.
- **RNF-107 Privacidad de sesión:** el token vive solo en `st.session_state` del proceso Streamlit (memoria del servidor de la UI); no se persiste en disco, no viaja a logs y se borra al cerrar sesión.
- **RNF-008/016 heredados:** `request_id` trazable de punta a punta y fail-fast de arranque de Spec 001 se mantienen; con probe deshabilitado no aplican las fallas de scope del probe.
- **RNF-108 Zona horaria e idioma:** la UI muestra fechas tal como las entrega el backend (es-VE, `dd/mm/aaaa`); no recalcula zonas ni meses.

## 11. Historias de usuario

- **HU-01** Como contador, quiero iniciar sesión con mi API key de Odoo para usar el asistente sin compartir credenciales de administrador.
- **HU-02** Como contador, quiero ver en el panel lateral qué compañías alcanza mi token, para saber si mis preguntas se responderán con datos completos o parciales.
- **HU-03** Como contador, quiero hacer clic en una compañía y que mi próxima pregunta se acotoe a ella, para no tener que escribir el nombre.
- **HU-04** Como contador, quiero mantener el hilo de la conversación ("¿y cuáles están vencidas?") dentro de una sesión.
- **HU-05** Como contador, quiero un mensaje claro si mi token fue revocado, para saber que debo generar uno nuevo.
- **HU-06** Como contador, quiero cerrar sesión y que el token desaparezca de la app.
- **HU-07** como auxiliar con alcance parcial, quiero que la app me avise cuando mi alcance es incompleto en lugar de mostrarme totales en 0 engañosos.
- **HU-08** Como contador, quiero ver la norma citada (documento y página) junto a la respuesta normativa.

## 12. Criterios de aceptación

Todos se verifican con pytest: `AppTest` para UI, `TestClient` para API, fakes para Odoo/LLM/Chroma.

| ID | Escenario | Resultado esperado | RF |
|---|---|---|---|
| AC-101 | `POST /chat` sin `X-User-Token` | HTTP 401 con `detail="Falta el token de usuario (X-User-Token)."`; sin tools ni LLM | RF-101 |
| AC-102 | `/chat` con token que Odoo rechaza (401/403 en `context_get`) | HTTP 401 con `detail="El token de Odoo no es válido o fue revocado."`; sin token ni URL en el payload | RF-103 |
| AC-103 | `/chat` con token válido | El cliente Odoo del turno envía `Authorization: Bearer <token del usuario>` y `X-Odoo-Database`; `scope_snapshot` del usuario llega al grafo; `ChatResponse` ok | RF-102 |
| AC-104 | Dos turnos con el mismo token | El scope check se ejecuta una sola vez (cache por `user_key` con TTL); el cache no contiene el token crudo | RF-104 |
| AC-105 | Token de usuario no interno (`base.group_user=false`), dev/test | Turno `partial` sin tools; sin `scope_admin_key` | RF-105 |
| AC-106 | Token de admin o sin atestación, dev/test | Turno con warning `scope_admin_key`; tools habilitadas | RF-105 |
| AC-107 | Producción con snapshot `scope_unsafe` | HTTP 503 antes de ejecutar tools | RF-105 |
| AC-108 | Dos usuarios distintos con el mismo `thread_id` | Sin cruce de historial/entidad/filtro; `thread_id` devuelto idéntico al enviado | RF-106 |
| AC-109 | `GET /scope` con token válido y alcance completo | 200 con `uid`, `companies` (id+name), `scope_state=scope_ok`, `scope_complete=true`, `tools_enabled=true` | RF-107, RF-111 |
| AC-110 | `GET /scope` con alcance parcial | `companies` = solo `user_company_ids`; `scope_state=scope_partial`, `scope_complete=false` | RF-107 |
| AC-111 | `/chat` sin `ODOO_API_KEY` configurada | Arranca; `/health` 200 `degraded` con `scope_state=null`, `odoo_available=false`; los turnos funcionan | RF-108, RF-112 |
| AC-112 | Traza de un turno con token de usuario | Payload contiene `user_key` = SHA-256 del token; no contiene el token crudo ni mensaje | RF-109 |
| AC-113 | App recién abierta | Existe `st.form` con `st.text_input` de tipo password; NO existe `st.chat_input`; no hay mensajes renderizados | RF-120 |
| AC-114 | Enviar formulario con token | `st.session_state["user_token"]` = token; `thread_id` UUID4 generado; **cero** requests HTTP enviados (sin validación client-side) | RF-121, RF-126 |
| AC-115 | Con token en sesión | Se renderiza `st.chat_input` y el sidebar con "Cerrar Sesión" | RF-122 |
| AC-116 | Click en "Cerrar Sesión" | `user_token` borrado; vuelta al login (form visible, chat_input ausente) | RF-123 |
| AC-117 | Backend responde 401 en `/scope` o `/chat` | `st.error` amigable visible; token borrado; pantalla de login | RF-124 |
| AC-118 | Turno exitoso | El request enviado lleva `X-User-Token` (y `X-API-Key` si configurado); se renderiza `answer` en `st.chat_message` | RF-125, RF-128 |
| AC-119 | Click en chip de compañía + mensaje | El body enviado tiene `message` prefijado con `Compañía <nombre>: `; el chip se puede desactivar | RF-127 |
| AC-120 | Respuesta con warnings y sources | Se renderizan los warnings y la lista de fuentes (documento + página) | RF-128 |
| AC-121 | Backend 503 o timeout de red | `st.error` amigable; el token permanece en sesión; el chat sigue visible | RF-130 |
| AC-122 | Recarga de la app con sesión activa | El historial de `st.session_state` se re-renderiza; `thread_id` no cambia | RF-131, RF-126 |
| AC-123 | Intento de envío con mensaje vacío/solo espacios | No se envía request al backend | RF-132 |
| AC-124 | Token visible solo en session_state | Ningún elemento renderizado contiene el token; no hay logs con el token | RF-129 |
| AC-125 | Suite de Spec 001 tras la migración | Los tests de `tests/api/` actualizados para enviar `X-User-Token` pasan; contrato y estados intactos | RF-110, §14 |

## 13. Casos límite y manejo de errores

| ID | Caso | Comportamiento esperado |
|---|---|---|
| EC-101 | Token con espacios alrededor | Se recorta antes de enviar; si queda vacío, se comporta como falta de token (401 del backend o no-envío en UI) |
| EC-102 | Token válido pero Odoo caído en `/scope` | 503; sidebar muestra aviso no bloqueante; chat sigue operable y los turnos también darán 503 hasta que Odoo vuelva |
| EC-103 | Usuario con cero compañías activas asignadas | `scope_partial`/`no_active_companies`; panel sin chips; turnos responden `partial` con `scope_partial` |
| EC-104 | Token de usuario portal (público) | `/scope` 200 con `scope_unsafe`; `/chat` devuelve turno `partial` sin tools (dev/test) o 503 (producción) |
| EC-105 | Muchas compañías visibles (más de 20) | El panel muestra las primeras 20 + indicación "y N más"; el chat no se afecta |
| EC-106 | `thread_id` desconocido tras reinicio del backend | Warning `thread_context_unavailable` de Spec 001; la UI lo muestra como warning y sigue |
| EC-107 | Dos pestañas/navegadores con tokens distintos | Sesiones Streamlit independientes; sin cruce de tokens ni de memoria (RF-106) |
| EC-108 | Backend tarda más que `UI_API_TIMEOUT_SECONDS` | `st.error` amigable de timeout; sesión mantenida; el usuario puede reintentar |
| EC-109 | Clic en chip y luego en "Cerrar Sesión" | El chip se descarta con el estado de sesión (RF-123) |
| EC-110 | Refresh del panel de alcance | Botón "Refrescar" en sidebar re-consultar `GET /scope` y actualiza el `ScopeInfo` cacheado en sesión |
| EC-111 | `API_SHARED_SECRET` configurado y la UI sin el secreto | Todas las llamadas dan 401 del middleware; la UI muestra el error genérico de login (no distingue) |
| EC-112 | Mensaje que excede `MAX_MESSAGE_CHARS` | Backend responde 200 `invalid_request`; la UI lo renderiza como mensaje del asistente |

## 14. Coexistencia con Spec 001 y migración

**Intacto (Spec 001 manda):** dominio, contratos, `ToolResult`, estados de respuesta y su precedencia, grafo LangGraph y presupuesto de tools, RAG, allowlist Odoo, `scope_check` y su clasificación, hilos y warnings, redacción/PII, timeouts, zona horaria, salud (campos).

**Superseded por esta spec (solo estos puntos):**

| Punto | Spec 001 | Spec 002 |
|---|---|---|
| Auth de `/chat` | Key global opcional (`X-API-Key` si hay secreto) | **`X-User-Token` obligatorio**; sin fallback a `ODOO_API_KEY` |
| `ODOO_API_KEY` | Requerida | **Opcional**; solo probe de arranque/salud |
| Cliente Odoo | Uno global por proceso | **Uno por request** con el token del usuario |
| `scope_check` | Al arrancar + TTL, una sola key | **Por token**, con cache TTL/LRU por `user_key`; probe opcional al arrancar |
| Memoria de hilos | Store global único | **Store segmentado por `user_key`** |
| UI | Fuera de alcance | `app.py` Streamlit single-file |
| Endpoints | `/chat`, `/health` | + `GET /scope` |
| LangSmith metadata | Sin usuario | + `user_key` |

**Migración de tests existente (parte del cierre, tarea T05/T10):**

- `tests/api/test_chat_api.py` (y todo test que llame a `/chat`): enviar `X-User-Token` o assert de 401. El cuerpo de los tests de contrato/estados no cambia.
- `tests/api/test_lifespan.py`, `tests/api/test_startup.py`, `tests/api/test_health.py`: añadir casos con `ODOO_API_KEY` ausente (arranque OK, health `degraded`).
- `tests/unit/test_config.py`: `ODOO_API_KEY` opcional; nuevos settings con defaults.
- `tests/integration/test_tracing.py`: `TraceEvent` gana `user_key` (default `None`); los tests existentes siguen pasando sin setearlo.
- `tests/conftest.py`: fixture de transporte inyectable para el cliente de usuario (`app.state.runtime.user_transport` o parámetro de `build_runtime`).
- No se modifica `specs/001-chat-fiscal/spec.md`; la supersession queda documentada aquí.

## 15. Dependencias nuevas y variables de entorno

### 15.1 Dependencias

- **Nueva runtime:** `streamlit` (app + framework de test incluido).
- **Corrección verificada:** el paquete PyPI **`streamlit-testing` no existe** (HTTP 404 en `https://pypi.org/pypi/streamlit-testing/json`). El framework de test es `st.testing.v1.AppTest`, que **se distribuye dentro de `streamlit`** (docs oficiales "App testing"). No se agrega ninguna dependencia extra para testear la UI; `pytest` ya está.
- Sin cambios: el resto del stack de Spec 001 (LangChain/LangGraph/LangSmith, FastAPI, httpx, Pydantic, Chroma, rank-bm25, rapidfuzz, pypdf).
- El backend usa `Depends`/`Header` de FastAPI (ya dependencia) para `X-User-Token`; no se introducen nuevos frameworks.

### 15.2 Variables de entorno nuevas

| Variable | Default | Uso |
|---|---|---|
| `USER_SCOPE_CACHE_MAX` | `500` | Máximo de entradas LRU del cache de alcance por `user_key` |
| `USER_STORES_MAX` | `50` | Máximo de stores de hilos por usuario (LRU) |
| `ASSISTANT_API_URL` | `http://127.0.0.1:8000` | URL base del backend para la UI |
| `UI_API_TIMEOUT_SECONDS` | `30` | Timeout de las llamadas UI→backend |
| `API_SHARED_SECRET` | vacío | Solo si el backend exige `X-API-Key`; la UI lo lee del mismo entorno |
| `ODOO_API_KEY` | — | **Ahora opcional**: probe de arranque/salud |

## 16. Supuestos

- A-01: Odoo 19 acepta API keys personales por usuario vía `Authorization: Bearer` en JSON/2 y las `ir.rule` del usuario se aplican (A-01/A-09/A-10 de Spec 001 siguen vigentes para el addon).
- A-02: El contador sabe generar su API key en Odoo 19 (Preferencias → Seguridad de la cuenta) y el despacho emite keys de solo lectura; la atestación `ODOO_READONLY_ATTESTED` pasa a significar "los tokens de usuario son de solo lectura".
- A-03: Streamlit y FastAPI corren en la misma máquina/repo; la UI habla al backend por `ASSISTANT_API_URL` (HTTP local por defecto).
- A-04: `st.testing.v1.AppTest` ejecuta el script en el mismo proceso del test, por lo que monkeypatch de `httpx.request` y de variables de entorno funciona.
- A-05: El token de usuario no contiene caracteres inválidos para headers HTTP; se recorta antes de enviar.
- A-06: Un solo worker uvicorn sigue siendo el modo de despliegue (Spec 001 RF-077); el cache de alcance es in-process.
- A-07: El LLM y el corpus son fijos en el backend; la UI no los configura.

## 17. Trazabilidad

| Clarificación | Decisión | Requisitos |
|---|---|---|
| C1 — Auth con API token personal (Bearer) | D12 | RF-102, RF-103, RF-108; AC-102, AC-103 |
| C2 — Header `X-User-Token` stateless | D13 | RF-101, RF-102, RF-125; AC-101, AC-103, AC-118 |
| C3 — Panel de alcance + chips | D14 | RF-107, RF-111, RF-127; AC-109, AC-110, AC-119 |
| C4 — Layout mínimo `app.py` | D15 | RF-120, RF-122, RF-128, RF-131; AC-113, AC-115, AC-118, AC-120 |
| C5 — Login con API Key | D16 | RF-121, RF-123, RF-124, RF-130; AC-114, AC-116, AC-117, AC-121 |
| Derivadas | D17, D18 | RF-104, RF-105, RF-106, RF-112, RF-113; AC-104 a AC-108, AC-111 |

| Constitución | Requisitos |
|---|---|
| El ERP manda | RF-102, RF-105 (el alcance lo impone Odoo, no la UI) |
| Privacidad del contador | RF-106, RF-109, RF-129, RNF-104, RNF-107 |
| LangGraph como orquestador | RF-102, RF-110 (el grafo no cambia) |
| Tests sin gastar tokens | RNF-102 |
| Cero alucinaciones | RF-110, RF-128 (la UI solo renderiza `answer`/`sources`) |

## 18. Definición de terminado (MVP)

1. Todos los AC-101..AC-125 pasan en CI sin red, sin Odoo real, sin OpenAI real y sin LLM real.
2. La suite de Spec 001 migrada (§14) sigue verde; contratos y estados intactos.
3. `app.py` corre con `streamlit run app.py` contra el backend local y se ejercitó login → chat → logout manualmente con un token real de solo lectura.
4. Un token inválido produce exactamente el flujo 401 → `st.error` → login, verificado por test de UI.
5. `user_key` aparece en las trazas de LangSmith y el token crudo no aparece en trazas, logs ni respuestas.
6. `.env.example` y `requirements.txt` actualizados (streamlit + variables nuevas), sin secretos.
7. README documenta: generar API key personal en Odoo 19, `streamlit run app.py`, `uvicorn --workers 1` y las variables de la UI.
8. El panel de alcance muestra las mismas compañías que el `scope_check` del backend para el mismo token.

## 19. Historial de revisión

### 19.1 Ronda de clarificación — 2026-10-26

Cinco preguntas resueltas por el usuario (tabla de §7):

| Pregunta | Decisión | Clave |
|---|---|---|
| 1. Auth contra Odoo | API Token personal (Bearer) | D12 |
| 2. Transporte token | Header `X-User-Token` por request | D13 |
| 3. Multi-company UI | Panel de alcance + atajos (chips) | D14 |
| 4. Layout chat | Mínimo viable (`app.py` single-file) | D15 |
| 5. Flujo login | Login con API Key | D16 |

Hallazgos de verificación técnica incorporados en r1:

| ID | Tipo | Hallazgo | Cambio aplicado |
|---|---|---|---|
| V-01 | Corrección | `streamlit-testing` no existe en PyPI (404) | §15.1: el framework de test es `st.testing.v1.AppTest`, incluido en `streamlit`; no se agrega dependencia |
| V-02 | Precisión | API de AppTest incierta | Verificada en docs/fuente oficial: `AppTest.from_file`, `.run()`, `at.session_state`, `at.chat_input[0].set_value`, `at.form[0]`/`at.form_submit_button[0].click()`, `at.error`, `at.sidebar.button`, `st.rerun()` interno |
| V-03 | Riesgo | `/chat` sin fallback rompería los tests de Spec 001 | §14: migración explícita de tests; contrato intacto |
| V-04 | Riesgo | Health 503 permanente sin `ODOO_API_KEY` | RF-112/RNF: health `degraded` (200) con probe deshabilitado |
