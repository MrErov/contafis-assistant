# Tareas 001 — Chat Fiscal (MVP)

- **Spec:** `specs/001-chat-fiscal/spec.md`
- **Plan:** `specs/001-chat-fiscal/plan.md`
- **Máximo:** 10 tareas.
- **Regla:** cada tarea empieza por tests que fallan (TDD). No se avanza a la siguiente con tests rojos.
- **Prohibido en toda la implementación:** red real, tokens reales, Odoo real, Chroma remoto, archivos temporales fuera de `tmp_path`, escrituras en Odoo y secretos hardcodeados.

## Resumen y orden

| Orden | Tarea | Depende de |
|---|---|---|
| 1 | T01 — Base, configuración, contratos y dominio puro | — |
| 2 | T02 — Odoo JSON/2, allowlist y scope_check | T01 |
| 3 | T03 — Entidades, compañías, clientes y obligaciones | T01, T02 |
| 4 | T04 — Cartelera, documentos y alertas | T01, T02, T03 |
| 5 | T05 — Manifiesto, ingesta y RAG híbrido | T01 |
| 6 | T06 — Memoria, clasificación y grafo acotado | T01, T03, T05 |
| 7 | T07 — Respuesta grounded y matriz de estados | T04, T05, T06 |
| 8 | T08 — FastAPI, auth, lifespan y health | T02, T05, T07 |
| 9 | T09 — Observabilidad, PII y hardening | T08 |
| 10 | T10 — Cierre de aceptación y operación | T01–T09 |

---

## T01 — Base, configuración, contratos y dominio puro

**Objetivo:** dejar el paquete instalable, la configuración validada y el núcleo puro que usan todas las tareas posteriores.

**Tests primero (rojo):**

- `tests/unit/test_request_contract.py`
  - `message` no textual, vacío, solo espacios, demasiado largo → `invalid_request`.
  - `thread_id` ausente, inválido o válido; UUID4 generado y devuelto.
  - `ChatResponse` y `ToolResult` validan campos, discriminante, `returned`, `total`, `truncated` y `pending_count`.
  - `not_found` con `scope_complete=false` es inválido.
- `tests/unit/test_config.py`
  - defaults de RNF-003;
  - `ODOO_PROTOCOL` distinto de `json2` falla;
  - `MAX_TOOL_CALLS > 3` falla;
  - `RECURSION_LIMIT < 2*MAX_TOOL_CALLS+4` falla;
  - host remoto sin `API_SHARED_SECRET` falla;
  - no existen campos `ODOO_USER`/`ODOO_PASSWORD`.
- `tests/unit/test_time_effective_state.py`
  - 23:30 Caracas del último día y 00:30 del día 1;
  - ventanas semiabiertas de día/semana;
  - Tabla 9.3-A completa para obligación, documento y snapshot;
  - `state=False`, `expiry_date=False`, `due_date=False`, `rejected`, `missing`.
- `tests/unit/test_domains_aggregation.py`
  - `D_pending` y `D_overdue` disjuntos;
  - dominios documentales exactos;
  - `null_obligation_type` sin doble conteo;
  - `compliance_status`;
  - orden determinista y truncado.
- `tests/unit/test_status_reducer.py`
  - precedencia completa de §8.3;
  - `not_found` solo con alcance completo;
  - 0 resultados de entidad existente → `ok`;
  - `partial` con alcance incompleto;
  - RAG bajo + ERP → `partial`;
  - presupuesto agotado prevalece.

**Implementación:**

- Paquete `src/contafis_assistant/`, `config.py` y modelos/enums de `domain/`.
- `ChatRequest` permisivo solo en la frontera; validación manual pura.
- `WarningCode` centralizado con los códigos estables de §8.4.
- Reloj inyectable, `zoneinfo`, formatos y dominios exactos.
- Reducer de estado, agregación y truncado sin I/O.
- `requirements.txt` con las dependencias del stack y `.env.example` con placeholders.
- Fixtures base: entorno de test, reloj fijo y bloqueo de red.

