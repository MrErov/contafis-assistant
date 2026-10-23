"""Ensamblador de `ChatResponse` a partir del estado final del grafo."""

from __future__ import annotations

import re
import uuid

from contafis_assistant.domain.answer import AnswerContext, render_answer
from contafis_assistant.domain.contracts import (
    THREAD_ID_PATTERN,
    ChatResponse,
    Warning,
)
from contafis_assistant.domain.enums import Status, WarningCode, WarningSource
from contafis_assistant.graph.state import TurnState

_THREAD_PATTERN = re.compile(THREAD_ID_PATTERN)


def _is_uuid4(value: str) -> bool:
    try:
        return uuid.UUID(value).version == 4
    except (ValueError, AttributeError, TypeError):
        return False


def _request_id(value: str | None) -> str:
    if isinstance(value, str) and _is_uuid4(value):
        return str(uuid.UUID(value))
    return str(uuid.uuid4())


def _thread_id(value: str | None) -> str:
    if isinstance(value, str) and _THREAD_PATTERN.fullmatch(value):
        return value
    return str(uuid.uuid4())


def _scope_warning() -> Warning:
    return Warning(
        code=WarningCode.scope_partial,
        message="El alcance de compañías es incompleto.",
        source=WarningSource.system,
    )


def _has_warning(warnings: list[Warning], code: WarningCode) -> bool:
    return any(warning.code is code for warning in warnings)


def _context(
    state: TurnState,
    status: Status,
    options,
) -> AnswerContext:
    return AnswerContext(
        status=status,
        intent=state.intent,
        data=list(state.data),
        sources=list(state.sources),
        entities=list(state.entities),
        options=list(options),
        warnings=list(state.warnings),
        truncated=state.truncated,
        message=state.message,
    )


def _resolve_status(state: TurnState) -> tuple[Status, list[Warning]]:
    warnings = list(state.warnings)
    status = state.status
    if status is None:
        status = Status.ok if (state.data or state.sources) else Status.upstream_error

    scope_complete = (
        state.scope_snapshot.scope_complete
        if state.scope_snapshot is not None
        else True
    )
    if status is Status.not_found and not scope_complete:
        status = Status.partial
        if not _has_warning(warnings, WarningCode.scope_partial):
            warnings.append(_scope_warning())
    return status, warnings


def to_chat_response(state: TurnState) -> ChatResponse:
    """Devuelve una respuesta válida y coherente para cualquier estado final."""
    status, warnings = _resolve_status(state)

    options = (
        list(state.options)
        if status in {Status.needs_clarification, Status.not_found}
        else []
    )
    context = _context(state, status, options)
    answer = state.answer
    if not answer or status is not state.status:
        answer = render_answer(context)

    truncated = state.truncated or any(result.truncated for result in state.data)

    return ChatResponse(
        request_id=_request_id(state.request_id),
        thread_id=_thread_id(state.thread_id),
        status=status,
        answer=answer,
        data=list(state.data),
        sources=list(state.sources),
        tools_used=list(state.tools_used),
        tool_call_count=state.tool_call_count,
        entities=list(state.entities),
        options=options,
        truncated=truncated,
        warnings=warnings,
    )


__all__ = ["to_chat_response"]
