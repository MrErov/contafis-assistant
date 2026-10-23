# Plan 001 — Chat Fiscal (MVP)

- **Spec fuente:** `specs/001-chat-fiscal/spec.md` (aprobada, 72 RF / 59 AC / 47 EC).
- **Estado:** listo para implementación por tareas.
- **Objetivo:** implementar el MVP del asistente fiscal read-only sobre Odoo 19 y un corpus normativo local, con contrato Pydantic, orquestación LangGraph, RAG híbrido, trazabilidad y tests sin red ni tokens.
- **Alcance de este documento:** decisiones de arquitectura, módulos, funciones puras, contratos, estrategia de pruebas y orden de implementación. No contiene código de producción.

## 1. Principios de diseño

1. **El ERP manda:** ningún hecho operativo se afirma sin un `ToolResult` válido del turno actual. El historial solo aporta entidad, filtros y rango temporal.
2. **Read-only por construcción:** el cliente JSON/2 y la allowlist bloquean métodos, campos, dominios y argumentos no permitidos antes de cualquier HTTP.
3. **Tipado en todas las fronteras:** Pydantic v2 para request, response, configuración, estado del grafo, argumentos y resultados de tools. Los `dict` crudos no cruzan nodos.
4. **Grafo acotado:** máximo 3 tool calls por turno, independiente del `recursion_limit`; el grafo siempre termina en un `ChatResponse` válido o en un fallo de arranque controlado.
5. **Cero alucinaciones:** la respuesta se redacta solo desde `data`, `sources` y `options`; un validador post-generación bloquea hechos no sustentados y activa el render determinista.
6. **Fallo degradado explícito:** corpus ausente/inválido no impide arrancar; alcance incompleto se reporta como `partial`; alcance inseguro en producción es fail-fast.
7. **Tests sin costo ni red:** dobles de LLM, Odoo, embeddings y reloj; ninguna prueba requiere OpenAI, Odoo, Chroma remoto ni LangSmith.

## 2. Arquitectura lógica

```text
POST /chat
  -> autenticación opcional (X-API-Key)
  -> parseo y validación manual del body
  -> resolución de hilo (ThreadStore)
  -> compuerta de alcance (ScopeService)
  -> TurnService / grafo LangGraph
       -> clasificación Pydantic (LLM fake en tests)
       -> resolución de entidad (Odoo, cuenta presupuesto si consulta)
       -> planificador source-aware (erp | rag)
       -> ejecución de tool acotada
       -> finalización / iteration_limit
       -> reducer de estado
       -> compositor de respuesta + validador de grounding
  -> ChatResponse (Pydantic)
```

- **Una sola instancia de proceso / un solo worker uvicorn.** El `ThreadStore` y el estado de alcance son in-process.
- **I/O secuencial:** todas las llamadas Odoo se ejecutan una tras otra. Nunca `search_read` y `search_count` en paralelo.
- **Dominio puro:** los cálculos de fechas, estados efectivos, dominios, agregaciones, fuzzy, RRF, sanitización y precedencia de estados no hacen I/O.
- **Inyección de dependencias:** reloj, cliente Odoo, retriever, modelo LLM y store se inyectan desde `main.py` y se sustituyen por dobles en tests.
- **Sin estado global mutable** salvo los singletons explícitos de la aplicación (store, scope y corpus), protegidos por lock.

## 3. Estructura de archivos propuesta

```text
src/contafis_assistant/
  __init__.py
  main.py                         # app factory, lifespan, wiring, /chat y /health
  config.py                       # Pydantic Settings, validación de entorno y fail-fast
  api/
    routes.py                     # endpoints y parseo manual del body
    security.py                   # X-API-Key con comparación en tiempo constante
    errors.py                     # traducción de fallos a respuestas HTTP seguras
  domain/
    enums.py                      # Status, Intent, ScopeState, WarningCode, Kind, etc.
    contracts.py                  # ChatRequest/Response, ToolResult, ítems, EntityRef, Source, Warning
    request.py                    # validación pura de message/thread_id
    time.py                       # reloj Caracas, ventanas semiabiertas, formato dd/mm/aaaa
    effective_state.py            # Tabla 9.3-A para obligación/documento/snapshot
    domains.py                    # dominios exactos de obligaciones y documentos
    aggregation.py                # totales, breakdown, compliance_status, truncamiento
    status.py                     # reducer de precedencia y coherencia de estados
    answer.py                     # render determinista, validación de grounding y citas
  memory/
    threads.py                    # ThreadStore con lock, LRU, topes y warnings
  odoo/
    allowlist.py                  # allowlist cerrada y validador puro de RPC
    client.py                     # JSON/2 httpx, headers, timeout, errores y reintentos
    scope.py                      # scope_check, clasificación y TTL
    tools/
      entities.py                 # resolución de compañía/cliente
      companies.py                # agregado y opciones
      clients.py                  # clientes y contadores
      obligations.py              # obligaciones, impuestos y estados efectivos
      documents.py                # documentos y cartelera por pares
      alerts.py                   # alertas, orden y sanitización
  rag/
    manifest.py                   # carga y validación de manifest.json
    tokenizer.py                  # normalización/tokenización compartida
    bm25.py                       # índice en memoria, versión/hash, solo lectura
    fusion.py                     # umbrales dense/BM25 y RRF
    corpus.py                     # estado del corpus, Chroma y disponibilidad
    retriever.py                  # consulta dense + BM25 y construcción de Source
    tool.py                       # tool normativa
    ingest.py                     # ingesta offline testeable
  graph/
    state.py                      # TurnState Pydantic y contadores de presupuesto
    classify.py                   # salida estructurada y normalización de intención/slots
    planner.py                    # selección source-aware y construcción de tool calls
    executor.py                   # ejecución acotada, reintentos y registro de fuentes
    nodes.py                      # nodos del grafo y finalización
    build.py                      # StateGraph, edges condicionales y recursion_limit
  observability/
    logging.py                    # logs estructurados y redacción de PII
    tracing.py                    # LangSmith metadata-only / ocultamiento de contenido
  scripts/
    ingest_corpus.py              # entrada offline de PDFs, Chroma y BM25 (sin runtime)
tests/
  conftest.py                     # fixtures, bloqueo de red, fakes, reloj fijo
  unit/                           # dominio, allowlist, RAG, memoria, planner
  integration/                    # grafo + tools fake + corpus fake
  api/                            # FastAPI, auth, health, errores
  acceptance/                     # trazabilidad AC-01..AC-59
```