**Hecho cuando:**

- [x] `pytest tests/unit/test_request_contract.py tests/unit/test_config.py tests/unit/test_time_effective_state.py tests/unit/test_domains_aggregation.py tests/unit/test_status_reducer.py` pasa sin red ni tokens.
- [x] Todos los tests usan `tmp_path`/dobles y ningún módulo de `domain/` importa `httpx`, LangChain u Odoo.
- [x] `requirements.txt` y `.env.example` contienen todas las claves de RNF-003, sin secretos.
- [x] La matriz de precedencia está cubierta por una prueba por transición relevante.

**Trazabilidad:** RF-002, RF-003, RF-007, RF-008, RF-025, RF-026, RF-027, RF-031, RF-034, RF-063, RF-064, RF-065, RF-066, RF-080; RNF-001, RNF-003, RNF-009, RNF-011, RNF-016; AC-19, AC-20, AC-25, AC-44, AC-54.

---

## T02 — Odoo JSON/2, allowlist y scope_check

**Objetivo:** implementar el acceso read-only a Odoo y la compuerta de alcance, sin permitir que una tool construya un RPC libre.

**Tests primero (rojo):**

- `tests/unit/test_odoo_allowlist.py`
  - métodos, campos, operadores y modelos de la Tabla 9.3-B;
  - rechazo de `write`, `unlink`, `create`, `action_*`, `read_group`, `fields_get`, `search` sin `read`, `like`, `not ilike`, `child_of`;
  - rechazo de `active` en `document.type`/`obligation.type`;
  - `has_group` solo con `ids=[uid]` y los tres XML IDs permitidos;
  - XML ID arbitrario → `invalid_tool_args` sin HTTP.
- `tests/unit/test_odoo_client.py`
  - URL, headers `Authorization`/`X-Odoo-Database`/`Content-Type`;
  - `context.allowed_company_ids` en cada llamada;
  - llamadas secuenciales, no paralelas;
  - timeout/5xx reintenta una vez solo con presupuesto;
  - 401/403 no reintenta;
  - anomalía de concurrencia: un reintento y luego `upstream_error`/`count_unavailable`;
  - truncamiento exige cambiar `offset`/`limit`;
  - ningún secreto en logs ni excepciones.
- `tests/unit/test_scope.py`
  - `scope_ok`, `scope_partial`, `scope_unsafe`;
  - admin, atestación falsa/revocada, no interno;
  - no interno no habilita tools y no recibe `scope_admin_key`;
  - producción unsafe inicial falla; `scope_partial` arranca;
  - refresh unsafe queda no saludable;
  - cobertura de compañías activas.

**Implementación:**

- `odoo/allowlist.py` con la tabla cerrada y validador recursivo.
- `odoo/client.py` JSON/2 con `httpx`, timeout, errores tipados y retry acotado.
- `odoo/scope.py` con `ScopeSnapshot`, razones de degradación, TTL y lock.
- Inyección de `allowed_company_ids` y filtros explícitos de compañía.
- No usar `ir.model.data`; no usar XML-RPC; no usar `read_group`.

**Hecho cuando:**

- [ ] `pytest tests/unit/test_odoo_allowlist.py tests/unit/test_odoo_client.py tests/unit/test_scope.py` pasa.
- [ ] Todo intento inválido se rechaza antes de construir/ejecutar el request HTTP, comprobado con `MockTransport`.
- [ ] Los tests demuestran que no hay dos RPC en paralelo y que un reintento consume presupuesto.
- [ ] `scope_unsafe` por admin/atestación y por no interno producen decisiones distintas, incluido `scope_admin_key` solo para el primer caso.

**Trazabilidad:** RF-032, RF-033, RF-061, RF-062, RF-068, RF-079; RNF-012, RNF-013, RNF-016; AC-12, AC-21, AC-37, AC-52, AC-55, AC-56, AC-58, AC-59.

