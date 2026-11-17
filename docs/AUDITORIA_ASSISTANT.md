# Auditoría técnica del Asistente Fiscal ContaFis

> **Tipo:** auditoría read-only de seguridad, manejo de errores y resiliencia  
> **Repositorio:** `contafis-assistant`  
> **Rama / commit auditado:** `main` / `870c15d`  
> **Fecha:** 2026-11-16  
> **Alcance:** microservicio FastAPI + LangGraph + NVIDIA NIM, integración Odoo 19, RAG local y despliegue Docker/Render  
> **Restricción respetada:** no se modificó código, no se ejecutaron tests y no se hizo push. Este documento es el único artefacto nuevo.

---

## 1. Resumen ejecutivo

El servicio tiene una base de ingeniería sólida: modelos Pydantic estrictos, errores tipados en el cliente Odoo, respuestas deterministas en español para los estados degradados, historial de hilos acotado, redacción de PII y trazas LangSmith metadata-only por defecto. No se encontraron secretos hardcodeados ni credenciales versionadas.

Sin embargo, la auditoría identificó **un hallazgo crítico condicional** y varios riesgos altos que deben atenderse antes de exponer staging o producción:

1. **La autenticación por `X-API-Key` falla abierta** si `API_SHARED_SECRET` falta o está vacío. El validador de arranque solo lo exige cuando `APP_ENV=production` o cuando `API_HOST` no es loopback. El contenedor Docker arranca con `--host 0.0.0.0`, pero `Settings.api_host` conserva su default `127.0.0.1`; por eso el validador no detecta el bind real. El blueprint de staging usa `APP_ENV=development`, de modo que un despliegue público de Render puede quedar sin API key si el secreto no se cargó en el panel. Las rutas de datos siguen exigiendo sesión/token de Odoo, pero `/docs`, `/openapi.json` y `/health` quedan alcanzables sin credencial alguna, y `/chat`/`/digitize*` para cualquiera que tenga o consiga una credencial Odoo.
2. **Hay esperas sin cota superior**: la validación de sesión Odoo no pasa timeout al transporte HTTP; el clasificador y el compositor pueden acumular reintentos de 60 s; `RETRIEVAL_TIMEOUT_SECONDS` está definido pero no se usa; no existe deadline global por turno ni circuit breaker.
3. **El transporte HTTP compartido de sesiones de usuario se cierra en cada request**, porque `OdooClient.close()` cierra el `httpx.HTTPTransport` compartido. Bajo concurrencia puede cerrar conexiones en uso y producir errores intermitentes.
4. **Digitize concentra los mayores problemas de entrada y observabilidad**: no limita el tamaño de la imagen, registra respuestas crudas de NIM con datos de factura, colapsa todos los fallos en un 503 genérico y procesa lotes de hasta 20 imágenes de forma síncrona durante 10–60 minutos.
5. **`ThreadStore` no tiene una fuga de memoria no acotada** —está limitado por LRU—, pero sí tiene un problema de aislamiento multi-tenant: el `thread_id` lo elige el cliente, el patrón permite valores predecibles y el store es global, no está segmentado por `user_key` (T03 pendiente). El historial guarda `EntityRef` con nombre y RIF, no el texto del mensaje.

**Riesgo global:** alto, condicionado a la configuración real del despliegue. La acción P0 es hacer que la autenticación falle cerrada y verificar el estado real de las variables en Render. Las acciones P1 deben cerrar las brechas de timeouts, tamaño de payload, transporte compartido, errores de Digitize y logging con PII.

---

## 2. Alcance, metodología y limitaciones

### 2.1 Alcance revisado

Se revisaron estáticamente, entre otros:

- `src/contafis_assistant/main.py`
- `src/contafis_assistant/config.py`
- `src/contafis_assistant/api/routes.py`
- `src/contafis_assistant/api/user_auth.py`
- `src/contafis_assistant/api/security.py`
- `src/contafis_assistant/api/errors.py`
- `src/contafis_assistant/api/digitize.py`
- `src/contafis_assistant/memory/threads.py`
- `src/contafis_assistant/graph/*.py`
- `src/contafis_assistant/domain/*.py`
- `src/contafis_assistant/odoo/client.py`
- `src/contafis_assistant/odoo/scope.py`
- `src/contafis_assistant/rag/*.py`
- `src/contafis_assistant/observability/*.py`
- `Dockerfile`, `render.yaml`, `docs/DEPLOY.md`, `README.md`, `pyproject.toml`, `requirements.txt`, `constraints.txt`, `.env.example`

### 2.2 Método

- Análisis estático de código y configuración.
- Contraste de hallazgos con el comportamiento de las librerías instaladas en `.venv`:
  - `httpx 0.28.1` / `httpcore 1.0.9`: `HTTPTransport.handle_request()` no aplica el timeout por defecto del `Client` si el `Request` no lleva la extensión `timeout`; `httpx.Client.close()` cierra el transporte y el pool.
  - `fastapi 0.162.0` / `pydantic 2.14`: el handler 422 devuelve `exc.errors()`, que incluye `input`.
  - `langchain-nvidia-ai-endpoints 1.5.0`: `NVIDIAEmbeddings` no recibe un timeout explícito en `_LazyEmbedder`, pero su cliente interno usa 60 s por defecto.
  - `Settings(max_tool_calls=8)` lanza `less_than_equal` porque el campo declara `le=3`.
  - `redact_text("X-Odoo-Session: ...")` no enmascara ese header; `X-User-Token` y `Cookie` sí quedan cubiertos.
- Revisión de consistencia entre `render.yaml`, `Dockerfile`, `docs/DEPLOY.md` y el código.

### 2.3 Limitaciones

- **No se ejecutaron tests** ni se levantó el servicio. No se hicieron llamadas reales a Odoo, NIM, Chroma ni LangSmith.
- No hubo acceso a la configuración efectiva del panel de Render ni a los logs de producción/staging.
- No se hizo análisis dinámico de concurrencia, carga, memoria ni latencia. Los hallazgos de concurrencia y agotamiento son análisis de código y deben confirmarse en staging.
- Las líneas citadas corresponden al commit `870c15d`; pueden desplazarse con cambios posteriores.

---

## 3. Arquitectura observada

```text
Cliente (widget Odoo / integración)
        │
        ▼
FastAPI (create_app)
  ├─ middleware X-API-Key  ── /livez exento
  ├─ /chat       ──► UserContext (X-Odoo-Session o X-User-Token)
  │                    └─► ChatRuntime.chat_with_user
  │                          └─► run_turn (LangGraph acotado)
  │                                ├─ clasificador (ChatOpenAI compatible NIM)
  │                                ├─ planner / executor
  │                                │    ├─ tools Odoo (JSON/2 o sesión)
  │                                │    └─ RAG (Chroma dense + BM25 + RRF)
  │                                └─ finalize ─► render determinista / Answer Composer
  ├─ /scope      ──► snapshot de alcance del usuario
  ├─ /health     ──► estado de scope/corpus/Odoo (no NIM/embedder)
  ├─ /digitize   ──► ChatOpenAI (NIM vision) ─► JSON ─► Pydantic
  ├─ /digitize_batch ──► N × /digitize secuencial
  └─ /livez      ──► liveness público
```

Puntos relevantes:

- El **middleware de API key** se aplica a todo salvo `/livez` (`main.py:1047-1058`).
- La **autenticación de usuario** es independiente: `/chat`, `/scope` y `/digitize*` dependen de `get_user_context_from_session` (`api/user_auth.py:403-454`).
- El **runtime** es un singleton por proceso (`main.py:1029-1065`) con un `ThreadStore` global (`main.py:965-968`).
- El despliegue Docker fija **un solo worker** (`main.py:73`, `main.py:1077`, `Dockerfile:52-54`).
- El **health check de Render y Docker** apunta a `/livez` (`render.yaml:6`, `Dockerfile:46-50`).

---

## 4. Modelo de severidad

| Severidad | Criterio |
|---|---|
| **Crítico** | Permite exponer el servicio o datos sin autenticación, o comprometer el perímetro de forma directa. |
| **Alto** | Riesgo de indisponibilidad, fuga de PII, ruptura de aislamiento multi-tenant o fallo de despliegue probable. |
| **Medio** | Debilidad de defensa en profundidad, error operativo recurrente o degradación significativa de latencia/costo. |
| **Bajo** | Higiene, consistencia, observabilidad o documentación. |

---

## 5. Hallazgos

### 5.1 Crítico

#### CR-01 — La autenticación por API key falla abierta si `API_SHARED_SECRET` no está configurado

**Severidad:** Crítico (condicionado a que el despliegue público no tenga el secreto cargado).

**Evidencia:**

