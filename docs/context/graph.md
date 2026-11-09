# CONTEXT: graph

## Purpose
Orquestar un turno conversacional acotado con LangGraph: clasificar intención, resolver entidades, planificar/ejecutar tools con presupuesto y componer una respuesta fundamentada.

## Responsibilities
- Mantener el estado tipado del turno (`TurnState`) entre nodos.
- Clasificar el mensaje en intención, slots y menciones de entidades.
- Resolver entidades y planificar acciones ERP/RAG.
- Ejecutar tools con máximo de llamadas y límite de recursión; reintentar o degradar.
- Componer la respuesta final y convertirla a `ChatResponse`.

## Public interface
- `run_turn`, `build_graph`, `GraphDependencies`, `RunTracker` — entrada y ensamblado del grafo.
- `TurnState`, `Slots`, `ToolArgs`, `PlanAction`, `ToolName`, `SourceKind` — estado y acciones.
- `classify`, `ClassificationDraft`, `ClassificationResult`, `ClassifierLLM` — clasificación.
- `plan_next`, `PlanContext`, `PlanDecision` — planificación.
- `execute_action`, `ToolOutcome`, `ToolCallable` — ejecución de tools.
- `finalize_node`, `to_chat_response` — cierre y contrato HTTP.

## Dependencies
- Internal: `domain` (contratos, enums, respuesta, estados, tiempo), `memory` (`ThreadStore`), `odoo` (`ScopeSnapshot`, `resolve_entity`), `observability` (redacción y tracing).
- External: `langgraph` (`StateGraph`), `pydantic`.

## Persistence / State
- `TurnState` es efímero por turno.
- `ThreadStore` persiste los hilos en memoria del proceso (LRU, `memory_max_threads`/`memory_max_turns`).
- No hay base de datos propia.

## Commands
- Build: `pip install -r requirements.txt`
- Test: `pytest tests/unit/test_classifier.py tests/unit/test_planner.py tests/unit/test_status_reducer.py tests/integration/test_graph_budget.py tests/integration/test_status_matrix.py tests/integration/test_grounding.py -q`
- Run: `uvicorn contafis_assistant.main:app --app-dir src --host 0.0.0.0 --port 8000` (el grafo se ejecuta dentro del runtime HTTP)

## Gotchas / Observed tech debt
- `TurnState` concentra 35 campos (intención, slots, entidades, datos, fuentes, presupuesto, warnings y flags de control).
- El grafo importa directamente `odoo.scope.ScopeSnapshot` y `odoo.tools.entities`, lo que acopla la orquestación al contexto ERP.
- El clasificador se inyecta como `ClassifierFn`; el prompt real vive en `main.py` y está redactado por NDA.
- `MAX_TOOL_CALLS` está acotado a 3 en `config.py`; `RECURSION_LIMIT` debe ser `>= 2*MAX_TOOL_CALLS+4`.
- `nodes.py` supera las 400 líneas y contiene los nodos, routers y la lógica de finalización.

## Last updated
2026-11-09 (skill 0.1.0)