---

## T03 — Entidades, compañías, clientes y obligaciones

**Objetivo:** resolver entidades y entregar las tools ERP principales con totales, breakdown, estados efectivos y contadores sin N+1.

**Tests primero (rojo):**

- `tests/unit/test_entities.py`
  - normalización de acentos/mayúsculas/espacios;
  - precedencia compañía exacta > cliente exacto > fuzzy;
  - `FUZZY_MIN_SCORE=85`, orden `score desc, id`;
  - empate, >5 coincidencias y colisión de ID → `needs_clarification`;
  - compañía archivada exacta → `ok` + `company_archived`;
  - cliente de compañía archivada;
  - inexistente con alcance completo/incompleto;
  - `needs_clarification` con `options` solo de tipo `EntityRef` y sin datos/citas; aclaración no entitativa con `options=[]`.
- `tests/unit/test_obligations.py`
  - dominios y estados efectivos;
  - default `pending`/`overdue`, exclusión de `paid`/`cancelled`;
  - filtro por `obligation_type_id.tax_type`;
  - cubeta `null_obligation_type` sin doble conteo;
  - `due_date=False`;
  - total/breakdown/orden/truncado con 60 registros y `DETAIL_LIMIT=50`.
- `tests/unit/test_clients_companies.py`
  - contadores `obligations_total`, `obligations_overdue`, `alerts_pending`;
  - `compliance_status` calculado en Python;
  - agregado de todas las compañías activas;
  - `no_active_companies`;
  - lectura en lote, sin N+1;
  - no filtrar por campos computados.
- `tests/integration/test_erp_tools.py`
  - tools conectadas a un gateway fake, con contrato `ToolResult` válido.

**Implementación:**

- Resolución de entidad y `EntityRef`, `options` y reglas de `not_found`/`partial`.
- `query_companies`, `query_clients`, `query_obligations`.
- Agregación en Python desde lectura mínima; sin `read_group`.
- `ToolResult` tipado y salida de warnings por ítem/tool.
- Filtros de compañía y `allowed_company_ids` en cada consulta.

**Hecho cuando:**

- [ ] `pytest tests/unit/test_entities.py tests/unit/test_obligations.py tests/unit/test_clients_companies.py tests/integration/test_erp_tools.py` pasa.
- [ ] Un test con 60 obligaciones devuelve `total=60`, `returned=50`, `truncated=true`.
- [ ] Un spy demuestra una sola lectura en lote para contadores de clientes, sin consultas por cliente.
- [ ] Todas las respuestas de tools validan `returned == len(items)` y `total >= returned`.
- [ ] No se consulta ni se filtra por `compliance_status`, `cartelera_pending_count` o `compliance_score`.

**Trazabilidad:** RF-004, RF-020, RF-021, RF-022, RF-023, RF-024, RF-025, RF-028, RF-031, RF-034, RF-061, RF-064, RF-065, RF-066, RF-067; AC-01, AC-02, AC-03, AC-04 (resolución), AC-22, AC-26, AC-29, AC-30, AC-31, AC-32 (obligaciones), AC-49, AC-50, AC-54.

---

## T04 — Cartelera, documentos y alertas

**Objetivo:** completar las consultas documentales y de alertas, incluyendo el algoritmo de pares, fallbacks y sanitización.

**Tests primero (rojo):**

- `tests/unit/test_documents.py`
  - universo de pares cliente/tipo `required_for='company'`;
  - snapshot actual;
  - faltantes con vivos y `cartelera_snapshot_incomplete`/`cartelera_fallback_live_documents`;
  - vivos caídos → snapshot anterior `(year, month)` + `cartelera_fallback_previous_snapshot`;
  - sin anterior → `partial`/`count_unavailable`, nunca 0;
  - `current_snapshot`/`live_document`/`previous_snapshot`;
  - `id=0`, `document_id=None` para par sin fila;
  - `total=pares`, `pending_count=total-valid`;
  - `rejected`/`missing` como brecha;
  - filtro de mes con `str(month)`.