La implementación puede fusionar archivos pequeños, pero no debe mezclar I/O con funciones puras ni saltarse las fronteras Pydantic. Los nombres anteriores son la referencia para las tareas.

## 4. Contrato y modelos Pydantic

### 4.1 Frontera HTTP

- `ChatRequest` es permisivo (`extra="allow"`, `strict=False`) y solo en esta frontera. Sus campos son `Any`; la validación real la hace una función pura que devuelve una decisión tipada.
- La validación manual cubre:
  - `message` debe ser `str`, no vacío, no solo espacios y de longitud 1..`MAX_MESSAGE_CHARS`.
  - `thread_id` debe ser `str` de 1..64 caracteres `[A-Za-z0-9_-]`.
  - `thread_id` ausente o inválido genera UUID4; un mensaje inválido produce `invalid_request` sin tools ni LLM.
  - Un body no parseable o no objeto también produce `invalid_request` con HTTP 200 y `thread_id` generado.
- `request_id` siempre es UUID4 y se propaga a logs y LangSmith.
- Los modelos internos usan `extra="forbid"` y no aceptan `dict` sin validar.

### 4.2 Respuesta

- `ChatResponse` incluye exactamente: `request_id`, `thread_id`, `status`, `answer`, `data`, `sources`, `tools_used`, `tool_call_count`, `entities`, `options`, `truncated`, `warnings`.
- `data: list[ToolResult]` es una unión discriminada por `kind`: `obligations`, `clients`, `documents`, `alerts`, `companies`.
- Validaciones cruzadas:
  - `returned == len(items)`.
  - Si `total` es entero, `total >= returned`.
  - Si `total is None`, `truncated=true` y existe `count_unavailable`.
  - `DocumentsResult.pending_count = total - valid`, calculado en Python, nunca pedido a Odoo.
  - `source="odoo"` en todos los `ToolResult`.
  - `options` vacío salvo `needs_clarification`/`not_found`.
  - `not_found` solo es válido con `scope_complete=true`.
- `EntityRef` exige `company_id`, `company_name` y `company_archived` cuando `type="client"`.
- `Warning` exige un código estable de §8.4, mensaje en español, fuente opcional y contexto acotado sin secretos, URLs internas ni PII innecesaria.
- `Source` exige `document_id`, `document`, `page`, `section`, `locator`, `chunk_id`, `score` RRF `>= 0` y `collection`.

### 4.3 Estado del grafo

`TurnState` es un modelo Pydantic y contiene, como mínimo:

- Entrada: `request_id`, `thread_id`, `message`, `history` solo de filtros/entidades/tiempo.
- Decisión: `intent`, `slots`, `entity_mentions`, `out_of_scope_components`.
- Contexto: `scope_snapshot`, `corpus_status`, `thread_warnings`.
- Ejecución: `entities`, `options`, `data`, `sources`, `tools_used`, `tool_call_count`, `sources_used`, `sources_attempted`, `retryable_source`.
- Resultado: `status`, `answer`, `truncated`, `warnings`, `latency_ms`.
- Control: `remaining_budget`, `iteration_limit_reason`, `tool_errors`, `is_final`.

El estado no guarda resultados crudos de Odoo ni textos completos de conversaciones. El historial persistido en `ThreadStore` solo contiene entidad resuelta, filtros normalizados y rango temporal.

## 5. Funciones puras y responsabilidades