- `api/security.py:33-34`: si `expected` es falsy, `verify_api_key()` devuelve `True`.
- `config.py:104-111`: un `API_SHARED_SECRET` vacío se normaliza a `None`.
- `config.py:125-128`: solo exige secreto si `api_host` no es loopback.
- `config.py:138-141`: solo lo exige si `app_env == "production"`.
- `config.py:143-149`: en cualquier otro caso imprime un warning y continúa.
- `Dockerfile:54`: el contenedor arranca con `uvicorn ... --host 0.0.0.0`.
- `config.py:83`: `api_host` tiene default `127.0.0.1`, que es lo que ve el validador aunque el bind real sea `0.0.0.0`.
- `render.yaml:52-54`: staging declara `APP_ENV=development`.
- `render.yaml:25-26`: `API_SHARED_SECRET` se marca `sync: false`, es decir, depende de que alguien lo cargue en el panel.
- `main.py:1045-1058`: el middleware acepta cualquier request si `ApiKeyGuard` considera que no hay secreto.
- `README.md:66` afirma “fail-closed en producción”; la afirmación es correcta solo para `APP_ENV=production`, no para un staging público con `APP_ENV=development`.

**Impacto:**

- Si `API_SHARED_SECRET` no está definido o está vacío, **todas las rutas salvo `/livez` quedan sin la compuerta de API key**.
- Las rutas de datos siguen exigiendo `X-Odoo-Session` o `X-User-Token` (`api/user_auth.py:403-454`), por lo que no hay lectura anónima directa del ERP. Pero quedan expuestos:
  - `/docs` y `/openapi.json` (superficie y esquema de la API);
  - `/health` (estado de scope, corpus, Odoo y worker count);
  - `/chat` (consumo de LLM, Odoo y RAG para cualquiera con una sesión/token Odoo);
  - `/digitize` y `/digitize_batch` (consumo de cuota NIM Vision, con payloads grandes).
- Un atacante con una sesión Odoo válida —por ejemplo, un usuario interno con pocos privilegios— puede usar el asistente sin la segunda barrera de API key y amplificar el consumo de recursos.
- La combinación es realista: Render free expone una URL pública, el Dockerfile escucha en `0.0.0.0` y el blueprint de staging está en `development`. No hace falta un error de código adicional; basta con que el secreto no se haya cargado.

**Recomendación P0:**

1. Cambiar la semántica a **fail-closed**: si no hay secreto configurado, `verify_api_key()` debe devolver `False` salvo un modo local explícito y acotado (`APP_ENV=test` o `ALLOW_INSECURE_LOCAL=true`).
2. Validar en arranque el **host real de bind**, no solo `Settings.api_host`. Si el proceso se ejecuta en Docker/Render, exigir secreto siempre.
3. Quitar `APP_ENV=development` de staging o garantizar `API_SHARED_SECRET` obligatorio en el blueprint. Mejor aún: que el blueprint falle el deploy si el secreto no está presente.
4. Verificar en el panel de Render si el secreto está cargado hoy; si no, rotarlo y redesplegar.
5. Agregar tests de regresión: secreto ausente/vacío → 401 en todas las rutas salvo `/livez`; `/livez` → 200.

---

### 5.2 Alto

#### AL-01 — `_validate_session` no tiene timeout de aplicación y fuerza un refresh de scope por request

**Evidencia:**

- `api/user_auth.py:164-205`: la validación usa `transport.handle_request(httpx.Request(...))` directamente.
- `api/user_auth.py:192-197`: el `Request` no lleva extensión `timeout`; `HTTPTransport.handle_request()` de httpx no aplica el timeout por defecto del `Client` en ese caso.
- `api/user_auth.py:294-295`: tras validar la sesión se ejecuta `scope_service.refresh(force=True)`.
- `api/user_auth.py:357`: el flujo legacy también fuerza `refresh(force=True)`.
- `odoo/scope.py:205-213`: el `ScopeService` tiene TTL, pero `force=True` lo ignora.
- `api/user_auth.py:300-312`: además se consulta `res.company` en cada request autenticado.

**Impacto:**

- Una conexión Odoo colgada puede bloquear el threadpool de FastAPI **indefinidamente** para esa request.
- Cada request autenticado ejecuta varios RPCs a Odoo (validación de sesión, scope check con `has_group`, `res.company`), aunque el TTL de scope sea de 300 s. Esto multiplica latencia y carga sobre Odoo.
- Con `UVICORN_WORKERS=1`, varias requests lentas pueden agotar el threadpool y afectar `/chat`, `/health` y `/livez` (aunque `/livez` no usa el runtime, compite por el event loop y el pool de threads).

**Recomendación:**

- Usar un `httpx.Client(timeout=settings.odoo_timeout_seconds)` para validar la sesión, o pasar `extensions={"timeout": {...}}` explícito.
- Cachear la validación de sesión por `session_id` con TTL corto y límite de tamaño, invalidando en 401.
- No forzar `refresh(force=True)` en cada request; usar el TTL existente y refrescar solo cuando haya señales de cambio.
- Aplicar un deadline por request (ver AL-08).

---

#### AL-02 — El transporte HTTP compartido de sesiones se cierra en cada request

**Evidencia:**

- `main.py:943-944`: `build_runtime()` crea un único `httpx.HTTPTransport()`.
- `main.py:1007`: lo guarda en `ChatRuntime.user_transport`.
- `api/user_auth.py:421`: cada request obtiene ese transporte compartido.
- `api/user_auth.py:270-279`: se pasa al `OdooClient` de la request.
- `odoo/client.py:100`: `httpx.Client(timeout=timeout, transport=transport)`.
- `odoo/client.py:113-114`: `OdooClient.close()` llama a `self._http.close()`.
- `api/user_auth.py:435-439` y `:446-450`: el `finally` cierra el cliente de cada request.
- `httpx.Client.close()` cierra el transporte y su pool.
- `main.py:139-149`: `ChatRuntime.close()` no cierra `user_transport`.

**Impacto:**

- El primer request que termina cierra el pool compartido. Los requests concurrentes pueden ver conexiones cerradas por debajo, errores de pool o, como mínimo, pérdida total de keep-alive.
- El síntoma esperable es intermitente: funciona con poca concurrencia y falla bajo carga.
- En el shutdown, el transporte compartido no se cierra explícitamente (fuga menor de recursos).

**Recomendación:**

- No cerrar el transporte compartido desde el `OdooClient` de cada request. Opciones:
  1. crear un `httpx.Client` por request sin transporte compartido; o
  2. usar un transporte compartido con contador de referencias y cerrarlo solo en el shutdown del runtime; o
  3. inyectar un `httpx.Client` por request y dejar que su ciclo de vida sea el del request.
- Agregar un test de concurrencia con dos requests solapados y un transporte instrumentado.

---

#### AL-03 — `/chat` no tiene manejo global de errores: traceback a stdout y 500 opaco

**Evidencia:**

- `api/routes.py:90-102`: el turno se ejecuta en `run_in_threadpool`; cualquier excepción se imprime con `print(f"CHAT ERROR: ...")` y `traceback.print_exc()`, y se re-lanza.
- `main.py:1060-1062`: el único handler global es para `ServiceUnavailable`; no hay handler para `Exception`.
- `graph/build.py:288-298`: el grafo sí captura excepciones y finaliza en `upstream_error`, pero eso no cubre fallos fuera del grafo (dependencias, threadpool, serialización, errores inesperados del runtime).
- `graph/classify.py:72-77` (ruta legacy/exportada): además de imprimir, devuelve `_failure(str(exc))`, y ese mensaje puede terminar en `ChatResponse.warnings` (`graph/response.py:103-116`).

**Impacto:**

- El cliente recibe un 500 genérico de Starlette, sin `request_id`, sin código de error estable y sin mensaje en español.
- El traceback se escribe en stdout, fuera del logger estructurado y sin redacción; puede incluir mensajes de excepción con datos de Odoo o del usuario.
- La ruta legacy `graph/classify.classify()` puede exponer `str(exc)` en un warning de la respuesta. En producción el clasificador usado es `_LazyOpenAIClassifier`, que devuelve un mensaje fijo (`main.py:849-860`), por lo que el riesgo es menor en el camino principal, pero la función sigue exportada y es fácil de reutilizar por error.

**Recomendación:**

- Agregar un `@app.exception_handler(Exception)` que:
  - genere/loguee un `request_id`;
  - devuelva `503` o `500` con un payload estable en español;
  - no incluya `str(exc)` ni traceback en la respuesta.
- Reemplazar `print()` + `traceback.print_exc()` por `logger.exception()` con el logger redactor.
- En `graph/classify.py`, no incluir `str(exc)` en el warning; usar un mensaje fijo y loguear el detalle solo del lado servidor.

---

#### AL-04 — `/digitize` colapsa todos los fallos de NIM en un 503 genérico

**Evidencia:**

- `api/routes.py:160-165`: `NIMTimeoutError`, `NIMParsingError` y `NIMError` se traducen todos a `HTTPException(503, detail="service_unavailable")`.
- `api/routes.py:42-52`: existe `_digitize_error_detail()` con mensajes accionables en español, pero solo se usa en `/digitize_batch`.
- `api/digitize.py:200-209`: los errores de NIM se clasifican y se re-lanzan con mensajes internos que el route descarta.
- `api/routes.py:166-168`: `ValueError` se devuelve como `400` con `str(e)`.