- `tests/unit/test_alerts.py`
  - `pending`/`sent` por defecto, `done` solo explícito;
  - hoy Caracas con intervalo semiabierto;
  - últimas 5 + `no_alerts_today`;
  - orden `date desc nulls last, id`;
  - `date=False` + `null_alert_date`;
  - sanitización de montos, monedas, emails, `wa.me`, `api.whatsapp.com`, `tel:`, `mailto:` y teléfonos;
  - `message_clean` vacío cuando no queda texto útil.
- `tests/unit/test_document_state.py`
  - `state=False` → `pending` + `null_document_state`;
  - `expiry_date=False`;
  - `rejected`/`missing` prevalecen;
  - `effective_state_adjusted`.

**Implementación:**

- `query_documents` y `query_alerts`.
- Algoritmo puro de cartelera y materialización de pares.
- Sanitizador y orden de alertas.
- `DocumentsResult.pending_count` y warnings de fallback.
- Filtro explícito de compañía también en cartelera.

**Hecho cuando:**

- [ ] `pytest tests/unit/test_documents.py tests/unit/test_alerts.py tests/unit/test_document_state.py` pasa.
- [ ] Los tests cubren snapshot completo, incompleto, ausente, vivos caídos y sin snapshot anterior.
- [ ] `total` nunca se deriva de `items` y `pending_count` se recalcula en Python.
- [ ] Los tests de zona horaria demuestran el mes correcto a las 23:30 y 00:30 de Caracas.
- [ ] `message_clean` no contiene montos, moneda, emails, links ni teléfonos.

**Trazabilidad:** RF-029, RF-030, RF-031, RF-063, RF-064, RF-065, RF-069, RF-070; AC-05, AC-06, AC-23, AC-24, AC-27, AC-28, AC-32 (documentos/alertas), AC-42, AC-45, AC-47.

---

## T05 — Manifiesto, ingesta y RAG híbrido

**Objetivo:** implementar el corpus normativo local, su validación, la ingesta offline y el retrieval dense + BM25 con RRF y citas trazables.

**Tests primero (rojo):**

- `tests/unit/test_manifest.py`
  - manifiesto válido;
  - faltante, vacío, hash incorrecto, versión/hash BM25 distinto;
  - `corpus_status` `ok|missing|empty|mismatch`;
  - versión expuesta para health;
  - documentos fuera del manifiesto no indexables.
- `tests/unit/test_tokenizer.py`
  - mayúsculas, acentos y espacios;
  - acrónimos fiscales preservados;
  - alias ISLR/IVA/IGTF/RIF;
  - mismo tokenizador para indexar y consultar.
- `tests/unit/test_retrieval.py`
  - dense top 10, BM25 top 10, señal antes de RRF;
  - distancia Chroma convertida a similitud cosine antes del umbral;
  - dense `>=0.30` o BM25 `>=1.0`;
  - sin señal → `retrieval_low_score`;
  - RRF `k=60`, top 5, `Source.score` RRF;
  - `Source` con página/sección/locator;
  - `page=None` + `page_metadata_missing`.
- `tests/unit/test_corpus.py`
  - archivo BM25 ausente/corrupto;
  - hash/versión incorrectos;
  - apertura read-only y ausencia de escrituras;
  - Chroma ausente/colección vacía;
  - estado degradado sin excepción de arranque.
- `tests/integration/test_rag_tool.py`
  - tool normativa con retriever fake;
  - RAG-only con corpus inválido/bajo;
  - mixta baja conserva fuentes ERP.

**Implementación:**

- `rag/manifest.py`, `tokenizer.py`, `bm25.py`, `fusion.py`, `corpus.py`, `retriever.py`, `tool.py`.
- `scripts/ingest_corpus.py` offline con `pypdf`, Chroma y BM25; no se ejecuta al arrancar.
- Carga de BM25 en memoria, solo lectura, con versión/hash.
- Fuentes con metadatos y `score` RRF.
- Estado del corpus inyectable para health y tests.