| Módulo | Funciones clave | Entrada/salida | Regla principal |
|---|---|---|---|
| `domain/request.py` | validación de body y `thread_id` | valores crudos → decisión tipada | `invalid_request` antes de cualquier tool/LLM |
| `domain/time.py` | `today_caracas`, ventana de día/semana, formato | reloj inyectado → fechas/rangos | `America/Caracas`, intervalos semiabiertos |
| `domain/effective_state.py` | estado efectivo de obligación/documento/snapshot | estado crudo + fecha + `today` → estado + warnings | Tabla 9.3-A |
| `domain/domains.py` | `D_pending`, `D_overdue`, `D_doc_*` | `today` → dominios | dominios exactos, sin `read_group` |
| `domain/aggregation.py` | totales, breakdown, `compliance_status`, truncado | ítems clasificados → contadores | `null_obligation_type` sin doble conteo |
| `domain/status.py` | reducer de precedencia | señales tipadas → `Status` + warnings | `not_found` solo con alcance completo |
| `odoo/allowlist.py` | validación de modelo/método/campos/operadores/args | RPC tipado → aceptado/rechazado | rechazo antes del HTTP |
| `odoo/scope.py` | clasificación de alcance | grupos, cobertura, atestación → `ScopeSnapshot` | no interno nunca habilita tools |
| `odoo/tools/entities.py` | normalización, puntuación fuzzy y decisión | menciones + candidatos → resolución/opciones | precedencia exacta y orden `score desc, id` |
| `odoo/tools/documents.py` | universo de pares y fallback de cartelera | pares + snapshots/vivos → brechas | `total=pares`, `pending_count` en Python |
| `odoo/tools/alerts.py` | orden y sanitización | alertas crudas → alertas limpias | `date desc nulls last, id` |
| `rag/fusion.py` | umbrales y RRF | listas dense/BM25 → `Source[]` | umbral antes de RRF, `k=60`, top 5 |
| `graph/planner.py` | siguiente fuente y presupuesto | intención + fuentes + budget → acción | máx. 3, sin repetir fuente resuelta |
| `domain/answer.py` | render determinista y grounding | estado + data/sources → texto validado | todo hecho debe existir en data/sources/options |

Todas las funciones de esta tabla deben poder probarse sin HTTP, sin LLM y con un `today` o un reloj fijo.

## 6. Odoo JSON/2 read-only

### 6.1 Allowlist

- Se replica exactamente la Tabla 9.3-B. La validación es previa a construir la URL.
- Modelos y métodos permitidos:
  - `res.company`: `search_read`, `read`, `search_count`.
  - `l10n.ve.compliance.client`: `search_read`, `read`, `search_count`.
  - `l10n.ve.obligation`: `search_read`, `read`, `search_count`.
  - `l10n.ve.document`: `search_read`, `read`, `search_count`.
  - `l10n.ve.cartelera.status`: `search_read`, `read`, `search_count`.
  - `l10n.ve.alert`: `search_read`, `read`, `search_count`.
  - `l10n.ve.document.type`: `search_read`, `read`, `search_count`.
  - `l10n.ve.obligation.type`: `search_read`, `read`, `search_count`.
  - `res.users`: `context_get`, `read`, `has_group`.
  - `res.groups`: `read`.
  - `ir.rule`: `search_read` solo diagnóstico.
- Se rechazan `create`, `write`, `unlink`, `action_*`, `read_group`, `fields_get`, `search` sin `read`, XML IDs arbitrarios y cualquier campo/operador fuera de la tabla.
- `res.users.has_group` solo acepta `ids=[uid_actual]` y `group_ext_id` exacto en `base.group_system`, `base.group_erp_manager` o `base.group_user`. No se usa `ir.model.data`.
- Los dominios se validan recursivamente: operadores lógicos y de hoja permitidos, campos permitidos, tipos de valores correctos, listas acotadas y `offset`/`limit` enteros no negativos.
- `context.allowed_company_ids` se inyecta como contexto de la llamada, no como dominio libre.

### 6.2 Cliente JSON/2

- Método único: `POST /json/2/{model}/{method}`.
- Headers: `Authorization: Bearer $ODOO_API_KEY`, `X-Odoo-Database: $ODOO_DB` y `Content-Type: application/json`.
- `ODOO_DB` por defecto `contea`; `ODOO_PROTOCOL` solo admite `json2`.
- El cliente recibe RPC ya validados y devuelve resultados normalizados; no acepta diccionarios arbitrarios de tools.
- Timeout configurable; errores de red/timeout, credenciales y errores JSON se traducen a errores internos tipados, nunca a trazas con secretos.
- Reintento:
  - máximo una vez, solo errores transitorios (timeout/red/5xx) y solo si queda presupuesto;
  - nunca reintenta 401/403 ni errores de validación;
  - un reintento consume presupuesto y se refleja en `tool_call_count`;
  - un truncamiento no se reintenta con los mismos argumentos: exige cambiar `offset`/`limit`.
- Las secuencias de RPC dentro de una tool son secuenciales y acotadas; no se usa concurrencia.
- Anomalía documentada (`unhashable type: 'dict'`, `Invalid field 'Count'`): un reintento si queda presupuesto; si no, `upstream_error` o `count_unavailable` según haya detalle.

### 6.3 Filtros de compañía y alcance

- Cada tool filtra explícitamente:
  - clientes: `company_id`;
  - obligaciones/documentos/alertas: `client_id.company_id`;
  - cartelera: `client_id.company_id`, porque no tiene record rule de compañía.
- Todas las llamadas envían `context.allowed_company_ids` con las compañías activas permitidas.
- Compañías archivadas se excluyen de agregados y fuzzy. Una compañía exacta archivada se devuelve como `ok` + `company_archived`, sin datos.
- Un cliente de compañía archivada no es consultable y sigue la misma regla.
- Registros con `client_id=False` quedan fuera del MVP; no se crea una cubeta "sin cliente".

### 6.4 Scope check