**Impacto:**

- El usuario de `/digitize` no sabe si debe reintentar (timeout), corregir la imagen (parsing) o reportar una caída del proveedor.
- El cliente no puede distinguir entre timeout, respuesta ilegible y error upstream; todos son `503 service_unavailable`.
- No hay `Retry-After` ni código de error estable.
- El batch sí da mensajes útiles, lo que genera una experiencia inconsistente entre endpoints.

**Recomendación:**

- Reutilizar `_digitize_error_detail()` también en `/digitize`, o devolver un contrato con `code` (`nim_timeout`, `nim_parsing`, `nim_unavailable`, `invalid_request`) y `message` en español.
- Agregar `Retry-After` para timeouts y errores transitorios.
- No devolver `str(e)` para `ValueError`; mapear a un mensaje seguro y loguear el detalle redactado.

---

#### AL-05 — `/digitize_batch` es una operación síncrona de hasta decenas de minutos

**Evidencia:**

- `api/routes.py:171-225`: el lote se procesa secuencialmente dentro del request HTTP.
- `api/routes.py:193-218`: por cada imagen se llama `run_in_threadpool(service.digitize, ...)` y se espera `asyncio.sleep(2.0)` entre imágenes.
- `api/routes.py:179-184`: el propio docstring reconoce ~30–60 s por imagen y ~10–20 minutos por lote de 20.
- `config.py:101`: `digitize_timeout_seconds` default 90 s, máximo 120 s.
- `api/digitize.py:126-136`: `max_retries=1` en el cliente de visión.
- `domain/digitize.py:188,211-219`: hasta 20 imágenes por lote.

**Impacto:**

- En el peor caso (timeouts + reintentos), un lote puede durar **30–60 minutos** y mantener la request abierta.
- Los proxies, balanceadores y el cliente suelen cortar antes; el trabajo del servidor puede continuar aunque el cliente ya se haya ido, porque no hay cancelación cooperativa.
- Un solo lote ocupa un worker del threadpool durante cada imagen; varios lotes concurrentes pueden saturar el pool y afectar `/chat` y `/health`.
- No hay cola, job id, estado parcial consultable ni reanudación.

**Recomendación:**

- Convertir el batch en un job asíncrono (cola + `job_id` + endpoint de estado) o, como mínimo:
  - reducir el máximo de imágenes;
  - imponer un deadline total del lote;
  - cancelar si el cliente se desconecta;
  - devolver `202 Accepted` con progreso.
- Mientras no exista cola, documentar el timeout mínimo del proxy y limitar el batch a un tamaño que quepa en él.

---

#### AL-06 — No hay límite de tamaño para body, imagen o lote

**Evidencia:**

- `domain/digitize.py:16-23`: `image_base64` solo tiene `min_length=1`; no hay `max_length`.
- `domain/digitize.py:211-219`: el lote limita la cantidad a 20, pero no el tamaño de cada imagen.
- `config.py:102`: `digitize_max_image_mb` existe (default 5, máximo 20), pero no se usa en ninguna parte del flujo.
- `api/routes.py:77-83`: `/chat` lee `await request.body()` completo y recién después valida `max_message_chars`.
- `api/routes.py:145-168`: `/digitize` recibe el modelo Pydantic ya parseado, sin límite de bytes.
- No hay middleware de `Content-Length` ni límite de body en FastAPI/Uvicorn.

**Impacto:**

- Un atacante puede enviar una imagen base64 arbitrariamente grande (o 20) y agotar memoria del proceso.
- En `/chat`, un body enorme se carga en memoria antes de que `validate_chat_request` lo rechace por longitud.
- La combinación con CR-01 (staging sin API key) convierte esto en un vector de DoS simple.
- `digitize_max_image_mb` da una falsa sensación de control: está configurado pero no se aplica.

**Recomendación:**

- Validar `Content-Length` y tamaño real del body en un middleware antes de parsear.
- Agregar `max_length` a `image_base64` calculado desde `digitize_max_image_mb` (base64 ≈ 4/3 del binario) y validar el tamaño decodificado.
- Limitar el tamaño total del lote (suma de imágenes), no solo la cantidad.
- Para `/chat`, leer el body con un límite o validar `Content-Length` antes de `request.body()`.

---

#### AL-07 — PII de facturas en logs y logger estructurado desconectado

**Evidencia:**

- `api/digitize.py:216`: `logger.warning("NIM RAW RESPONSE: %s", content[:2000])`.
- `api/digitize.py:221`: `logger.error("Respuesta NIM no es JSON válido: %s", content[:500])`.
- `api/digitize.py:202-205`: `logger.exception(...)` y `logger.warning("NIM raw error: ...", e)` sobre el error del proveedor.
- `observability/logging.py:55-74`: `configure_logging()` existe, pero no se llama en el arranque.
- `observability/logging.py:91,124,151`: `log_turn`, `log_tool_result_summary` y `log_odoo_error` no tienen call sites en `src/` (verificado por búsqueda).
- `observability/redaction.py:29-33`: la regla de headers no incluye `x-odoo-session`.
- `observability/redaction.py:40-43`: `token` sí está cubierto por la regla clave-valor.

**Impacto:**

- Las respuestas crudas de NIM pueden contener nombre del proveedor, RIF, dirección, números de factura, montos y líneas de detalle. Quedan en los logs de Render, que suelen retenerse y ser accesibles a más personas que la base de datos.
- El logger por defecto de Uvicorn no aplica `JsonFormatter` ni `redact_text`.
- La promesa de RNF-005/008/015 (redacción en logs, warnings y trazas) no se cumple en el camino de Digitize.
- `X-Odoo-Session` no se enmascara si algún log futuro incluye headers.

**Recomendación:**

- Llamar a `configure_logging()` en el arranque de la app y en el CLI de ingesta.
- Eliminar los logs de respuesta cruda; loguear solo `request_id`, `document_type`, `confidence`, longitud y códigos de warning.
- Agregar `x-odoo-session` y `x-odoo-session-id` a `_HEADER`.
- Auditar los `logger.*` existentes para asegurar que pasan por el redactor.

---

#### AL-08 — Sin deadline global ni circuit breaker; `RETRIEVAL_TIMEOUT_SECONDS` no se usa

**Evidencia:**

- `config.py:56`: `llm_timeout_seconds=60`.
- `main.py:784-790`: clasificador estructurado con `max_retries=2`.
- `main.py:797-804`: fallback JSON con `max_retries=2`.
- `main.py:898-905`: Answer Composer con `max_retries=2`.
- `main.py:817-830`: si el structured output falla, se resetea `_structured_model` y **cada request** vuelve a intentar structured antes del fallback.
- `config.py:70`: `retrieval_timeout_seconds=10`, sin uso en `rag/retriever.py`.
- `rag/retriever.py:85-95`: el embed y la query a Chroma están dentro de un `try`, pero sin timeout explícito.
- `api/digitize.py:126-136`: timeout por llamada de 90 s y `max_retries=1`.
- No hay `asyncio.wait_for` ni deadline por turno en `/chat`; `/digitize` solo tiene timeout por llamada.

**Impacto:**

- Un turno de chat puede acumular: clasificador estructurado (hasta 3 intentos × 60 s) + fallback JSON (hasta 3 × 60 s) + compositor (hasta 3 × 60 s), además de Odoo y RAG. En el peor caso supera los **9 minutos** antes de responder o fallar.
- El cliente/proxy puede cortar antes y el servidor sigue trabajando.
- No hay circuit breaker: si NIM está caído, cada request paga el timeout completo.
- `RETRIEVAL_TIMEOUT_SECONDS` no protege nada; el embedding de NVIDIA puede esperar hasta 60 s por su default interno.
- El reset de `_structured_model` en cada fallo convierte un modelo sin soporte de structured output en **dos llamadas por turno** de forma permanente.

**Recomendación:**

- Definir un deadline por turno (por ejemplo, 30–45 s para chat) y envolver la ejecución en `asyncio.wait_for` o en un `httpx.Timeout` global.
- Implementar un circuit breaker por dependencia (NIM, Odoo, Chroma) con estado abierto/medio abierto y métricas.
- Usar `retrieval_timeout_seconds` en el embed y en la query; si no se puede cancelar la librería, correr el retrieval en un thread con timeout y descartar el resultado.
- Cachear el resultado negativo de structured output (por modelo) durante un TTL, en lugar de reintentarlo en cada request.

---

#### AL-09 — `ThreadStore` global, `thread_id` predecible y metadatos con PII

**Evidencia:**

