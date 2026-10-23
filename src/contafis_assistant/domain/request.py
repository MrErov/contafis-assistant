"""Validación manual y pura del request HTTP (RF-002, AC-19, AC-20, AC-44)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from contafis_assistant.domain.contracts import ChatRequest, THREAD_ID_PATTERN

InvalidReason = Literal[
    "invalid_body",
    "missing_message",
    "non_textual_message",
    "blank_message",
    "too_long_message",
    "invalid_thread_id",
]

_THREAD_ID_RE = re.compile(THREAD_ID_PATTERN)


class RequestDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    message: str | None = None
    thread_id: str
    thread_id_generated: bool = False
    reason: InvalidReason | None = None


def _new_uuid4() -> str:
    return str(uuid.uuid4())


def _as_mapping(body: Any) -> Mapping[str, Any] | None:
    if isinstance(body, ChatRequest):
        values: dict[str, Any] = dict(body.model_extra or {})
        values.update(body.model_dump(include={"message", "thread_id"}))
        return values
    if isinstance(body, Mapping):
        return body
    return None


def validate_chat_request(
    body: Any,
    *,
    max_message_chars: int = 2000,
    id_factory: Callable[[], str] = _new_uuid4,
) -> RequestDecision:
    """Valida `message` y `thread_id` sin I/O ni tools.

    Un request inválido siempre devuelve un `thread_id` UUID4 nuevo y
    `ok=False`, para que la capa HTTP pueda responder `invalid_request`.
    """
    payload = _as_mapping(body)
    if payload is None:
        return RequestDecision(
            ok=False,
            thread_id=id_factory(),
            thread_id_generated=True,
            reason="invalid_body",
        )

    generated = False
    raw_thread_id = payload.get("thread_id")
    thread_id: str
    if raw_thread_id is None:
        thread_id = id_factory()
        generated = True
    elif not isinstance(raw_thread_id, str) or not _THREAD_ID_RE.fullmatch(
        raw_thread_id
    ):
        thread_id = id_factory()
        generated = True
        return RequestDecision(
            ok=False,
            thread_id=thread_id,
            thread_id_generated=generated,
            reason="invalid_thread_id",
        )
    else:
        thread_id = raw_thread_id

    if "message" not in payload:
        return RequestDecision(
            ok=False,
            thread_id=thread_id,
            thread_id_generated=generated,
            reason="missing_message",
        )

    message = payload.get("message")
    if not isinstance(message, str):
        return RequestDecision(
            ok=False,
            thread_id=thread_id,
            thread_id_generated=generated,
            reason="non_textual_message",
        )
    if not message.strip():
        return RequestDecision(
            ok=False,
            thread_id=thread_id,
            thread_id_generated=generated,
            reason="blank_message",
        )
    if len(message) > max_message_chars:
        return RequestDecision(
            ok=False,
            thread_id=thread_id,
            thread_id_generated=generated,
            reason="too_long_message",
        )

    return RequestDecision(
        ok=True,
        message=message,
        thread_id=thread_id,
        thread_id_generated=generated,
    )