**Hecho cuando:**

- [ ] `pytest tests/unit/test_manifest.py tests/unit/test_tokenizer.py tests/unit/test_retrieval.py tests/unit/test_corpus.py tests/integration/test_rag_tool.py` pasa sin Chroma remoto ni OpenAI.
- [ ] Los tests demuestran que el umbral se aplica antes de RRF y que `Source.score` es el score fusionado.
- [ ] Un manifiesto/BM25 inválido no lanza fail-fast y produce el estado de corpus correcto.
- [ ] La ingesta es invocable offline y no modifica archivos durante un turno.
- [ ] Ningún documento fuera del manifiesto puede aparecer en `Source`.

**Trazabilidad:** RF-040, RF-041, RF-042, RF-043, RF-044, RF-045, RF-046, RF-047, RF-071, RF-072, RF-073, RF-074, RF-075; RNF-017; AC-07, AC-08, AC-33, AC-34, AC-35, AC-36, AC-41, AC-53.

---

## T06 — Memoria, clasificación y grafo acotado

**Objetivo:** implementar el ciclo conversacional con historial limitado, clasificación tipada, planificación source-aware y presupuesto de tools independiente del `recursion_limit`.

**Tests primero (rojo):**

- `tests/unit/test_threads.py`
  - sin `thread_id` → UUID4 sin warning;
  - hilo existente;
  - expulsado por LRU → `thread_evicted`;
  - UUID4 desconocido → `thread_context_unavailable`;
  - ID no UUID4 nuevo sin warning;
  - `MEMORY_MAX_TURNS` → `thread_history_truncated`;
  - dos hilos concurrentes sin fuga;
  - el historial no guarda `data`/`sources`.
- `tests/unit/test_classifier.py`
  - `erp`, `normativa`, `mixta`, `conversacional`, `fuera_de_alcance`;
  - componentes fuera de alcance;
  - salida Pydantic válida;
  - fallo del LLM → `upstream_error`.
- `tests/unit/test_planner.py`
  - source-aware;
  - no repetir fuente resuelta;
  - prioridad ERP/RAG;
  - presupuesto exacto;
  - reintento cuenta;
  - truncamiento exige nueva paginación.
- `tests/integration/test_graph_budget.py`
  - cuarta tool → `iteration_limit` antes de ejecutar;
  - reintento sin presupuesto → `iteration_limit`;
  - exactamente 3 tools resueltas → `ok`/`partial`, no `iteration_limit`;
  - resolución de entidad desde el hilo no consume presupuesto; si consulta Odoo, sí;
  - `recursion_limit` → `iteration_limit` con estado parcial;
  - conversacional/out-of-scope no invocan tools;
  - seguimiento "¿y cuáles están vencidas?" vuelve a consultar;
  - nueva entidad reemplaza la anterior.

**Implementación:**

- `ThreadStore` con lock, LRU, set de expulsados y topes.
- `graph/state.py`, `classify.py`, `planner.py`, `executor.py`, `nodes.py`, `build.py`.
- Tools fake/stubs en tests; wiring real solo cuando T03/T05 estén listas.
- Contador propio de 3 tools y captura de `GraphRecursionError` como `iteration_limit`.
- `sources_used`/`sources_attempted` y registro de tools/retry.
- Historial solo de filtros/entidad/tiempo.

**Hecho cuando:**

- [ ] `pytest tests/unit/test_threads.py tests/unit/test_classifier.py tests/unit/test_planner.py tests/integration/test_graph_budget.py` pasa.
- [ ] Un test espía demuestra que la cuarta tool no se ejecuta y que `tool_call_count=3`.
- [ ] Exactamente 3 tools con fuentes resueltas no emiten `iteration_limit`.
- [ ] El seguimiento vuelve a consultar y no reutiliza hechos del turno anterior.
- [ ] El grafo no usa resultados crudos ni historial como fuente de hechos.