- `main.py:965-968`: se crea un único `ThreadStore` para todo el proceso.
- `main.py:258`: `chat_with_user` usa `self.store` sin segmentar por usuario.
- `main.py:229-232`: el docstring reconoce que el store segmentado por `user_key` “se implementará en T03”.
- `main.py:431`: comentario `# T03: reemplazar por store segmentado por user_key`.
- `domain/contracts.py:30`: `THREAD_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"`, no un UUID4.
- `domain/request.py:76-88`: se acepta cualquier `thread_id` que cumpla ese patrón.
- `memory/threads.py:61-66`: `ThreadTurn` guarda `EntityRef`, `ThreadFilters` y `TimeRange`.
- `domain/contracts.py:62-71`: `EntityRef` incluye `name`, `company_name` y `rif`.

**Impacto:**

- Un usuario autenticado puede elegir `thread_id` como `"default"`, `"chat1"` o cualquier valor predecible y **colisionar con el historial de otro usuario**.
- El historial no guarda el texto del mensaje, pero sí la entidad (nombre/RIF), filtros y período. Ese contexto puede influir en el siguiente turno y potencialmente reflejarse en entidades/opciones de la respuesta.
- Un usuario puede crear 500 hilos y expulsar por LRU los hilos legítimos de otros (cache poisoning / denegación de continuidad).
- No hay TTL: los hilos permanecen hasta que el LRU los expulsa.
- El aislamiento multi-tenant depende de que el `thread_id` sea impredecible, pero el contrato permite cualquier cadena de 1–64 caracteres.

**Recomendación:**

- Segmentar el store por `user_key` (o `(user_key, thread_id)`) antes de exponer multi-usuario.
- Exigir UUID4 o un token firmado (HMAC) para `thread_id`; rechazar IDs predecibles.
- Agregar TTL y métricas de hilos/evicciones.
- Si se escala horizontalmente, mover el store a Redis/DB o fijar explícitamente una sola instancia.

---

#### AL-10 — No hay readiness real; `/livez` es el único health check

**Evidencia:**

- `main.py:113-120`: `ChatRuntime.startup()` intenta abrir el corpus y **ignora cualquier excepción**.
- `main.py:126-134`: en producción solo falla si el scope es `scope_unsafe` o no hay snapshot; no exige corpus.
- `main.py:486-512`: `/health` devuelve `degraded` con **HTTP 200** si el corpus no está `ok`, si Odoo no está disponible o si el scope no es completo.
- `main.py:463-531`: `/health` no verifica NIM ni el embedder.
- `render.yaml:6`: Render usa `healthCheckPath: /livez`.
- `Dockerfile:46-50`: el HEALTHCHECK también usa `/livez`.
- `api/routes.py:125-129`: `/livez` solo devuelve `{"status":"alive"}`.
- `docs/DEPLOY.md:16,99,125,191,297,321,352,459,481` documenta `/readyz`, que no existe.

**Impacto:**

- Una instancia sin corpus, con Chroma roto o con credenciales NIM inválidas puede arrancar y ser marcada “healthy”.
- Render enviará tráfico a esa instancia; el usuario verá respuestas degradadas o 500.
- No hay un punto de control para un deploy que verifique la disponibilidad real del corpus/embedder.
- El runbook pide verificar `/readyz`, que dará 404 (o 401 si el middleware está activo y no se envía la key).

**Recomendación:**

- En producción, fallar el arranque si el corpus no está `ok` o si el manifest no coincide.
- Crear `/readyz` (o usar `/health`) con verificación de corpus, embedder y configuración de NIM; no llamar a NIM en cada probe, pero sí validar credenciales/URL/modelo.
- Mantener `/livez` para liveness y usar `/readyz` para el deploy gate.
- Actualizar `render.yaml` y `docs/DEPLOY.md` para reflejar el endpoint real.

---

#### AL-11 — El blueprint de staging es incompatible con las validaciones y debilita los guards

**Evidencia:**

- `render.yaml:76-78`: `MAX_TOOL_CALLS=8`.
- `config.py:89`: `max_tool_calls: int = Field(default=3, ge=1, le=3)`.
- Verificación con `.venv`: `Settings(max_tool_calls=8)` lanza `ValidationError` con `less_than_equal`.
- `render.yaml:52-54`: `APP_ENV=development`.
- `render.yaml:64-66`: `ODOO_READONLY_ATTESTED=false`.
- `render.yaml:40-42`: `LANGSMITH_HIDE_CONTENT=false`.
- `render.yaml:58-59`: `LLM_MODEL=meta/llama-3.2-90b-vision-instruct`.
- `odoo/scope.py:61-68`: en `development`, `tools_enabled` sigue siendo `True` para `scope_unsafe` por admin o falta de atestación.
- `main.py:126-134` y `main.py:238-240`: los bloqueos de `scope_unsafe` son solo para `production`.

**Impacto:**

- Si Render aplica `MAX_TOOL_CALLS=8`, la app **no arranca**; el deploy falla con un error de validación de Pydantic. Si el servicio está corriendo, probablemente el panel tenga otro valor y el blueprint ya no refleje la realidad.
- `APP_ENV=development` desactiva los guards de producción y permite ejecutar tools con scope `unsafe` (por ejemplo, API key admin o falta de atestación read-only), algo aceptable en local pero riesgoso en un staging público.
- `LANGSMITH_HIDE_CONTENT=false` envía prompts y respuestas a LangSmith, incluyendo posibles datos fiscales.
- `LLM_MODEL` apunta a un modelo de visión para tareas de texto (clasificador/compositor) porque `CLASSIFIER_MODEL` y `ANSWER_MODEL` no están definidos.

**Recomendación:**

- Corregir `MAX_TOOL_CALLS` a un valor ≤ 3 o ampliar el límite del campo con una justificación explícita y tests.
- Separar el blueprint de staging del de producción y no usar `APP_ENV=development` en un entorno público.
- Activar `LANGSMITH_HIDE_CONTENT=true` por defecto; habilitar contenido solo en local.
- Definir `CLASSIFIER_MODEL` y `ANSWER_MODEL` dedicados; no usar el modelo de visión para texto.
- Agregar una validación de CI que cargue el blueprint en un `Settings` de prueba y falle si es inconsistente.

---

### 5.3 Medio

#### ME-01 — El parser de Digitize es frágil ante JSON válido pero inesperado

**Evidencia:**

