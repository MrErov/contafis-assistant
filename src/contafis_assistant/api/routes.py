"""Endpoints `/chat`, `/scope` y `/health` con autenticación por token de usuario."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from contafis_assistant.api.errors import (
    ServiceUnavailable,
    invalid_request_response,
)
from contafis_assistant.api.user_auth import UserContext, get_user_context
from contafis_assistant.domain.contracts import ChatResponse, ScopeInfo
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
async def chat(
    request: Request,
    user_ctx: UserContext = Depends(get_user_context),
) -> ChatResponse:
    runtime = _runtime(request)
    raw_body = await request.body()
    payload = _parse_json(raw_body)

    decision = validate_chat_request(
        payload,
        max_message_chars=runtime.settings.max_message_chars,
    )

    if not decision.ok:
        return invalid_request_response(decision)

    # Ejecutar el turno con el cliente del usuario (scope del usuario)
    return runtime.chat_with_user(
        user_ctx,
        decision.message or "",
        thread_id=None if decision.thread_id_generated else decision.thread_id,
        request_id=str(uuid.uuid4()),
    )


@router.get("/scope", response_model=ScopeInfo)
def scope(user_ctx: UserContext = Depends(get_user_context)) -> ScopeInfo:
    """Devuelve el alcance del usuario autenticado (compañías visibles, estado de scope)."""
    from contafis_assistant.domain.enums import ScopeState

    # Mapear companies a CompanyRef
    company_refs = [
        {"id": c["id"], "name": c["name"]}
        for c in user_ctx.companies
    ]

    return ScopeInfo(
        uid=user_ctx.snapshot.uid or 0,
        companies=company_refs,
        scope_state=user_ctx.snapshot.state,
        scope_complete=user_ctx.snapshot.scope_complete,
        tools_enabled=user_ctx.snapshot.tools_enabled,
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