- Se ejecuta al arrancar y se refresca cada `SCOPE_CHECK_TTL_SECONDS`, con lock y reloj inyectable.
- Consulta compañías activas, `allowed_company_ids`/compañías del usuario y los tres grupos permitidos.
- Clasificación:
  - `scope_ok`: interno no admin, atestación verdadera y cobertura completa.
  - `scope_partial`: interno no admin, atestación verdadera y cobertura incompleta.
  - `scope_unsafe`: admin, atestación falsa/revocada o `base.group_user=false`.
- Consecuencias:
  - producción + `scope_unsafe` al arrancar → fallo de arranque;
  - producción + `scope_partial` → arranca y responde `partial` con `scope_partial`;
  - refresh posterior a `scope_unsafe` en producción → health no saludable y turnos 503;
  - dev/test + unsafe por admin/atestación → degradado, `scope_complete=false`, tools Odoo permitidas y warning `scope_admin_key`;
  - dev/test + unsafe por no interno → degradado, `scope_complete=false`, sin tools; **no** se emite `scope_admin_key`.
- `scope_partial` nunca se presenta como completo; agregados y no-match responden `partial`, y nunca se emite un total 0 engañoso.

## 7. Tools ERP y cálculos

### 7.1 Catálogo de tools

| Tool | Fuente | Uso | Presupuesto |
|---|---|---|---|
| `resolve_entity` | Odoo | resolver compañía/cliente mencionado | cuenta si consulta Odoo; no cuenta si sale del hilo |
| `query_obligations` | Odoo | vencimientos, impuestos y estados efectivos | 1 |
| `query_clients` | Odoo | cartera, RIF, compliance y contadores | 1 |
| `query_documents` | Odoo | brechas documentales y cartelera | 1 |
| `query_alerts` | Odoo | alertas urgentes/hoy | 1 |
| `query_companies` | Odoo | agregado por compañía | 1 |
| `search_normativa` | RAG | fragmentos normativos | 1 |

- Cada tool recibe argumentos tipados y construidos por código; el LLM no pasa dominios, campos ni XML IDs.
- Una tool puede ejecutar varias RPC secuenciales para contar/clasificar; eso no consume presupuesto adicional.
- Un reintento o una repetición de tool consume presupuesto.
- `tools_used` guarda nombres únicos; `tool_call_count` cuenta intentos reales, incluidos reintentos.

### 7.2 Obligaciones

- Por defecto se usan estados efectivos `pending` y `overdue`; `paid`/`cancelled` solo si se piden.
- Los dominios son los exactos de la spec y se construyen con `today_caracas`.
- El total activo es la suma de los dominios disjuntos; nunca se infiere del detalle.
- Filtro por impuesto: `obligation_type_id.tax_type`. Las obligaciones sin tipo se excluyen del total filtrado y se reportan en cubeta separada; en agregados sin impuesto se incluyen y se evita doble conteo.
- `due_date=False`: se incluye, conserva estado y emite `no_due_date`.
- Orden `due_date, id`; el detalle se limita a `DETAIL_LIMIT`.
- El breakdown y los contadores de clientes usan una lectura mínima en lote de `id`, `client_id`, `company_id`, `state`, `due_date`, agrupada en Python. Sin `read_group` y sin N+1.

### 7.3 Clientes y compañías

- Se devuelven RIF opcional, compañía, `company_archived`, `compliance_status`, contadores y warnings.
- `compliance_status` se calcula en Python con estados efectivos: `vencido` > `pendiente` > `al_dia`.
- `obligations_total` = pendientes + vencidas efectivas del conjunto filtrado; `obligations_overdue` = vencidas; `alerts_pending` = alertas `pending`.
- El agregado consulta todas las compañías activas y devuelve `entities` y breakdown; si no hay activas, `ok` + `no_active_companies`.
- Los contadores se obtienen en una lectura en lote, no por cliente.

### 7.4 Cartelera y documentos

- Universo: producto cartesiano de clientes en alcance y tipos `required_for='company'`.
- Por cada par `(client_id, document_type_id)`:
  1. snapshot del mes actual;
  2. si falta, documento vivo más reciente por `expiry_date desc, id desc`;
  3. si no hay documento y la consulta fue exitosa, `missing`;
  4. si los vivos fallan, snapshot anterior de mayor `(year, month)`;
  5. si no hay anterior, `partial`/`count_unavailable`, nunca 0.
- Si el snapshot actual no aporta la fila de un par, se usan vivos y se emite `cartelera_fallback_live_documents`.
- Si el snapshot actual existe pero no cubre todos los tipos, se completan los faltantes con vivos y se emite `cartelera_snapshot_incomplete`.
- Si se usa el snapshot anterior, se emite `cartelera_fallback_previous_snapshot`.
- `total` es el número de pares; `pending_count = total - valid`, calculado en Python.
- `DocumentGapItem` expone `source` (`current_snapshot`, `live_document`, `previous_snapshot`) y `as_of`.
- Un par sin fila se materializa con `id=0`, `document_id=None`, `state="missing"` y la fuente/corte correspondiente.
- El filtro de mes usa `str(month)`.
- Los estados efectivos y los dominios documentales salen de la Tabla 9.3-A; `rejected` y `missing` prevalecen; `state=False` es `pending`.

### 7.5 Alertas