- `api/digitize.py:218-222`: `json.loads(content)` directo, sin extraer bloques ```json ni prosa.
- `api/digitize.py:236-249`: `_normalize_nim_response` asume `dict`; si NIM devuelve una lista o un string JSON válido, `"extracted" in parsed` o `dict(parsed)` pueden lanzar `TypeError`/`ValueError`.
- `api/routes.py:158-168`: solo se capturan `NIMTimeoutError`, `NIMParsingError`, `NIMError` y `ValueError`; un `TypeError` o `AttributeError` termina en 500.
- `api/digitize.py:385`: `parsed.get(...)` asume dict.

**Impacto:**

- Un cambio de formato del proveedor (markdown, lista, respuesta plana) puede convertir un error de parsing controlado en un 500.
- El clasificador de chat sí tiene `_extract_json_object` (`main.py:726`), pero Digitize no usa una estrategia equivalente.

**Recomendación:**

- Extraer el primer objeto JSON de la respuesta (fences, prosa, etc.) antes de `json.loads`.
- Validar el tipo (`dict`) y mapear a `NIMParsingError` si no lo es.
- Agregar tests con respuestas NIM: fenced JSON, lista, string, JSON plano, JSON anidado.

---

#### ME-02 — El timeout de NIM se detecta por substring

**Evidencia:**

- `api/digitize.py:207-209`: `if "timeout" in str(e).lower() or "timed out" in str(e).lower(): raise NIMTimeoutError(...)`.
- Cualquier otro error se convierte en `NIMError` genérico.

**Impacto:**

- Un `openai.APIConnectionError` o un error de DNS que no incluya “timeout” se clasifica como fallo genérico, no como transitorio.
- Un mensaje de error que contenga “timeout” por casualidad se clasifica como timeout.
- La causa real se pierde para el cliente y para las métricas.

**Recomendación:**

- Clasificar por tipo (`openai.APITimeoutError`, `openai.APIConnectionError`, `httpx.TimeoutException`, `httpx.TransportError`) y usar el texto solo como fallback.
- Adjuntar un `code` estable a cada excepción.

---

#### ME-03 — El clasificador reintenta structured output en cada request

**Evidencia:**

- `main.py:817-830`: si `self._structured().invoke(...)` falla por cualquier motivo, se hace `self._structured_model = None` y se cae al modelo JSON.
- En el siguiente request, `_structured()` se vuelve a crear y se vuelve a intentar structured output.
- `main.py:784-790`: `max_retries=2`.
- `main.py:797-804`: el fallback también tiene `max_retries=2`.

**Impacto:**

- Si el modelo desplegado no soporta structured output, **todos** los turnos pagan primero el intento fallido (con reintentos) y luego el fallback. Esto duplica latencia y costo.
- No hay memoria de la capacidad del modelo ni un flag de “structured no soportado”.

**Recomendación:**

- Cachear el resultado negativo por modelo/base URL durante un TTL (o hasta reinicio).
- Detectar la capacidad en el arranque con un smoke test opcional.
- Bajar `max_retries` en el intento estructurado y reservar reintentos para el fallback.

---

#### ME-04 — `X-Odoo-Session` no está cubierto por la redacción

**Evidencia:**

- `observability/redaction.py:29-33`: la regla `_HEADER` incluye `authorization`, `x-api-key`, `x-auth-token`, `x-odoo-database`, `cookie`, `set-cookie`, `x-forwarded-for`, pero **no** `x-odoo-session`.
- `observability/redaction.py:40-43`: `_KEY_VALUE` sí cubre `token`, por eso `X-User-Token` se enmascara.
- Verificación con `.venv`: `redact_text("X-Odoo-Session: <uuid>")` devuelve el header sin cambios.

**Impacto:**

- Si un log, warning o traza incluye headers de la request, el `session_id` de Odoo puede quedar en claro.
- Es una debilidad de defensa en profundidad: hoy no se loguea el header explícitamente, pero cualquier log futuro o excepción que lo incluya lo filtraría.

**Recomendación:**

- Agregar `x-odoo-session` y `x-odoo-session-id` a `_HEADER`.
- Agregar un test unitario de redacción para ese header.

---

#### ME-05 — `LANGSMITH_HIDE_CONTENT=false` en staging

**Evidencia:**

- `render.yaml:40-42`: `LANGSMITH_HIDE_CONTENT=false`.
- `observability/tracing.py:32-35`: `HIDE_CONTENT_ENV` define `LANGSMITH_HIDE_INPUTS=true` y `LANGSMITH_HIDE_OUTPUTS=true`.
- `observability/tracing.py:134-145`: si `langsmith_hide_content` es falso, no se aplican esas variables.
- `observability/tracing.py:148-177`: `langsmith_env()` combina la configuración con `hide_content_env`.

**Impacto:**

- Los prompts, respuestas y fragmentos RAG pueden quedar visibles en LangSmith.
- En staging se suelen probar datos reales; el contenido puede incluir nombres, RIF, montos o texto de facturas.
- El default del código es `True`; el blueprint lo desactiva explícitamente.

**Recomendación:**

- Poner `LANGSMITH_HIDE_CONTENT=true` en staging y producción.
- Si se necesita debug con contenido, usar un proyecto LangSmith separado, con retención corta y acceso restringido, nunca el proyecto compartido.

---

#### ME-06 — Sin rate limiting, CORS ni validación de HTTPS

**Evidencia:**

- No hay middleware de rate limit en `main.py`.
- No hay configuración de CORS en `create_app` (`main.py:1029-1065`).
- `config.py:34`: `odoo_url: str`; `config.py:44`: `openai_base_url: str`, sin validar esquema `https`.
- `Dockerfile:54`: el servicio escucha en `0.0.0.0`.

**Impacto:**

- Un cliente autenticado puede martillar `/chat` o `/digitize` y consumir cuota de LLM/NIM y conexiones Odoo.
- Si el widget Odoo llama desde el navegador a otro origen, la falta de CORS puede romper la integración; si se agrega CORS de forma amplia, se abre otra superficie.
- Una `ODOO_URL` o `OPENAI_BASE_URL` en `http://` envía credenciales/datos sin cifrar.

**Recomendación:**

- Agregar rate limiting por usuario/API key y por IP en el borde.
- Configurar CORS con una allowlist explícita de orígenes del widget.
- Validar que `ODOO_URL` y `OPENAI_BASE_URL` usen `https://` en producción (permitir `http://localhost` solo en desarrollo).
- Documentar el TLS termination y el forwarding de headers.

---

#### ME-07 — `API_SHARED_SECRET` sin requisitos de entropía ni rotación

**Evidencia:**

- `config.py:104-111`: solo se normaliza vacío a `None`.
- `config.py:125-141`: no hay longitud mínima, complejidad ni comparación contra una lista de valores débiles.
- `api/security.py:33-34`: si el secreto es `None`, no hay autenticación.

**Impacto:**

- Un secreto corto o predecible es vulnerable a fuerza bruta si el endpoint está expuesto.
- No hay procedimiento de rotación ni soporte para dos secretos válidos durante la transición.
- Un `API_SHARED_SECRET=""` se convierte silenciosamente en “sin autenticación”.

**Recomendación:**

- Exigir longitud mínima (por ejemplo, 32 bytes aleatorios) y rechazar valores débiles en producción.
- Soportar rotación con `API_SHARED_SECRET` y `API_SHARED_SECRET_PREVIOUS` durante una ventana.
- Documentar la rotación en el runbook.

---

#### ME-08 — Excepciones silenciadas sin logging

**Evidencia:**

- `main.py:117-120`: fallo al abrir corpus → `pass`.
- `main.py:537-540`: fallo al leer scope → `[]`.
- `api/user_auth.py:313-314` y `:387-388`: fallo al consultar `res.company` → `companies = []`.
- `graph/build.py:288-298`: cualquier excepción del grafo → `upstream_error` sin log.
- `rag/retriever.py:92-95` y `:128-133`: fallos de dense/BM25 → warning, sin log.

**Impacto:**

- Los fallos de infraestructura se vuelven invisibles en logs; solo se ven como respuestas degradadas.
- Un corpus roto o una consulta de compañías fallida puede producir resultados incompletos sin una señal clara para on-call.
- Dificulta distinguir “no hay datos” de “la dependencia falló”.

**Recomendación:**

- Loguear cada excepción silenciada con `logger.warning(..., exc_info=True)` y el `request_id`.
- Mantener la degradación controlada, pero con métricas y trazas.
- Agregar contadores de fallos por dependencia.

---

#### ME-09 — `docs/DEPLOY.md` está desactualizado y contradice el código

**Evidencia:**

- `docs/DEPLOY.md:16,99,125,191,297,321,352,459,481`: referencia `/readyz`, que no existe; el endpoint real es `/health` (`api/routes.py:132`).
- `docs/DEPLOY.md:129,342`: referencia `POST /v1/chat`; el endpoint real es `POST /chat` (`api/routes.py:71`).
- `docs/DEPLOY.md:171`: recomienda `numInstances: 2+` para producción, pero `render.yaml:7` usa 1 y el `ThreadStore` es in-process.
- `docs/DEPLOY.md:72,84`: documenta `LANGSMITH_HIDE_CONTENT=false` y `MAX_TOOL_CALLS=8`, ambos problemáticos (ME-05, AL-11).
- `docs/DEPLOY.md:201`: dice que `/readyz` devolverá `corpus_status: missing`; el endpoint no existe.

**Impacto:**

- El on-call ejecuta comandos que fallan o interpretan mal el estado.
- Un operador puede escalar a 2 instancias siguiendo el runbook y romper la continuidad de hilos.
- Los valores de configuración documentados pueden tumbar el arranque (`MAX_TOOL_CALLS=8`).

**Recomendación:**

- Reemplazar `/readyz` por `/health` o implementar `/readyz`.
- Reemplazar `/v1/chat` por `/chat`.
- Corregir la recomendación de instancias y documentar la dependencia del store in-process.
- Agregar una verificación de CI que compare los endpoints documentados con las rutas de FastAPI.

---

#### ME-10 — Modelo de visión usado para tareas de texto

**Evidencia:**

- `render.yaml:58-59`: `LLM_MODEL=meta/llama-3.2-90b-vision-instruct`.
- `config.py:46-53`: `answer_model` y `classifier_model` son opcionales y caen a `llm_model`.
- `main.py:784-790` y `:898-905`: clasificador y compositor usan `classifier_model_id` / `answer_model_id`.
- `config.py:153-161`: ambos properties caen a `llm_model` si no se definen.

**Impacto:**

- El clasificador y el compositor usan un modelo de visión de 90B para texto, con mayor latencia y costo que un modelo de texto dedicado.
- El `LLM_MODEL` también se usa en `/digitize`, así que cambiarlo por un modelo de texto rompería la digitalización si no se separan las variables.

**Recomendación:**

- Definir `CLASSIFIER_MODEL` y `ANSWER_MODEL` en staging/producción.
- Mantener `LLM_MODEL` como modelo de visión para Digitize o renombrarlo a `VISION_MODEL` para evitar ambigüedad.
- Documentar la matriz de modelos por caso de uso.

---

#### ME-11 — `ValueError` con detalle interno/PII en respuestas 400 y batch

**Evidencia:**

- `api/routes.py:166-168`: `/digitize` devuelve `HTTPException(400, detail=str(e))`.
- `api/routes.py:50-51`: `_digitize_error_detail()` devuelve `str(exc)` para cualquier `ValueError`.
- `api/digitize.py:333-367`: `_parse_extracted` puede lanzar `ValueError` desde `float(...)` con el valor de una línea, o `ValidationError` (subclase de `ValueError`) con los campos extraídos.
- `domain/digitize.py:36-42`: el validador de base64 lanza `ValueError("image_base64 no es base64 válido")` (mensaje seguro).

**Impacto:**

