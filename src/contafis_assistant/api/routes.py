"""Endpoints `/chat` y `/health` con parseo manual del body."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from contafis_assistant.api.errors import (
    ServiceUnavailable,
    invalid_request_response,
)
from contafis_assistant.domain.contracts import ChatResponse
from contafis_assistant.domain.request import validate_chat_request

router = APIRouter()


def _runtime(request: Request) -> Any:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None or not getattr(runtime, "ready", False):
        raise ServiceUnavailable("el runtime aún no está inicializado")
    return runtime


def _parse_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request) -> ChatResponse:
    runtime = _runtime(request)
    payload = _parse_json(await request.body())
    decision = validate_chat_request(
        payload,
        max_message_chars=runtime.settings.max_message_chars,
    )

    if not decision.ok:
        return invalid_request_response(decision)

    return runtime.chat(
        decision.message or "",
        thread_id=None if decision.thread_id_generated else decision.thread_id,
        request_id=str(uuid.uuid4()),
    )


@router.get("/health")
def health(request: Request) -> JSONResponse:
    runtime = _runtime(request)
    try:
        status_code, payload = runtime.health()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"detail": "service_unavailable"},
        )
    return JSONResponse(status_code=status_code, content=payload)


__all__ = ["router"]
