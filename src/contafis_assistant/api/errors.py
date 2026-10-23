"""Traducción de fallos a respuestas HTTP seguras."""

from __future__ import annotations

import uuid

from contafis_assistant.domain.answer import AnswerContext, render_answer
from contafis_assistant.domain.contracts import ChatResponse
from contafis_assistant.domain.enums import Status
from contafis_assistant.domain.request import RequestDecision


class ServiceUnavailable(RuntimeError):
    """El servicio no puede atender turnos (inicialización o unsafe runtime)."""


def invalid_request_response(decision: RequestDecision) -> ChatResponse:
    """Respuesta `invalid_request` HTTP 200 sin tools ni LLM."""
    return ChatResponse(
        request_id=str(uuid.uuid4()),
        thread_id=decision.thread_id,
        status=Status.invalid_request,
        answer=render_answer(
            AnswerContext(
                status=Status.invalid_request,
                message=decision.message or "",
            )
        ),
        tool_call_count=0,
        truncated=False,
    )


def unavailable_payload() -> dict[str, str]:
    return {"detail": "service_unavailable"}


__all__ = [
    "ServiceUnavailable",
    "invalid_request_response",
    "unavailable_payload",
]