- Un error de validación de la factura puede devolver al cliente (y al campo `error` del batch) nombres, RIF, montos o líneas crudas.
- Aunque el cliente envió la imagen, el detalle interno facilita abuso y puede quedar registrado en logs del cliente.
- No hay un código de error estable para distinguir “base64 inválido” de “campos extraídos inválidos”.

**Recomendación:**

- Mapear `ValueError`/`ValidationError` a mensajes seguros y códigos estables.
- Loguear el detalle redactado solo del lado servidor.
- No devolver `str(e)` en producción.

---

#### ME-12 — `DigitizeService` se crea por request y no reutiliza el cliente LLM

**Evidencia:**

- `api/digitize.py:420-422`: `get_digitize_service(settings)` devuelve una instancia nueva.
- `api/routes.py:156` y `:187`: se llama en cada request.
- `api/digitize.py:120-137`: `_llm` se cachea en la instancia, pero la instancia muere al terminar el request.

**Impacto:**

- Se crea un `ChatOpenAI` nuevo por request, sin reutilizar el pool de conexiones ni el cliente HTTP.
- Aumenta la latencia de la primera llamada y el consumo de recursos.
- El cache `_llm` solo sirve dentro del request (y solo hay una llamada por request), así que es redundante.

**Recomendación:**

- Crear un `DigitizeService` singleton por runtime (o cachearlo por `Settings`), igual que el clasificador y el compositor.
- Si se necesita aislamiento, usar un pool de clientes, no una instancia por request.

---

#### ME-13 — `ThreadStore` sin TTL y con expulsión fácil por LRU

**Evidencia:**

- `memory/threads.py:91-112`: LRU con `max_threads=500`, `max_turns=20`, `evicted_limit=1000`.
- `memory/threads.py:189-199`: al crear un hilo se expulsa el más antiguo.
- No hay TTL por inactividad; el único mecanismo de salida es la presión de LRU.
- `domain/contracts.py:30`: IDs de 1–64 caracteres, fáciles de generar en masa.

**Impacto:**

- Un usuario autenticado puede crear 500 hilos y expulsar los de otros, forzando `thread_context_unavailable` o `thread_evicted`.
- Los hilos viejos permanecen en memoria hasta que el LRU los saca, aunque nadie los use.
- En un servicio multi-tenant, esto es una denegación de continuidad de bajo costo.

**Recomendación:**

- Agregar TTL por último uso y un límite por usuario.
- Segmentar por `user_key` (ver AL-09).
- Métricas de `thread_count`, evictions y edad del hilo más antiguo.

---

### 5.4 Bajo

#### BA-01 — `print()` y logging no estructurado en código de producción

**Evidencia:**

- `api/routes.py:100-101`: `print` + `traceback.print_exc()`.
- `config.py:144-149`: warning de secreto ausente por `print`.
- `graph/classify.py:74`: `print` del error del clasificador.

**Impacto:** logs inconsistentes, sin `request_id`, sin redacción y difíciles de parsear.

**Recomendación:** usar `logger` con el formatter JSON y el redactor.

---

#### BA-02 — Comparación de enum por string en `main.py`

**Evidencia:**

- `main.py:238`: `snapshot.state.value == "scope_unsafe"` en lugar de `snapshot.state is ScopeState.scope_unsafe`.

**Impacto:** frágil ante refactors o cambios de valor; funcionalmente equivalente hoy.

**Recomendación:** usar la comparación de enum, como ya se hace en `main.py:131` y `:188`.

---

#### BA-03 — `/health` no incluye NIM ni embedder

**Evidencia:**

- `main.py:463-531`: el payload incluye scope, corpus, Chroma, BM25 y Odoo; no NIM/embedder.
- `main.py:504-512`: `degraded` sigue siendo 200.

**Impacto:** un operador no puede distinguir “todo ok” de “NIM caído” desde `/health`.

**Recomendación:** agregar flags de configuración/credenciales de NIM y embedder, sin hacer llamadas costosas en cada probe.

---

#### BA-04 — 503 sin `Retry-After` ni código estable

**Evidencia:**

- `api/routes.py:160-165`: 503 con `detail="service_unavailable"`.
- `api/routes.py:137-141`: `/health` 503 con el mismo detalle.
- `api/errors.py:13-15`: `ServiceUnavailable` sin metadatos.

**Impacto:** los clientes no saben cuándo reintentar ni cómo distinguir causas.

**Recomendación:** agregar `Retry-After` y un `code` estable en el payload.

---

#### BA-05 — El `request_id` del cliente se ignora

**Evidencia:**

- `api/routes.py:96`: siempre se genera `str(uuid.uuid4())`, aunque el cliente envíe uno.
- `ChatResponse.request_id` exige UUID4 (`domain/contracts.py:326-335`).

**Impacto:** no se puede correlacionar un request del cliente con los logs del servidor si el cliente ya tiene su propio ID.

**Recomendación:** aceptar un `X-Request-ID` validado (UUID4) y reflejarlo en la respuesta/logs; si no es válido, generar uno.

---

#### BA-06 — `ODOO_READONLY_ATTESTED=false` en staging

**Evidencia:**

- `render.yaml:64-66`.
- `config.py:133-136`: solo es obligatorio en producción.
- `odoo/scope.py:132-137`: sin atestación, el scope es `scope_unsafe` (razón `attestation`).

**Impacto:** en staging se puede operar con una API key no atestiguada; los guards de producción no aplican.

**Recomendación:** usar una API key read-only atestiguada también en staging, o aceptar explícitamente el riesgo en un entorno no público.

---

## 6. Respuestas a las preguntas de alcance

### 6.1 ¿Qué pasa si NIM falla o timeout? ¿Qué mensaje ve el usuario?

**Camino `/digitize`:**

1. `DigitizeService._get_llm()` construye `ChatOpenAI` con `timeout=digitize_timeout_seconds` (default 90 s, máximo 120 s) y `max_retries=1` (`api/digitize.py:126-136`).
2. `llm.invoke(messages)` es síncrono. Si lanza:
   - si el texto del error contiene `"timeout"` o `"timed out"` → `NIMTimeoutError`;
   - cualquier otro → `NIMError` (`api/digitize.py:200-209`).
3. Si la respuesta está vacía o no es string → `NIMParsingError` (`:211-213`).
4. Si el contenido no es JSON → `NIMParsingError` (`:218-222`).
5. Si faltan claves requeridas → `NIMParsingError` (`:229-232`).
6. El route traduce **los tres** a `503 {"detail": "service_unavailable"}` (`api/routes.py:160-165`). No hay mensaje en español, ni `Retry-After`, ni distinción de causa.

**Camino `/digitize_batch`:**

- Cada ítem captura cualquier excepción y usa `_digitize_error_detail()` (`api/routes.py:42-52, 193-205`):
  - timeout → “El servicio de digitalización tardó demasiado con esta imagen.”
  - parsing → “El servicio devolvió una respuesta ilegible para esta imagen.”
  - NIM genérico → “El servicio de digitalización no pudo procesar esta imagen.”
  - `ValueError` → `str(exc)` (riesgo ME-11).
  - otro → “Error inesperado al digitalizar esta imagen.”
- El lote continúa con las demás imágenes.

**Camino `/chat`:**

- El clasificador `_LazyOpenAIClassifier` captura cualquier excepción y devuelve `ClassificationResult(status=upstream_error)` con un mensaje fijo (“No se pudo clasificar la consulta.”) (`main.py:849-860`).
- `graph/build.py:288-298` captura excepciones del grafo y finaliza en `upstream_error`.
- `render_answer` produce un mensaje determinista en español: “No pude completar la consulta porque una fuente no está disponible...” (`domain/answer.py:340-345`).
- El compositor tiene fallback determinista si el LLM falla (`domain/answer.py:590-592`).
- **Excepción:** fallos fuera del grafo (por ejemplo, en la dependency de autenticación o en el threadpool) pueden terminar en el 500 opaco de AL-03, porque `/chat` no tiene handler global.

**Conclusión:** el chat está bien protegido a nivel de dominio; Digitize no. El usuario de `/digitize` recibe un 503 genérico y no puede accionar. El batch es mejor en mensajes, pero peor en latencia y volumen.

---

### 6.2 ¿`API_SHARED_SECRET` protege todas las rutas? ¿Hay rutas sin autenticación?

**Rutas exentas del middleware:** solo `/livez` (`main.py:1047-1058`). `/docs`, `/openapi.json`, `/health`, `/scope`, `/chat`, `/digitize` y `/digitize_batch` pasan por el guard.

**Pero el guard falla abierto si no hay secreto:**

- `verify_api_key()` devuelve `True` cuando `expected` es `None` o vacío (`api/security.py:33-34`).
- `Settings` solo exige el secreto si `api_host` no es loopback (`config.py:125-128`) o si `APP_ENV=production` (`:138-141`).
- El Dockerfile escucha en `0.0.0.0` (`Dockerfile:54`), pero `Settings.api_host` sigue siendo `127.0.0.1` (`config.py:83`), así que el validador no ve el bind real.
- Staging usa `APP_ENV=development` (`render.yaml:52-54`).

