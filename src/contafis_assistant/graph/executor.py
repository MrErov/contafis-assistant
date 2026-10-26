"""Ejecución acotada de tools con validación previa y registro de fuentes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.contracts import Source, ToolResult, Warning
from contafis_assistant.domain.enums import Status, WarningCode, WarningSource
from contafis_assistant.graph.state import (
    PlanAction,
    SourceKind,
    ToolArgs,
    ToolName,
    TurnState,
)
from contafis_assistant.observability.redaction import redact_exception


class ToolOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status = Status.ok
    data: list[ToolResult] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
    truncated: bool = False
    retryable: bool = False


class ToolCallable(Protocol):
    def __call__(self, args: ToolArgs) -> ToolOutcome: ...


def _warning(code: WarningCode, message: str) -> Warning:
    return Warning(code=code, message=message, source=WarningSource.system)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _append_source(values: list[SourceKind], value: SourceKind) -> None:
    if value not in values:
        values.append(value)


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


def _lookup(
    tools: Mapping[Any, ToolCallable],
    tool: ToolName,
) -> ToolCallable | None:
    candidate = tools.get(tool)
    if candidate is None:
        candidate = tools.get(tool.value)
    return candidate


def _normalize(raw: Any) -> ToolOutcome:
    if isinstance(raw, ToolOutcome):
        return raw
    if isinstance(raw, BaseModel):
        return ToolOutcome.model_validate(raw.model_dump())
    return ToolOutcome.model_validate(raw)


def _iteration_limit(state: TurnState, reason: str) -> TurnState:
    return state.model_copy(
        update={
            "status": Status.iteration_limit,
            "iteration_limit_reason": reason,
            "is_final": True,
            "next_action": None,
        }
    )


def _rejected(
    state: TurnState,
    action: PlanAction,
    code: WarningCode,
    message: str,
) -> TurnState:
    return state.model_copy(
        update={
            "status": Status.upstream_error,
            "is_final": True,
            "next_action": None,
            "tool_errors": [*state.tool_errors, message],
            "warnings": _merge_warnings(
                state.warnings, [_warning(code, message)]
            ),
        }
    )


def execute_action(
    state: TurnState,
    action: PlanAction,
    *,
    tools: Mapping[Any, ToolCallable],
    max_tool_calls: int = 3,
) -> TurnState:
    remaining = min(
        state.remaining_budget,
        max(0, max_tool_calls - state.tool_call_count),
    )
    if action.requires_budget and remaining < 1:
        return _iteration_limit(state, "tool_budget")

    if (
        state.last_truncated
        and state.last_tool == action.tool
        and state.last_args == action.args
    ):
        return _rejected(
            state,
            action,
            WarningCode.invalid_tool_args,
            "El reintento por truncamiento debe cambiar offset o limit.",
        )

    tool = _lookup(tools, action.tool)
    if tool is None:
        return _rejected(
            state,
            action,
            WarningCode.invalid_tool_args,
            f"La tool {action.tool.value} no está registrada.",
        )

    tool_call_count = state.tool_call_count + 1
    tools_used = list(state.tools_used)
    _append_unique(tools_used, action.tool.value)
    sources_attempted = list(state.sources_attempted)
    _append_source(sources_attempted, action.source)
    retried_sources = list(state.retried_sources)
    if action.is_retry:
        _append_source(retried_sources, action.source)

    common = {
        "tool_call_count": tool_call_count,
        "tools_used": tools_used,
        "sources_attempted": sources_attempted,
        "retried_sources": retried_sources,
        "remaining_budget": max(0, remaining - 1),
        "last_tool": action.tool,
        "last_args": action.args,
        "next_action": None,
    }

    try:
        outcome = _normalize(tool(action.args))
    except Exception as exc:
        message = f"La tool {action.tool.value} falló: {redact_exception(exc)}"
        retryable_source = state.retryable_source
        if action.source not in retried_sources:
            retryable_source = action.source
        return state.model_copy(
            update={
                **common,
                "retryable_source": retryable_source,
                "tool_errors": [*state.tool_errors, message],
                "warnings": _merge_warnings(
                    state.warnings,
                    [_warning(WarningCode.source_unavailable, message)],
                ),
            }
        )

    data = [*state.data, *outcome.data]
    sources = [*state.sources, *outcome.sources]
    sources_used = list(state.sources_used)
    if outcome.status is not Status.upstream_error:
        _append_source(sources_used, action.source)

    retryable_source = state.retryable_source
    if outcome.retryable and action.source not in retried_sources:
        retryable_source = action.source
    elif not outcome.retryable:
        retryable_source = None

    tool_errors = list(state.tool_errors)
    if outcome.status is Status.upstream_error:
        tool_errors.append(f"La tool {action.tool.value} devolvió upstream_error.")

    return state.model_copy(
        update={
            **common,
            "data": data,
            "sources": sources,
            "sources_used": sources_used,
            "retryable_source": retryable_source,
            "warnings": _merge_warnings(state.warnings, outcome.warnings),
            "truncated": state.truncated or outcome.truncated,
            "last_truncated": outcome.truncated,
            "tool_errors": tool_errors,
        }
    )


__all__ = ["ToolCallable", "ToolOutcome", "execute_action"]