- Vigentes: `pending` y `sent`; `done` solo si se pide.
- "Hoy" se evalúa en Caracas con intervalo semiabierto; si no hay alertas hoy, se muestran las últimas 5 con `no_alerts_today`.
- Orden determinista `date desc nulls last, id`, resuelto en Python si Odoo no lo garantiza.
- Se sanea `message`: se eliminan montos, símbolos de moneda, emails, links `wa.me`/`api.whatsapp.com`, `tel:`/`mailto:` y teléfonos alternativos.
- Se devuelven campos estructurados y `message_clean` solo si queda texto útil.

### 7.6 Resolución de entidades

- Normalización de acentos, mayúsculas y espacios.
- Precedencia: compañía exacta > cliente exacto > fuzzy.
- Compañía exacta usa `active_test=false`; las archivadas se informan y no se consultan.
- Fuzzy solo candidatos activos, RapidFuzz `token_set_ratio`, `FUZZY_MIN_SCORE=85`, orden `score desc, id`.
- Más de un candidato sobre el umbral, empate, más de 5 coincidencias o colisión de ID compañía/cliente → `needs_clarification` con hasta 5 `EntityRef`.
- No existe y alcance completo → `not_found` con hasta 5 sugerencias; alcance incompleto → `partial` con `scope_partial`.
- Si no se menciona entidad → agregado de compañías activas.
- La entidad resuelta se guarda en el hilo como filtro, no como hecho operativo.

## 8. RAG híbrido

### 8.1 Ingesta offline

- `scripts/ingest_corpus.py` verifica manifiesto y hashes, extrae texto con `pypdf`, fragmenta, genera embeddings configurados e indexa en Chroma; además construye BM25 y escribe versión/hash.
- La ingesta no corre en runtime ni reindexa al arrancar.
- El manifiesto incluye por documento: `id`, título, versión, archivo, SHA-256, páginas, fecha de publicación, idioma y origen; a nivel de índice: `bm25_index_version` y `bm25_index_sha256`.
- Solo documentos del manifiesto pueden indexarse o citarse.
- La ruta del manifiesto se deriva de la configuración de corpus/rutas de test (`data/manifest.json` por defecto, junto al índice BM25), sin añadir un parámetro público nuevo.

### 8.2 Runtime

- `CorpusManager` valida manifiesto, colección Chroma e índice BM25 al arrancar.
- Estado `corpus_status`: `ok`, `missing`, `empty` o `mismatch`; se reporta en health con versión.
- Un corpus inválido no es fail-fast. El servicio arranca degradado.
- Chroma y BM25 se abren en solo lectura; el runtime no reindexa ni modifica el índice.
- El tokenizador es idéntico entre indexación y consulta: normaliza mayúsculas, acentos y espacios, preserva acrónimos fiscales y aplica solo el mapa de alias curado.
- Alias permitidos: ISLR ↔ Impuesto sobre la Renta; IVA ↔ Impuesto al Valor Agregado; IGTF ↔ Impuesto a las Grandes Transacciones Financieras; RIF ↔ Registro de Información Fiscal.

### 8.3 Retrieval

- Dense top 10 y BM25 top 10.
- La colección Chroma se declara/valida en espacio cosine; la distancia devuelta se convierte a similitud antes de comparar con `DENSE_MIN_SCORE`.
- Umbral de señal **antes** de RRF: dense cosine `>= DENSE_MIN_SCORE` o BM25 `>= BM25_MIN_SCORE`.
- Si ningún candidato supera la señal, no se fusiona; se emite `retrieval_low_score`.
- RRF `k=60`, top 5 final; `Source.score` es el score RRF, sin umbral adicional.
- `Source` se completa con documento, página 1-based o `None`, sección, locator, chunk estable y colección.
- Si faltan metadatos de página: `page=None`, `section`/`locator` y `page_metadata_missing`.
- RAG-only sin corpus → `upstream_error` + `corpus_unavailable` (y `manifest_mismatch` si aplica).
- RAG-only con corpus pero sin señal → `insufficient_context` + `retrieval_low_score`.
- Mixta con ERP disponible y RAG bajo → `partial` conservando ERP.
- Mixta con RAG caído → `partial` + `source_unavailable`; si el corpus no coincide, se añade `manifest_mismatch`. RAG no se repite.

## 9. Memoria e hilos

- `ThreadStore` en memoria, protegido por lock, con `MEMORY_MAX_THREADS=500` y `MEMORY_MAX_TURNS=20`.
- LRU de hilos; al expulsar se registra el ID en un set acotado. El siguiente uso emite `thread_evicted` y crea un hilo nuevo.
- Historial limitado a 20 turnos; al recortar se emite `thread_history_truncated`.
- Resolución:
  - sin `thread_id` → UUID4, sin warning;
  - existente → reanudar;
  - expulsado → nuevo hilo + `thread_evicted`;
  - UUID4 desconocido → nuevo hilo + `thread_context_unavailable`;
  - ID no UUID4 desconocido → nuevo hilo sin warning.
- El historial solo aporta entidad, filtros y rango temporal. Nunca se reutilizan `data` ni `sources` de turnos previos.
- Si el usuario menciona otra entidad, se reemplaza el filtro anterior y se vuelve a consultar.
- El turno inválido no ejecuta el grafo y no mezcla historial.