**Resultado:** si el secreto no está cargado en Render, **no hay autenticación de API key en ningún endpoint salvo `/livez`**. Las rutas de datos siguen requiriendo Odoo, pero la superficie pública y el consumo de recursos quedan abiertos.

**Recomendación:** fail-closed, validar el bind real y exigir el secreto en cualquier despliegue que no sea local/test.

---

### 6.3 ¿Qué tan acoplado está el servicio a NVIDIA? ¿Se puede pasar a Ollama u OpenAI?

**Chat, clasificador, compositor y Digitize:**

- Usan `ChatOpenAI` con `base_url` (`main.py:784-804`, `:898-905`; `api/digitize.py:126-136`).
- Eso es **OpenAI-compatible**: cambiar `OPENAI_BASE_URL` y `LLM_MODEL` permite apuntar a OpenAI, Ollama (`http://localhost:11434/v1`), vLLM, etc.
- Cuidado con:
  - `with_structured_output(ClassificationDraft)` (`main.py:790`): no todos los proveedores lo soportan.
  - `model_kwargs={"response_format": {"type": "json_object"}}` (`main.py:803`, `api/digitize.py:132-134`): muchos proveedores lo soportan, otros no.
  - `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` para Nemotron (`api/digitize.py:123-125`): específico de NIM.
  - El modelo de visión para `/digitize` debe ser multimodal; Ollama/OpenAI requieren un modelo equivalente.

**Embeddings:**

- Están **hard-codeados** a `NVIDIAEmbeddings`:
  - `main.py:700-707` (`_LazyEmbedder._ensure`).
  - `rag/ingest.py:321-351` (`build_default_embedder`).
- `embedding_dimensions=2048` y `chroma_collection="contafis_normativa_v1"` (`config.py:54-60`).
- Cambiar a OpenAI/Ollama implica:
  - cambiar el código o introducir una factory por proveedor;
  - definir nuevas dimensiones;
  - reingestar el corpus;
  - crear una colección nueva o migrarla;
  - validar que la fusión RRF y los umbrales sigan funcionando.

**Configuración:**

- `openai_api_key` es obligatorio (`config.py:43`), aunque el proveedor sea NIM.
- `render.yaml` declara `NVIDIA_API_KEY` y `OPENAI_API_KEY` por separado (`render.yaml:13-14,27-28`), pero el código solo lee `OPENAI_API_KEY`. Esto confunde la operación.
- `langchain-nvidia-ai-endpoints` solo se usa para embeddings; el chat ya es OpenAI-compatible.

**Conclusión:** el chat está **mayormente desacoplado** (configuración), pero los embeddings están **fuertemente acoplados** (código + dimensiones + corpus). Un fallback a Ollama/OpenAI para chat es viable con cambios de configuración y pruebas de compatibilidad; para embeddings requiere una abstracción y una reingesta.

---

### 6.4 ¿`ThreadStore` tiene una fuga de memoria?

**No hay una fuga no acotada.** El store está diseñado con límites:

- `max_threads=500` (`memory/threads.py:16,97`).
- `max_turns=20` (`:17,98`).
- `evicted_limit=1000` (`:99,195-199`).
- `RLock` (`:110`).
- LRU con `OrderedDict` (`:111,189-193`).
- `ThreadTurn` guarda solo `EntityRef`, `ThreadFilters` y `TimeRange` (`:61-66`), no el texto del mensaje.

El peor caso es acotado: 500 hilos × 20 turnos × objetos pequeños, más 1000 IDs de expulsados. No es un crecimiento sin límite.

**Pero hay riesgos reales distintos de la fuga:**

1. **Aislamiento multi-tenant:** el store es global y no está segmentado por `user_key` (`main.py:229-232,431`). El `thread_id` lo elige el cliente y el patrón permite cualquier cadena de 1–64 caracteres (`domain/contracts.py:30`). Un usuario puede colisionar o adivinar IDs y heredar contexto (entidad, filtros, período; `EntityRef` incluye nombre y RIF).
2. **Evicción dirigida:** 500 hilos son fáciles de crear; un usuario puede expulsar los hilos de otros.
3. **Sin TTL:** los hilos viejos permanecen hasta que el LRU los saca.
4. **Multi-instancia:** el store vive en el proceso y `UVICORN_WORKERS=1` (`main.py:73,1077`); `docs/DEPLOY.md:171` recomienda 2 instancias, lo que rompería la continuidad. El Dockerfile lo advierte (`Dockerfile:52`).

**Recomendación:** mantener los límites actuales, pero agregar segmentación por usuario, IDs firmados/UUID4, TTL y un store compartido si se escala.

---

## 7. Plan de acción priorizado

### P0 — Inmediato (0–48 h)

| Acción | Hallazgo | Criterio de cierre |
|---|---|---|
| Hacer `verify_api_key()` fail-closed si no hay secreto; permitir bypass solo en `APP_ENV=test` o flag local explícito. | CR-01 | Test: sin secreto → 401 en todas las rutas salvo `/livez`. |
| Validar el host real de bind y exigir `API_SHARED_SECRET` en Docker/Render. | CR-01 | El arranque falla si el bind no es loopback y no hay secreto. |
| Verificar/rotar `API_SHARED_SECRET` en el panel de Render; quitar `APP_ENV=development` de staging o forzar el secreto. | CR-01 | Staging responde 401 sin `X-API-Key`. |
| Revisar si `MAX_TOOL_CALLS=8` está aplicado; corregirlo a ≤ 3. | AL-11 | El servicio arranca con el blueprint tal como está. |

### P1 — Corto plazo (1–2 semanas)

| Acción | Hallazgo | Criterio de cierre |
|---|---|---|
| Timeout explícito en `_validate_session`; cache de sesión; no forzar `refresh` por request. | AL-01 | Odoo colgado no bloquea más de N segundos; menos RPCs por request. |
| Corregir el ciclo de vida del transporte compartido. | AL-02 | Test de concurrencia sin errores de pool; transporte cerrado solo en shutdown. |
| Handler global de excepciones en `/chat` con `request_id` y mensaje seguro. | AL-03 | 500/503 estable, sin traceback en respuesta. |
| Mensajes accionables y códigos estables en `/digitize`; `Retry-After`. | AL-04, BA-04 | Timeout/parsing/upstream distinguibles. |
| Límite de body, imagen y lote; usar `digitize_max_image_mb`. | AL-06 | Payload sobredimensionado → 413 antes de parsear. |
| Configurar logging JSON con redacción; eliminar logs de respuesta cruda; agregar `x-odoo-session`. | AL-07, ME-04 | No hay PII de factura en logs; test de redacción. |
| Deadline por turno y circuit breaker; usar `retrieval_timeout_seconds`. | AL-08 | Turno acotado; dependencia caída no bloquea. |
| Segmentar `ThreadStore` por `user_key`; IDs UUID4/firmados; TTL. | AL-09, ME-13 | Un usuario no puede leer/alterar el hilo de otro. |
| Readiness real y fallo de arranque sin corpus en producción. | AL-10 | `/readyz` o `/health` refleja corpus/embedder; deploy gate. |
| Corregir `render.yaml` y `docs/DEPLOY.md`. | AL-11, ME-09 | CI valida el blueprint y los endpoints documentados. |

### P2 — Medio plazo (2–4 semanas)

| Acción | Hallazgo | Criterio de cierre |
|---|---|---|
| Parser de Digitize robusto (fences, tipos, JSON inesperado). | ME-01 | Tests con respuestas NIM variadas. |
| Clasificación de errores NIM por tipo, no por substring. | ME-02 | `APITimeoutError`/`APIConnectionError` mapeados correctamente. |
| Cachear el fallo de structured output por modelo. | ME-03 | Un modelo sin structured no paga el intento en cada turno. |
| Rate limiting, CORS y validación HTTPS. | ME-06 | Límites por usuario/IP; orígenes permitidos; URLs https. |
| Secretos con entropía mínima y rotación. | ME-07 | Secreto débil rechazado; rotación documentada. |
| Logging de excepciones silenciadas. | ME-08 | Fallos de corpus/scope/companies visibles en logs/métricas. |
| Modelos dedicados para clasificador/compositor. | ME-10 | `CLASSIFIER_MODEL`/`ANSWER_MODEL` definidos; latencia/costo medidos. |
| Errores de validación sin `str(e)`. | ME-11 | 400 con código seguro; detalle solo en logs redactados. |
| `DigitizeService` singleton por runtime. | ME-12 | Cliente LLM reutilizado; menos latencia. |
| Abstracción de embeddings y plan de reingesta. | 6.3 | `EMBEDDING_PROVIDER` + colección/dimensiones versionadas. |

### P3 — Backlog (4+ semanas)

- Métricas y alertas por dependencia (NIM, Odoo, Chroma, BM25) y por estado de respuesta.
- Pruebas de carga y caos: Odoo lento, NIM 500/timeout, Chroma vacío, 20 lotes concurrentes.
- Contratos de compatibilidad para Ollama/OpenAI (structured output, JSON mode, visión).
- Runbook de rotación de secretos y de recuperación de corpus.
- Auditoría de retención de logs y de datos en LangSmith.
- Revisión de `thread_id` en el cliente Odoo: ¿se comparte entre usuarios?