**Trazabilidad:** RF-006, RF-010, RF-011, RF-013, RF-014, RF-015, RF-018, RF-019, RF-054, RF-055, RF-057, RF-058, RF-059, RF-077; AC-09, AC-11, AC-13, AC-15, AC-17, AC-18, AC-40, AC-48, AC-51, AC-57.

---

## T07 — Respuesta grounded y matriz de estados

**Objetivo:** cerrar el flujo del grafo con reducer integrado, render determinista, validación de grounding y todas las combinaciones de error/parcialidad.

**Tests primero (rojo):**

- `tests/unit/test_answer.py`
  - render de `ok`/`partial` solo desde `data`/`sources`;
  - `insufficient_context` empieza con "No tengo información sobre eso";
  - `needs_clarification` pregunta concreta y solo `options`;
  - `not_found` sugiere hasta 5;
  - `out_of_scope` ofrece alternativa in-MVP;
  - fechas `dd/mm/aaaa`;
  - no montos ni moneda;
  - total/ID/fecha/cita inventados → fallback determinista.
- `tests/integration/test_status_matrix.py`
  - `invalid_request`;
  - `out_of_scope` total y `out_of_scope_component`;
  - `needs_clarification`;
  - `not_found` solo con alcance completo;
  - no-match incompleto → `partial`;
  - 0 resultados de entidad existente → `ok`;
  - ERP caído/RAG disponible → `partial`;
  - RAG caído/ERP disponible → `partial`;
  - ambos caídos → `upstream_error`;
  - RAG bajo sin ERP → `insufficient_context`;
  - RAG bajo con ERP → `partial`;
  - `iteration_limit` conserva datos;
  - `source_unavailable`, `manifest_mismatch`, `retrieval_low_score`, `corpus_unavailable`.
- `tests/integration/test_grounding.py`
  - el validador acepta solo hechos presentes en `data`/`sources`/`options`;
  - toda respuesta normativa incluye al menos un `Source`;
  - citas normativas solo de `sources`;
  - prompt injection en datos no cambia tools ni hechos.

**Implementación:**

- `domain/answer.py`: render determinista, validador y fallback.
- Integración del reducer en el nodo final.
- Compositor LLM solo para `ok`/`partial`; mensajes deterministas para el resto.
- Reglas de `data`, `sources`, `entities`, `options`, `truncated`, `warnings`.
- Estado final garantizado incluso ante error interno controlado.

**Hecho cuando:**

- [ ] `pytest tests/unit/test_answer.py tests/integration/test_status_matrix.py tests/integration/test_grounding.py` pasa.
- [ ] Cada fila de §8.3 tiene al menos una prueba de estado final.
- [ ] Un test confirma que `not_found` con alcance incompleto se transforma en `partial` y no afirma inexistencia.
- [ ] Un test inyecta un total/ID/cita falso y demuestra el fallback determinista.
- [ ] `answer` no contiene montos ni moneda en ningún estado.

**Trazabilidad:** RF-003, RF-005, RF-007, RF-012, RF-016, RF-017, RF-040, RF-041, RF-042, RF-043, RF-044, RF-047, RF-050, RF-056, RF-060; RNF-006; AC-04 (estado final), AC-10, AC-14, AC-23 (estado), AC-30 (estado), AC-33 (estado), AC-43, AC-46, AC-55 (estado), AC-57 (estado).

---

## T08 — FastAPI, auth, lifespan y health

**Objetivo:** exponer el servicio con el contrato HTTP correcto, arranque seguro y health operativo.

**Tests primero (rojo):**