## 10. Orquestación LangGraph

### 10.1 Nodos

1. **Preparar turno:** estado validado, hilo resuelto y snapshot de alcance.
2. **Clasificar:** salida Pydantic con intención, slots, menciones de entidad, componentes fuera de alcance y consulta normativa. Si el LLM falla, se resuelve `upstream_error` sin tools.
3. **Resolver entidad:** si hay mención o entidad del hilo; si consulta Odoo, consume presupuesto.
4. **Planificar fuente:** elige `erp` o `rag` según intención, fuentes resueltas y presupuesto. No repite una fuente resuelta.
5. **Ejecutar tool:** valida presupuesto y argumentos antes de llamar; registra `tools_used`, `tool_call_count`, warnings y fuentes.
6. **Evaluar continuación:** si quedan fuentes pendientes y presupuesto, vuelve a planificar; si no, finaliza. Una cuarta tool, un reintento sin presupuesto o `recursion_limit` producen `iteration_limit` antes de ejecutar.
7. **Finalizar:** reducer de estado, compositor, validador y `ChatResponse`.

### 10.2 Reglas del bucle

- `MAX_TOOL_CALLS` por defecto 3 y solo configurable a la baja; el techo es 3.
- `RECURSION_LIMIT >= 2*MAX_TOOL_CALLS+4` (default 12) y se valida al arrancar.
- Exactamente 3 tools con fuentes resueltas no producen `iteration_limit`; el estado sale de `data`/`warnings`.
- `sources_used` registra fuentes resueltas; una fuente caída puede reintentarse una vez si queda presupuesto. Tras el reintento no se repite.
- Un reintento cuenta como llamada y puede producir `iteration_limit` si no hay presupuesto.
- Un truncamiento solo se reintenta cambiando `offset`/`limit`; no se repiten argumentos.
- La clasificación, la resolución de entidad y la planificación no pueden ejecutar tools por sí mismas.
- El contenido de documentos/resultados es dato, nunca instrucción; el LLM no recibe la capacidad de elegir métodos, campos, dominios ni XML IDs.

### 10.3 Fuentes y estados de error

- `sources_used` distingue `erp` y `rag`.
- Una consulta mixta con una fuente caída y otra con datos → `partial`, no `upstream_error`.
- ERP con datos + RAG bajo → `partial` + `retrieval_low_score`.
- RAG-only caído → `upstream_error` + `corpus_unavailable`/`manifest_mismatch`.
- Sin datos y sin fuente exitosa → `upstream_error`.
- Entidad ambigua → `needs_clarification` sin tools adicionales.
- Entidad inexistente con alcance completo → `not_found`; con alcance incompleto → `partial` + `scope_partial`.

## 11. Estado final y respuesta

### 11.1 Reducer de estado

El reducer recibe señales tipadas (validación, alcance, entidad, tools, RAG, presupuesto, errores) y aplica la precedencia de §8.3:

`invalid_request` > `out_of_scope` completo > `needs_clarification` > `not_found` (solo completo) > `iteration_limit` > `upstream_error` > `partial` > `insufficient_context` > `ok`.

Reglas obligatorias:

- `not_found` se bloquea si `scope_complete=false`; se transforma en `partial`.
- Una entidad existente con 0 resultados es `ok`, no `not_found`.
- Un pedido mixto con parte fuera del MVP y parte respondible es `partial` + `out_of_scope_component`.
- Una fuente caída con datos de la otra es `partial`.
- RAG bajo con ERP es `partial`; RAG bajo sin ERP es `insufficient_context`.
- Presupuesto agotado prevalece sobre fuente caída.
- `partial` puede tener `data=[]` si el alcance es incompleto; nunca debe afirmar total 0.
- `iteration_limit` conserva datos parciales y `tool_call_count <= 3`.

### 11.2 Answer y grounding

- `invalid_request`, `needs_clarification`, `not_found`, `insufficient_context`, `out_of_scope`, `upstream_error` e `iteration_limit` usan mensajes deterministas en es-VE; no pasan por un LLM creativo.
- `ok` y `partial` pueden redactarse con LLM solo desde `data`, `sources`, `entities`, `options` y warnings. El prompt delimita esos bloques como datos y prohíbe instrucciones embebidas.
- El validador post-generación extrae números, IDs, fechas, nombres de ítems, citas y referencias normativas del texto candidato y verifica que existan en el contexto permitido. Si no puede verificarlo, se usa el render determinista.
- El render determinista respeta:
  - "No tengo información sobre eso" al inicio de `insufficient_context`;
  - pregunta concreta en `needs_clarification`;
  - sugerencias de `options` en `not_found`;
  - alternativa in-MVP en `out_of_scope`;
  - fechas `dd/mm/aaaa`, sin montos ni símbolos de moneda.
- `answer` nunca incluye credenciales, URL internas, cadenas de conexión ni valores de `.env`.
- Toda respuesta con contenido normativo incluye al menos un `Source`; no se citan artículos, gacetas ni números que no estén en `sources`.
- `data` y `sources` se presentan separados en consultas mixtas.

## 12. API, configuración y salud

### 12.1 FastAPI