---

## 8. Anexos

### A. Evidencia principal por archivo

| Archivo:línea | Qué muestra |
|---|---|
| `api/security.py:33-34` | Fail-open si no hay secreto. |
| `config.py:104-111` | Secreto vacío → `None`. |
| `config.py:125-128` | Secreto obligatorio solo si el host no es loopback. |
| `config.py:138-141` | Secreto obligatorio solo en producción. |
| `config.py:143-149` | Warning y continuar en desarrollo. |
| `Dockerfile:54` | Bind `0.0.0.0`. |
| `render.yaml:52-54` | Staging en `development`. |
| `main.py:1045-1058` | Middleware y allowlist `/livez`. |
| `api/user_auth.py:164-205` | Validación de sesión sin timeout explícito. |
| `api/user_auth.py:294-295`, `:357` | `refresh(force=True)` por request. |
| `main.py:943-944`, `:1007` | Transporte compartido. |
| `odoo/client.py:100`, `:113-114` | Cliente cierra el transporte. |
| `api/routes.py:90-102` | `/chat` imprime traceback y re-lanza. |
| `api/routes.py:160-168` | `/digitize` 503 genérico / 400 con `str(e)`. |
| `api/routes.py:171-225` | Batch secuencial de hasta 20 imágenes. |
| `api/digitize.py:126-136` | ChatOpenAI NIM, timeout 90 s, 1 retry. |
| `api/digitize.py:200-222` | Clasificación por substring y parseo JSON directo. |
| `api/digitize.py:216,221` | Logs de respuesta cruda. |
| `domain/digitize.py:16-23` | `image_base64` sin máximo. |
| `domain/digitize.py:188,211-219` | 20 imágenes por lote. |
| `config.py:70` | `retrieval_timeout_seconds` sin uso. |
| `config.py:102` | `digitize_max_image_mb` sin uso. |
| `main.py:784-804`, `:898-905` | Clasificador/compositor con reintentos. |
| `main.py:817-830` | Reset del structured model en cada fallo. |
| `main.py:849-860` | Fallback determinista del clasificador. |
| `graph/build.py:288-298` | Captura amplia del grafo. |
| `domain/answer.py:340-345` | Mensaje determinista para `upstream_error`. |
| `memory/threads.py:16-17,91-112,189-199` | LRU acotado. |
| `domain/contracts.py:30` | `thread_id` de 1–64 caracteres, no UUID4. |
| `domain/contracts.py:62-71` | `EntityRef` con nombre/RIF. |
| `main.py:229-232`, `:431` | Store global; segmentación pendiente T03. |
| `main.py:73`, `:1077` | Un solo worker. |
| `main.py:113-120` | Corpus abierto con excepción silenciada. |
| `main.py:463-531` | `/health` sin NIM/embedder; degradado 200. |
| `render.yaml:6` | Health check `/livez`. |
| `render.yaml:40-42` | `LANGSMITH_HIDE_CONTENT=false`. |
| `render.yaml:76-78` | `MAX_TOOL_CALLS=8`. |
| `config.py:89` | `max_tool_calls` con `le=3`. |
| `docs/DEPLOY.md:16,99,129,171,321-323` | Endpoints e instancias desactualizados. |
| `observability/redaction.py:29-33` | Header `x-odoo-session` no cubierto. |
| `observability/logging.py:55-74` | Logger estructurado no configurado. |
| `README.md:66` | Afirmación de fail-closed que no cubre staging. |

### B. Matriz de configuración relevante

| Variable | Default / código | Staging (`render.yaml`) | Riesgo |
|---|---|---|---|
| `APP_ENV` | `development` (`config.py:40`) | `development` | Desactiva guards de producción. |
| `API_HOST` | `127.0.0.1` (`config.py:83`) | No se define | El validador no ve el bind `0.0.0.0` del Dockerfile. |
| `API_SHARED_SECRET` | `None` (`config.py:85`) | `sync: false` | Fail-open si no se carga. |
| `MAX_TOOL_CALLS` | `3` con `le=3` (`config.py:89`) | `8` | Arranque falla si se aplica. |
| `LANGSMITH_HIDE_CONTENT` | `true` (`config.py:80`) | `false` | Contenido a LangSmith. |
| `LLM_MODEL` | Obligatorio (`config.py:45`) | `llama-3.2-90b-vision-instruct` | Modelo de visión para texto. |
| `EMBEDDING_MODEL` | Obligatorio (`config.py:54`) | `nvidia/nemotron-3-embed-1b` | Acoplamiento NVIDIA. |
| `DIGITIZE_MAX_IMAGE_MB` | `5` (`config.py:102`) | No se define | No se usa. |
| `RETRIEVAL_TIMEOUT_SECONDS` | `10` (`config.py:70`) | No se define | No se usa. |
| `UVICORN_WORKERS` | `1` (`main.py:73`) | Docker `--workers 1` | Sin escala horizontal. |
| `ODOO_READONLY_ATTESTED` | `false` (`config.py:39`) | `false` | Scope unsafe permitido en dev. |

### C. Controles positivos verificados

- **Sin secretos hardcodeados** en `src/` (búsqueda de asignaciones de API keys/tokens/passwords).
- **`.env` ignorado por git** (`git check-ignore` confirma `.gitignore:115`); `.env.example` usa placeholders.
- **`SecretStr`** para API keys y `hmac.compare_digest` para la API key (`api/security.py:5,33-36`).
- **Producción exige** `ODOO_READONLY_ATTESTED=true` y `API_SHARED_SECRET` (`config.py:133-141`).
- **OdooClient** con errores tipados, timeout de 15 s, un retry acotado y allowlist de RPC (`odoo/client.py:38,84-114,164-205`; `odoo/allowlist.py`).
- **ThreadStore** acotado con lock, LRU y límite de turnos.
- **Respuestas deterministas** para `invalid_request`, `upstream_error` y `partial` (`domain/answer.py:271-275,319-323,340-348`).
- **Fallback determinista** del compositor si el LLM falla (`domain/answer.py:590-592`).
- **LangSmith metadata-only** por defecto (`observability/tracing.py:20-35,134-145`).
- **Redacción** de RIF, email, teléfono, documento, URL, tokens y headers sensibles (`observability/redaction.py`).
- **Docker** no-root, copias explícitas, sin `.env`, healthcheck `/livez` (`Dockerfile:26-50`).
- **Middleware** protege `/docs` y `/openapi.json` (además de las rutas de negocio), salvo que el secreto falte.

### D. Checklist de verificación en staging

- [ ] Confirmar si `API_SHARED_SECRET` está cargado en Render. Si no, el staging está abierto.
- [ ] Confirmar si `MAX_TOOL_CALLS=8` está aplicado; si el servicio corre, revisar el valor efectivo.
- [ ] Confirmar `APP_ENV` efectivo y si el staging es públicamente accesible.
- [ ] Confirmar `LANGSMITH_HIDE_CONTENT` efectivo y el proyecto usado.
- [ ] Probar `GET /livez` sin key → 200.
- [ ] Probar `GET /health`, `/docs`, `/openapi.json`, `POST /chat` sin key → 401 (si el secreto está configurado).
- [ ] Medir latencia de `/chat` con Odoo lento y con NIM lento.
- [ ] Probar `/digitize` con timeout, JSON inválido y JSON válido inesperado.
- [ ] Probar `/digitize_batch` con 20 imágenes y un proxy con timeout de 60 s.
- [ ] Revisar logs de Render en busca de `NIM RAW RESPONSE`, RIF, nombres o montos.
- [ ] Revisar si `thread_id` se comparte entre usuarios en el widget Odoo.
- [ ] Revisar si hay más de una instancia y cómo se comporta el historial.

### E. Cuestiones abiertas

1. ¿El staging de Render tiene `API_SHARED_SECRET` cargado? Es la diferencia entre un hallazgo latente y uno activo.
2. ¿El servicio corre con `MAX_TOOL_CALLS=8`? Si sí, ¿cómo arrancó, dado el `le=3`?
3. ¿El widget Odoo genera `thread_id` por usuario o usa un valor compartido?
4. ¿Se planea escalar a 2+ instancias? Si sí, el `ThreadStore` in-process es un bloqueante.
5. ¿NIM soporta structured output para el modelo configurado? Si no, ME-03 aplica siempre.
6. ¿Los logs de Render se retienen y quién tiene acceso? Necesario para evaluar el impacto real de AL-07.
7. ¿El corpus se reconstruye en cada deploy? Si falta, ¿el servicio debe fallar o degradar?

---

**Fin del informe.**  
Este documento no certifica ausencia de vulnerabilidades: es una revisión estática acotada al commit y alcance indicados. Las correcciones deben validarse con tests y con una prueba controlada en staging antes de pasar a producción.