- `tests/api/test_chat_api.py`
  - body malformado → `invalid_request` HTTP 200, UUID4, sin tools/LLM;
  - mensaje vacío/solo espacios/excedido → `invalid_request`;
  - `thread_id` inválido → UUID4 devuelto, mensaje no procesado;
  - sin `thread_id` → UUID4 y turno normal;
  - respuesta con todos los campos;
  - `X-API-Key` obligatorio si hay secreto, también en `/health`; 401 si falta/incorrecto;
  - comparación en tiempo constante (spy).
- `tests/api/test_health.py`
  - `/health` sin secretos;
  - `scope_state`, `scope_complete`, `corpus_status`, versión, Chroma/BM25, Odoo;
  - `scope_partial` arranca y responde 200;
  - refresh unsafe en producción → 503 y turnos 503 sin tools;
  - corpus inválido → health degradado, no crash.
- `tests/api/test_startup.py`
  - configuración inválida → no arranca;
  - producción unsafe inicial → no arranca;
  - dev/test unsafe admin → arranca degradado con `scope_admin_key`;
  - dev/test no interno → arranca degradado sin tools;
  - bind remoto sin secreto → no arranca.
- `tests/api/test_lifespan.py`
  - wiring de store, scope, corpus y grafo;
  - no se crean workers adicionales;
  - no se acepta tráfico antes de completar la inicialización.

**Implementación:**

- `main.py`, `api/routes.py`, `api/security.py`, `api/errors.py`.
- Parseo manual del body y validación antes del grafo.
- Lifespan: configuración, corpus, scope inicial, wiring, cierre limpio.
- `/health` con estado agregado y sin PII/secretos.
- 503 solo para unsafe runtime en producción; `invalid_request` siempre HTTP 200.
- Documentación de `uvicorn --workers 1` y bind local.

**Hecho cuando:**

- [ ] `pytest tests/api/test_chat_api.py tests/api/test_health.py tests/api/test_startup.py tests/api/test_lifespan.py` pasa.
- [ ] Un body no JSON y un `thread_id` inválido devuelven HTTP 200 con `invalid_request` y `thread_id`.
- [ ] Producción con unsafe inicial no arranca; refresh unsafe devuelve 503 y no ejecuta tools.
- [ ] `scope_partial` arranca y responde `partial`; nunca 503.
- [ ] Health no contiene secretos, RIF, nombres ni IDs de compañías.

**Trazabilidad:** RF-001, RF-002, RF-006, RF-052, RF-076, RF-077; RNF-013, RNF-016; AC-17, AC-18, AC-19, AC-20, AC-21, AC-38, AC-40, AC-44, AC-52, AC-55.

---

## T09 — Observabilidad, PII y hardening

**Objetivo:** demostrar que trazas, logs y respuestas no filtran PII/secretos y que el contenido recuperado no puede alterar la política del agente.

**Tests primero (rojo):**

- `tests/unit/test_redaction.py`
  - RIF, email, teléfono, nombres de clientes y números de documento;
  - mensaje completo y resultados crudos;
  - URLs internas, tokens y cadenas de conexión;
  - la redacción no altera IDs, conteos, estados ni nombres de tools.
- `tests/integration/test_tracing.py`
  - un turno con LangSmith habilitado no incluye PII;
  - metadata con `request_id`, tools, estado y latencia;
  - tests/CI deshabilitan trazas.
- `tests/integration/test_prompt_injection.py`
  - documento con "ignora las instrucciones" se cita como dato;
  - resultado ERP con instrucciones no ejecuta tools ni métodos;
  - el modelo no recibe capacidad de elegir dominios/campos/XML IDs;
  - no se revelan credenciales en el texto ni en warnings.
- `tests/unit/test_logging.py`
  - log info sin mensaje completo ni resultados;
  - `request_id` presente;
  - errores de Odoo sin headers ni key.

**Implementación:**

- Redactor común para logs, warnings y trazas.
- `observability/logging.py` y `tracing.py`.
- LangSmith con contenido oculto/redactado y metadata-only.
- Prompt boundaries para datos no confiables.
- Revisión de mensajes de error y `Warning` para no filtrar secretos.
- Tests adversariales de injection en RAG y ERP.