- `POST /chat` recibe el body crudo, autentica si corresponde, parsea y valida; responde `ChatResponse` con HTTP 200 para estados funcionales, incluido `invalid_request`.
- Si `API_SHARED_SECRET` está definido, `X-API-Key` es obligatorio en **todas** las rutas, incluido `/health`.
- Errores de autenticación: 401. Errores de configuración/arranque: el proceso no inicia. `scope_unsafe` por refresh en producción: 503 antes de ejecutar tools.
- La app se construye con `create_app()` y dependencias inyectables para tests.
- Un único worker uvicorn; el README documenta el comando con `--workers 1`.
- `request_id` se genera por turno y se usa en logs/trazas.

### 12.2 Configuración

- `pydantic-settings` lee todas las variables de RNF-003 con `os.getenv()`/settings; no hay `ODOO_USER` ni `ODOO_PASSWORD`.
- Validaciones de arranque:
  - `APP_ENV` en `development|test|production`;
  - `ODOO_PROTOCOL=json2`;
  - `MAX_TOOL_CALLS` en 1..3;
  - `RECURSION_LIMIT >= 2*MAX_TOOL_CALLS+4`;
  - valores requeridos presentes y numéricos válidos;
  - host remoto sin `API_SHARED_SECRET` → fallo;
  - `API_SHARED_SECRET` se compara en tiempo constante;
  - en producción, `scope_unsafe` inicial → fallo.
- Un manifiesto/BM25 inválido no es fail-fast: arranca con `corpus_status` degradado.
- `scope_partial` no es fail-fast.
- Secretos nunca se serializan en logs, health ni respuestas.

### 12.3 Health

- `GET /health` reporta, sin secretos:
  - `status` global;
  - `scope_state`, `scope_complete`;
  - `corpus_status`, `corpus_version`;
  - disponibilidad de Chroma/BM25;
  - conectividad Odoo;
  - versión de la app.
- Devuelve 200 para `ok` o degradado operable; 503 cuando el refresh de alcance en producción es unsafe o el servicio no puede atender turnos.
- El health no ejecuta tools de negocio ni devuelve IDs de compañías, RIF, nombres ni resultados.

## 13. Observabilidad y seguridad

- LangSmith habilitado por defecto en desarrollo y deshabilitado en tests/CI.
- Cada turno genera un run con `request_id`, tools, estado y latencia. El contenido de mensajes, resultados crudos, RIF, nombres, emails y teléfonos queda oculto o redactado.
- Logs estructurados con `request_id`, estado, tool names, conteos y latencia; nunca mensaje completo, resultados crudos ni PII.
- Redactor común para logs, warnings y trazas.
- El prompt de clasificación, entidad, RAG y respuesta trata entradas como datos y no permite que contenido de documentos cambie la política, la allowlist o el catálogo de tools.
- La API key de Odoo es dedicada y read-only; se declara con `ODOO_READONLY_ATTESTED`. No se reutiliza la key del MCP.
- `.env` no se versiona; `.env.example` solo contiene placeholders.
- Todas las consultas son de solo lectura; un pedido de escritura/monto/otra jurisdicción se responde fuera de alcance con alternativa in-MVP.

## 14. Estrategia de tests con pytest

### 14.1 Reglas generales

- `pytest` corre sin red, sin OpenAI, sin Odoo y sin Chroma real.
- Fixture autouse que bloquea conexiones de red y falla si un test intenta salir.
- `FakeListChatModel`/fake estructurado para clasificación y respuesta; salidas Pydantic controladas.
- `httpx.MockTransport` para Odoo; doble de embeddings y de colección Chroma; BM25 en archivo temporal.
- Reloj fijo inyectado para Caracas; `TZ=UTC` en CI para detectar dependencias accidentales del host.
- `TestClient`/ASGI transport para la API.
- Cada test de aceptación referencia su AC en el nombre o en un marcador de trazabilidad.

### 14.2 Capas de prueba

| Capa | Qué prueba | Ejemplos |
|---|---|---|
| Unidad de dominio | tablas, dominios, estados, fechas, fuzzy, RRF, sanitización, reducer | sin I/O |
| Contrato | validadores Pydantic, unión discriminada, campos obligatorios, warnings | `ChatResponse`, `ToolResult`, `Source` |
| Cliente Odoo | allowlist antes del HTTP, headers, secuencia, retry, errores, scope | `MockTransport` |
| RAG | manifiesto/hash, tokenizador, umbrales, RRF, Source, corpus degradado | dobles de Chroma/BM25 |
| Grafo | intención, presupuesto, fuentes, reintentos, recursion, hilos, estados | tools/LLM fake |
| API | body inválido, auth, 503, health, thread_id, HTTP 200 | ASGI |
| Seguridad | prompt injection, redacción, logs, no secretos | espías |
| Aceptación | AC-01..AC-59 | matriz parametrizada + integración |

### 14.3 Casos transversales obligatorios

