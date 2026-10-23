"""Construcción del StateGraph acotado y ejecución de un turno."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph

from contafis_assistant.domain.answer import AnswerComposer
from contafis_assistant.domain.contracts import EntityRef, Warning
from contafis_assistant.domain.enums import EntityType, Status
from contafis_assistant.domain.time import Clock
from contafis_assistant.graph.classify import ClassificationResult
from contafis_assistant.graph.executor import ToolCallable
from contafis_assistant.graph.nodes import (
    ClassifierFn,
    EntityResolverFn,
    PlannerFn,
    classify_node,
    execute_node,
    finalize_node,
    plan_node,
    prepare_node,
    resolve_entity_node,
    route_after_classify,
    route_after_execute,
    route_after_plan,
    route_after_resolve,
)
from contafis_assistant.graph.planner import PlanContext, PlanDecision, plan_next
from contafis_assistant.graph.state import TurnState
from contafis_assistant.memory.threads import (
    ThreadFilters,
    ThreadResolution,
    ThreadStore,
    ThreadTurn,
    TimeRange,
)
from contafis_assistant.odoo.scope import ScopeSnapshot


class RunTracker:
    def __init__(self) -> None:
        self.state: TurnState | None = None


@dataclass
class GraphDependencies:
    classifier: ClassifierFn
    tools: Mapping[Any, ToolCallable]
    planner: PlannerFn = plan_next
    entity_resolver: EntityResolverFn | None = None
    store: ThreadStore | None = None
    clock: Clock = field(default_factory=Clock)
    max_tool_calls: int = 3
    recursion_limit: int = 12
    scope_snapshot: ScopeSnapshot | None = None
    answer_composer: AnswerComposer | None = None


def _as_state(value: Any) -> TurnState:
    if isinstance(value, TurnState):
        return value
    return TurnState.model_validate(value)


def _wrap(
    node: Callable[[TurnState], TurnState],
    tracker: RunTracker | None,
) -> Callable[[Any], dict[str, Any]]:
    def run(value: Any) -> dict[str, Any]:
        current = _as_state(value)
        updated = node(current)
        if tracker is not None:
            tracker.state = updated
        return updated.model_dump(mode="python")

    return run


def _route(
    router: Callable[[TurnState], str],
) -> Callable[[Any], str]:
    def run(value: Any) -> str:
        return router(_as_state(value))

    return run


def build_graph(
    deps: GraphDependencies,
    *,
    tracker: RunTracker | None = None,
):
    builder = StateGraph(TurnState)

    builder.add_node("prepare", _wrap(prepare_node, tracker))
    builder.add_node(
        "classify",
        _wrap(lambda state: classify_node(state, deps.classifier), tracker),
    )
    builder.add_node(
        "resolve_entity",
        _wrap(
            lambda state: resolve_entity_node(state, deps.entity_resolver),
            tracker,
        ),
    )
    builder.add_node(
        "plan",
        _wrap(lambda state: plan_node(state, deps.planner), tracker),
    )
    builder.add_node(
        "execute",
        _wrap(
            lambda state: execute_node(
                state,
                tools=deps.tools,
                max_tool_calls=deps.max_tool_calls,
            ),
            tracker,
        ),
    )
    builder.add_node(
        "finalize",
        _wrap(
            lambda state: finalize_node(state, deps.answer_composer),
            tracker,
        ),
    )

    builder.set_entry_point("prepare")
    builder.add_edge("prepare", "classify")
    builder.add_conditional_edges(
        "classify",
        _route(route_after_classify),
        {
            "resolve_entity": "resolve_entity",
            "plan": "plan",
            "finalize": "finalize",
        },
    )
    builder.add_conditional_edges(
        "resolve_entity",
        _route(route_after_resolve),
        {"plan": "plan", "finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "plan",
        _route(route_after_plan),
        {"execute": "execute", "finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "execute",
        _route(route_after_execute),
        {"plan": "plan", "finalize": "finalize"},
    )
    builder.add_edge("finalize", END)

    return builder.compile()


def _resolve_thread(
    store: ThreadStore | None,
    thread_id: str | None,
) -> ThreadResolution:
    if store is None:
        return ThreadResolution(
            thread_id=thread_id or str(uuid.uuid4()),
            is_new=True,
        )
    return store.resolve(thread_id)


def _turn_from_state(state: TurnState) -> ThreadTurn:
    entity: EntityRef | None = None
    if state.entities:
        entity = state.entities[-1]
    elif state.entity_mentions:
        entity = None
    elif state.history:
        entity = state.history[-1].entity

    filters = ThreadFilters(
        company_id=(
            entity.id
            if entity is not None and entity.type is EntityType.company
            else None
        ),
        client_id=(
            entity.id
            if entity is not None and entity.type is EntityType.client
            else None
        ),
        tax_type=state.slots.tax_type,
        states=list(state.slots.states),
        topic=state.slots.topic,
        document_type_id=state.slots.document_type_id,
    )
    period = None
    if state.slots.period_start is not None or state.slots.period_end is not None:
        period = TimeRange(
            start=state.slots.period_start,
            end=state.slots.period_end,
        )
    return ThreadTurn(entity=entity, filters=filters, period=period)


def _record(
    state: TurnState,
    store: ThreadStore,
) -> TurnState:
    resolution = store.append_turn(state.thread_id, _turn_from_state(state))
    if not resolution.warnings:
        return state
    warnings = list(state.thread_warnings)
    known = {warning.code for warning in warnings}
    for warning in resolution.warnings:
        if warning.code in known:
            continue
        known.add(warning.code)
        warnings.append(warning)
    return state.model_copy(
        update={
            "thread_warnings": warnings,
            "warnings": [*state.warnings, *resolution.warnings],
        }
    )


def run_turn(
    message: str,
    *,
    deps: GraphDependencies,
    thread_id: str | None = None,
    request_id: str | None = None,
    recursion_limit: int | None = None,
    scope_snapshot: ScopeSnapshot | None = None,
    warnings: list[Warning] | None = None,
) -> TurnState:
    resolution = _resolve_thread(deps.store, thread_id)
    initial = TurnState(
        request_id=request_id or str(uuid.uuid4()),
        thread_id=resolution.thread_id,
        message=message,
        history=resolution.history,
        thread_warnings=resolution.warnings,
        scope_snapshot=(
            scope_snapshot if scope_snapshot is not None else deps.scope_snapshot
        ),
        warnings=list(warnings or []),
        remaining_budget=deps.max_tool_calls,
    )

    tracker = RunTracker()
    graph = build_graph(deps, tracker=tracker)
    limit = recursion_limit or deps.recursion_limit

    try:
        raw = graph.invoke(
            initial.model_dump(mode="python"),
            config={"recursion_limit": limit},
        )
        final = _as_state(raw)
    except GraphRecursionError:
        base = tracker.state or initial
        final = base.model_copy(deep=True).model_copy(
            update={
                "status": Status.iteration_limit,
                "iteration_limit_reason": "recursion_limit",
                "is_final": True,
                "next_action": None,
                "remaining_budget": max(
                    0, deps.max_tool_calls - base.tool_call_count
                ),
            }
        )
        final = finalize_node(final)
    except Exception:
        base = tracker.state or initial
        final = finalize_node(
            base.model_copy(
                update={
                    "status": Status.upstream_error,
                    "is_final": True,
                    "next_action": None,
                }
            )
        )

    if not final.answer:
        final = finalize_node(final)

    if deps.store is not None:
        final = _record(final, deps.store)

    return final


__all__ = [
    "GraphDependencies",
    "PlanContext",
    "PlanDecision",
    "RunTracker",
    "build_graph",
    "run_turn",
]
