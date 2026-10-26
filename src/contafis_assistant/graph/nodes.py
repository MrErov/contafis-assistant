"""Nodos del grafo acotado: clasificación, entidad, plan, ejecución y cierre."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel

from contafis_assistant.domain.answer import (
    AnswerComposer,
    AnswerContext,
    compose_answer,
    render_answer,
)
from contafis_assistant.domain.contracts import Warning
from contafis_assistant.domain.enums import Intent, Status, WarningCode, WarningSource
from contafis_assistant.domain.status import StatusSignals, reduce_status
from contafis_assistant.graph.classify import ClassificationResult
from contafis_assistant.graph.executor import execute_action
from contafis_assistant.graph.planner import PlanContext, PlanDecision, plan_next
from contafis_assistant.graph.state import PlanAction, TurnState
from contafis_assistant.odoo.tools.entities import (
    EntityResolution,
    normalize_entity_name,
)
from contafis_assistant.observability.redaction import redact_exception

WARNING_MESSAGES = {
    WarningCode.scope_partial: "El alcance de compañías es incompleto.",
    WarningCode.out_of_scope_component: "Parte de la consulta está fuera del alcance del MVP.",
    WarningCode.source_unavailable: "Una de las fuentes no está disponible.",
    WarningCode.retrieval_low_score: "No hubo señal suficiente en el corpus normativo.",
    WarningCode.corpus_unavailable: "El corpus normativo no está disponible.",
    WarningCode.manifest_mismatch: "El corpus no coincide con el manifiesto.",
    WarningCode.invalid_tool_args: "Los argumentos de la tool no son válidos.",
}

ENTITY_FINAL_STATUSES = {
    Status.needs_clarification,
    Status.not_found,
    Status.partial,
}

ClassifierFn = Callable[[str], ClassificationResult]
EntityResolverFn = Callable[[str], EntityResolution]
PlannerFn = Callable[[PlanContext], PlanDecision]


def _warning(code: WarningCode, message: str | None = None) -> Warning:
    return Warning(
        code=code,
        message=message or WARNING_MESSAGES.get(code, code.value),
        source=WarningSource.system,
    )


def _merge_warnings(existing: list[Warning], extra: list[Warning]) -> list[Warning]:
    result = list(existing)
    seen = {(warning.code, warning.message) for warning in result}
    for warning in extra:
        key = (warning.code, warning.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(warning)
    return result


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _iteration_limit(state: TurnState, reason: str) -> TurnState:
    return state.model_copy(
        update={
            "status": Status.iteration_limit,
            "iteration_limit_reason": reason,
            "is_final": True,
            "next_action": None,
        }
    )


def _model(value: Any, model_type: type[BaseModel]) -> Any:
    if isinstance(value, model_type):
        return value
    return model_type.model_validate(value)


def _history_entity(state: TurnState):
    if not state.history:
        return None
    return state.history[-1].entity


def prepare_node(state: TurnState) -> TurnState:
    updates = {
        "remaining_budget": max(
            0, state.remaining_budget - state.tool_call_count
        ),
        "warnings": _merge_warnings(state.warnings, state.thread_warnings),
    }
    if not state.entity_mentions and not state.entities and state.history:
        history_entity = state.history[-1].entity
        if history_entity is not None:
            updates["entities"] = [history_entity]
    return state.model_copy(update=updates)


def classify_node(state: TurnState, classifier: ClassifierFn) -> TurnState:
    if state.classified or state.is_final:
        return state

    try:
        raw = classifier(state.message)
        result = _model(raw, ClassificationResult)
    except Exception as exc:
        return state.model_copy(
            update={
                "classified": True,
                "status": Status.upstream_error,
                "is_final": True,
                "warnings": _merge_warnings(
                    state.warnings,
                    [
                        _warning(
                            WarningCode.source_unavailable,
                            f"No se pudo clasificar la consulta: {redact_exception(exc)}",
                        )
                    ],
                ),
            }
        )

    if result.failed or result.status is Status.upstream_error:
        return state.model_copy(
            update={
                "classified": True,
                "status": Status.upstream_error,
                "is_final": True,
                "warnings": _merge_warnings(state.warnings, result.warnings),
            }
        )

    draft = result.classification
    if draft is None:
        return state.model_copy(
            update={
                "classified": True,
                "status": Status.upstream_error,
                "is_final": True,
                "warnings": _merge_warnings(
                    state.warnings,
                    [_warning(WarningCode.source_unavailable)],
                ),
            }
        )

    is_terminal = draft.intent in {
        Intent.conversacional,
        Intent.fuera_de_alcance,
    }
    status = Status.out_of_scope if is_terminal and result.out_of_scope_all else state.status
    return state.model_copy(
        update={
            "classified": True,
            "intent": draft.intent,
            "slots": draft.slots,
            "entity_mentions": list(draft.entity_mentions),
            "out_of_scope_components": list(draft.out_of_scope_components),
            "warnings": _merge_warnings(state.warnings, result.warnings),
            "status": status,
            "is_final": is_terminal,
        }
    )


def resolve_entity_node(
    state: TurnState,
    resolver: EntityResolverFn | None = None,
) -> TurnState:
    if state.is_final:
        return state

    history_entity = _history_entity(state)
    mentions = [mention for mention in state.entity_mentions if mention.strip()]

    if not mentions:
        if history_entity is None:
            return state
        return state.model_copy(update={"entities": [history_entity]})

    mention = mentions[0].strip()
    if history_entity is not None and normalize_entity_name(
        history_entity.name
    ) == normalize_entity_name(mention):
        return state.model_copy(update={"entities": [history_entity]})

    if resolver is None:
        return state

    if state.remaining_budget < 1:
        return _iteration_limit(state, "tool_budget")

    tool_call_count = state.tool_call_count + 1
    tools_used = list(state.tools_used)
    _append_unique(tools_used, "resolve_entity")
    common = {
        "tool_call_count": tool_call_count,
        "tools_used": tools_used,
        "remaining_budget": max(0, state.remaining_budget - 1),
    }

    try:
        resolution = _model(resolver(mention), EntityResolution)
    except Exception as exc:
        return state.model_copy(
            update={
                **common,
                "status": Status.upstream_error,
                "is_final": True,
                "warnings": _merge_warnings(
                    state.warnings,
                    [
                        _warning(
                            WarningCode.source_unavailable,
                            f"No se pudo resolver la entidad: {redact_exception(exc)}",
                        )
                    ],
                ),
            }
        )

    warnings = _merge_warnings(state.warnings, resolution.warnings)

    if resolution.status is Status.ok and resolution.entity is not None:
        return state.model_copy(
            update={
                **common,
                "entities": [resolution.entity],
                "options": list(resolution.options),
                "warnings": warnings,
            }
        )

    if resolution.status in ENTITY_FINAL_STATUSES:
        return state.model_copy(
            update={
                **common,
                "entities": [],
                "options": list(resolution.options),
                "warnings": warnings,
                "status": resolution.status,
                "is_final": True,
            }
        )

    return state.model_copy(
        update={
            **common,
            "status": resolution.status
            if resolution.status is not Status.ok
            else Status.upstream_error,
            "options": list(resolution.options),
            "warnings": warnings,
            "is_final": True,
        }
    )


def _plan_context(state: TurnState) -> PlanContext:
    if state.entities:
        entity = state.entities[-1]
    elif state.entity_mentions:
        entity = None
    else:
        entity = _history_entity(state)
    entity_type = entity.type.value if entity is not None else None
    return PlanContext(
        intent=state.intent or Intent.conversacional,
        slots=state.slots,
        message=state.message,
        sources_used=list(state.sources_used),
        sources_attempted=list(state.sources_attempted),
        retryable_source=state.retryable_source,
        retried_sources=list(state.retried_sources),
        remaining_budget=state.remaining_budget,
        truncated_tool=state.last_tool if state.last_truncated else None,
        last_args=state.last_args,
        entity_id=entity.id if entity is not None else None,
        entity_type=entity_type,
        entity_company_id=entity.company_id if entity is not None else None,
    )


def plan_node(state: TurnState, planner: PlannerFn = plan_next) -> TurnState:
    if state.is_final:
        return state
    if state.intent is None:
        return state.model_copy(update={"is_final": True})

    decision = _model(planner(_plan_context(state)), PlanDecision)
    if decision.iteration_limit:
        return _iteration_limit(state, decision.reason or "tool_budget")
    if decision.action is None:
        return state.model_copy(update={"is_final": True, "next_action": None})
    return state.model_copy(update={"next_action": decision.action})


def execute_node(
    state: TurnState,
    *,
    tools: Mapping[Any, Callable[..., Any]],
    max_tool_calls: int = 3,
) -> TurnState:
    action: PlanAction | None = state.next_action
    if action is None:
        return state.model_copy(update={"is_final": True})
    return execute_action(
        state,
        action,
        tools=tools,
        max_tool_calls=max_tool_calls,
    )


def _signals(state: TurnState) -> StatusSignals:
    codes = {warning.code for warning in state.warnings}
    erp_attempted = "erp" in state.sources_attempted
    rag_attempted = "rag" in state.sources_attempted
    erp_failed = erp_attempted and "erp" not in state.sources_used
    rag_failed = rag_attempted and "rag" not in state.sources_used
    has_usable_data = bool(state.data or state.sources)
    return StatusSignals(
        request_valid=state.status is not Status.invalid_request,
        out_of_scope_all=(
            state.status is Status.out_of_scope
            or state.intent is Intent.fuera_de_alcance
        ),
        out_of_scope_component=WarningCode.out_of_scope_component in codes,
        needs_clarification=state.status is Status.needs_clarification,
        entity_not_found=state.status is Status.not_found,
        scope_complete=(
            state.scope_snapshot.scope_complete
            if state.scope_snapshot is not None
            else True
        ),
        iteration_limit=(
            state.status is Status.iteration_limit
            or state.iteration_limit_reason is not None
        ),
        upstream_error=(
            state.status is Status.upstream_error
            or (
                (erp_attempted or rag_attempted)
                and not has_usable_data
                and erp_failed + rag_failed == (erp_attempted + rag_attempted)
            )
        ),
        source_failed=(
            state.retryable_source is not None
            or WarningCode.source_unavailable in codes
            or WarningCode.corpus_unavailable in codes
            or erp_failed
            or rag_failed
        ),
        erp_attempted=erp_attempted,
        rag_attempted=rag_attempted,
        manifest_mismatch=WarningCode.manifest_mismatch in codes,
        corpus_unavailable=WarningCode.corpus_unavailable in codes,
        rag_low_score=WarningCode.retrieval_low_score in codes,
        has_erp_data=("erp" in state.sources_used) or bool(state.data),
        has_usable_data=has_usable_data,
        insufficient_context=state.status is Status.insufficient_context,
        zero_results_existing_entity=bool(state.entities) and bool(state.data),
    )


def _answer_context(
    state: TurnState,
    status: Status,
    warnings: list[Warning],
) -> AnswerContext:
    options = (
        list(state.options)
        if status in {Status.needs_clarification, Status.not_found}
        else []
    )
    return AnswerContext(
        status=status,
        intent=state.intent,
        data=list(state.data),
        sources=list(state.sources),
        entities=list(state.entities),
        options=options,
        warnings=list(warnings),
        truncated=state.truncated,
        message=state.message,
    )


def finalize_node(
    state: TurnState,
    composer: AnswerComposer | None = None,
) -> TurnState:
    try:
        decision = reduce_status(_signals(state))
        warnings = list(state.warnings)
        for code in decision.warnings:
            warnings = _merge_warnings(warnings, [_warning(code)])

        status = decision.status
        if state.status is Status.partial and not state.data and not state.sources:
            status = Status.partial
            warnings = _merge_warnings(
                warnings, [_warning(WarningCode.scope_partial)]
            )

        answer = compose_answer(
            _answer_context(state, status, warnings),
            composer=composer,
        )
    except Exception:
        status = Status.upstream_error
        warnings = list(state.warnings)
        answer = render_answer(
            AnswerContext(
                status=Status.upstream_error,
                intent=state.intent,
                message=state.message,
            )
        )

    return state.model_copy(
        update={
            "status": status,
            "answer": answer,
            "warnings": warnings,
            "remaining_budget": max(0, state.remaining_budget),
            "is_final": True,
            "next_action": None,
        }
    )


def route_after_classify(state: TurnState) -> str:
    if state.is_final:
        return "finalize"
    if any(mention.strip() for mention in state.entity_mentions):
        return "resolve_entity"
    return "plan"


def route_after_resolve(state: TurnState) -> str:
    return "finalize" if state.is_final else "plan"


def route_after_plan(state: TurnState) -> str:
    if state.is_final or state.next_action is None:
        return "finalize"
    return "execute"


def route_after_execute(state: TurnState) -> str:
    return "finalize" if state.is_final else "plan"