- 23:30 Caracas del último día vs 00:30 del día 1.
- `state=overdue` con fecha futura y `state=pending` con fecha pasada.
- `state=False`, `expiry_date=False`, `due_date=False`, `date=False`.
- `rejected`/`missing` en cartelera y `pending_count`.
- Snapshot incompleto, vivos caídos, snapshot anterior.
- 60 obligaciones con `DETAIL_LIMIT=50`.
- Empate fuzzy, más de 5 coincidencias, colisión de ID y compañía archivada.
- Cuarta tool, reintento sin presupuesto, exactamente 3 tools y `recursion_limit`.
- ERP caído + RAG disponible, RAG caído + ERP disponible, ambos caídos, RAG bajo + ERP.
- `scope_ok`, `scope_partial`, admin degradado y no interno sin tools.
- Manifiesto/BM25 con hash/versión incorrectos.
- Hilos simultáneos, UUID4 perdido, LRU, ID no UUID4.
- `answer` con total/ID/fecha/cita inventados → fallback determinista.
- Documento con instrucciones maliciosas → se trata como dato.
- Logs/traza sin PII ni secretos.

### 14.4 Set dorado manual

Además de CI, se documenta un set de 10 preguntas para ejecutar manualmente contra `contea` y el corpus real. No forma parte de la suite automática porque requiere servicios reales; su resultado se registra en el cierre de aceptación.

## 15. Orden de implementación y dependencias

| Tarea | Depende de | Entrega principal |
|---|---|---|
| T01 | — | Base, configuración, contratos, tiempo, estados efectivos y reducer |
| T02 | T01 | Odoo JSON/2, allowlist y scope_check |
| T03 | T01, T02 | Entidades, compañías, clientes y obligaciones |
| T04 | T01, T02, T03 | Cartelera, documentos y alertas |
| T05 | T01 | Manifiesto, ingesta, BM25 y retrieval híbrido |
| T06 | T01, T03, T05 | Memoria, clasificación, planner y grafo acotado |
| T07 | T04, T05, T06 | Respuesta grounded, reducer integrado y matriz de fallos |
| T08 | T02, T05, T07 | FastAPI, auth, lifespan, health y fail-fast/degradado |
| T09 | T08 | Observabilidad, redacción y hardening anti-injection |
| T10 | T01–T09 | Cierre AC, set dorado, dependencias, README y DoD |

**Justificación del orden:** primero se estabilizan contratos y cálculos puros; después se incorpora Odoo y alcance; luego las consultas de negocio; después RAG; al final se conecta el grafo, la API y la observabilidad. Ninguna tarea depende de la UI ni de servicios reales.

## 16. Trazabilidad de aceptación por tarea

| Tarea | AC cubiertos |
|---|---|
| T01 | AC-19, AC-20, AC-25, AC-44, AC-54 (parte de contrato y tiempo) |
| T02 | AC-12, AC-21, AC-37, AC-52, AC-55, AC-56, AC-58, AC-59 |
| T03 | AC-01, AC-02, AC-03, AC-04 (resolución), AC-22, AC-26, AC-29, AC-30, AC-31, AC-32 (obligaciones), AC-49, AC-50, AC-54 |
| T04 | AC-05, AC-06, AC-23, AC-24, AC-27, AC-28, AC-32 (documentos/alertas), AC-42, AC-45, AC-47 |
| T05 | AC-07, AC-08, AC-33, AC-34, AC-35, AC-36, AC-41, AC-53 |
| T06 | AC-09, AC-11, AC-13, AC-15, AC-17, AC-18, AC-40, AC-48, AC-51, AC-57 |
| T07 | AC-04 (estado final), AC-10, AC-14, AC-23 (estado), AC-30 (estado), AC-33 (estado), AC-43, AC-46, AC-55 (estado), AC-57 (estado) |
| T08 | AC-17, AC-18, AC-19, AC-20, AC-21, AC-38, AC-40, AC-44, AC-52, AC-55 |
| T09 | AC-16, AC-39 |
| T10 | AC-01..AC-59 (auditoría final y cierre) |

La matriz se complementa con tests unitarios por RF. T10 falla si algún AC no tiene al menos una prueba que lo nombre o lo referencie.

## 17. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Respuesta del LLM inventa un total/cita | Validador conservador + render determinista; tests adversariales |
| Presupuesto de 3 tools se confunde con el `recursion_limit` | Contador propio en `TurnState`; validación de arranque y tests de 4.ª tool |
| `scope_admin_key` se emite para un no interno | Razón de degradación tipada (`admin_attestation` vs `not_internal`) y tests AC-52/59 |
| Cartelera explota en pares o N+1 | Lecturas mínimas en lote, pares calculados en Python y pruebas de gran volumen |
| Fechas UTC adelantan el mes | Reloj inyectable, `zoneinfo` y pruebas 23:30/00:30 |
| Corpus inválido tumba el servicio | `CorpusManager` degradado; solo config/scope inseguro son fail-fast |
| PII en LangSmith/logs | Ocultamiento de inputs/outputs y redactor probado |
| Allowlist incompleta permite escritura | Validación previa a URL + tests con `MockTransport` que verifican cero requests |

## 18. Definición de terminado del plan

1. `plan.md` y `tasks.md` existen y no se modificó `spec.md` durante la planificación.
2. Las 10 tareas respetan el máximo y cada una declara “Hecho cuando:”.
3. Cada tarea empieza por tests que fallan (TDD), sin red ni tokens.
4. La matriz AC-01..AC-59 queda cubierta por tests y la suite completa pasa en CI.
5. `requirements.txt`, `.env.example`, README y documentación operativa quedan completos según RNF-014 y el DoD de la spec.
6. El set dorado manual de 10 preguntas se ejecuta contra servicios reales antes del cierre.