**Hecho cuando:**

- [ ] `pytest tests/unit/test_redaction.py tests/integration/test_tracing.py tests/integration/test_prompt_injection.py tests/unit/test_logging.py` pasa.
- [ ] Un test con PII en mensaje/documentos confirma que logs y trazas no la contienen.
- [ ] Un documento malicioso no cambia la allowlist, el catálogo de tools ni el estado final.
- [ ] Ningún warning/respuesta contiene secretos, URLs internas ni cadenas de conexión.
- [ ] LangSmith está deshabilitado en tests y habilitado por defecto en desarrollo con contenido oculto.

**Trazabilidad:** RF-051, RF-052, RF-053, RF-057, RF-078, RF-079; RNF-005, RNF-008, RNF-015; AC-16, AC-39.

---

## T10 — Cierre de aceptación y operación

**Objetivo:** auditar la trazabilidad completa, cerrar los requisitos operativos y dejar el MVP listo para revisión.

**Tests primero (rojo):**

- `tests/acceptance/test_acceptance_manifest.py`
  - exige que cada AC-01..AC-59 tenga un test que lo nombre o lo marque;
  - falla si hay IDs duplicados, faltantes o tests rotulados con un AC inexistente;
  - exige que la suite no abra sockets.
- `tests/acceptance/test_end_to_end.py`
  - flujo `/chat` con dobles para los estados principales;
  - `ok`, `partial`, `not_found`, `needs_clarification`, `out_of_scope`, `insufficient_context`, `iteration_limit`, `upstream_error`, `invalid_request`;
  - verifica `request_id`, `thread_id`, `data`, `sources`, `entities`, `options`, `warnings`, `truncated`.
- `tests/acceptance/test_security_audit.py`
  - no hay métodos de escritura en la allowlist;
  - no hay secretos en fixtures ni en `.env.example`;
  - no hay rutas de escritura en tools;
  - logs/trazas sin PII.

**Implementación:**

- Completar tests faltantes y corregir cualquier desviación de la matriz AC.
- Actualizar `requirements.txt` con Chroma, BM25, RapidFuzz, `pypdf`, `httpx`, Pydantic, settings y test.
- Completar `.env.example` con RNF-003, sin valores reales.
- README con ingesta offline, manifiesto/BM25, un worker, bind local, auth remota y operación read-only.
- Actualizar `docs/memory.md` y `AGENTS.md` después de la aprobación.
- Preparar el set dorado manual de 10 preguntas y su registro de ejecución.

**Hecho cuando:**

- [ ] `pytest` completo pasa sin red, sin tokens y sin servicios reales.
- [ ] La auditoría automatizada informa exactamente 59 AC cubiertos, sin faltantes ni IDs inválidos.
- [ ] `requirements.txt` incluye todas las dependencias de RNF-014 y `.env.example` todas las variables de RNF-003.
- [ ] README permite levantar el servicio con un solo worker, ingesta offline y configuración read-only.
- [ ] El set dorado manual de 10 preguntas se ejecuta contra `contea` y el corpus real sin alucinaciones, escrituras ni PII en trazas.
- [ ] `git status` confirma que no se agregaron archivos temporales ni secretos.

**Trazabilidad:** RF-080; RNF-014; DoD completo; AC-01..AC-59 (auditoría final).

---

## Regla de secuencia

1. No iniciar T02 sin T01 verde.
2. T03 y T04 comparten la capa Odoo; T04 depende de T03 para reutilizar resolución y contrato.
3. T05 puede avanzar en paralelo con T03/T04, pero T06 requiere ambos para el grafo integrado.
4. T07 no puede cerrarse sin T04/T05/T06.
5. T08 no puede cerrarse sin T07.
6. T09 y T10 son puertas de salida; un fallo de PII, inyección o AC bloquea el cierre.
