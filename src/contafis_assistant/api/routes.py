"""Endpoints `/chat`, `/scope`, `/health` y `/digitize` con autenticación por sesión."""

from __future__ import annotations

import asyncio
import json
import traceback
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from contafis_assistant.api.digitize import (
    get_digitize_service,
    NIMError,
    NIMTimeoutError,
    NIMParsingError,
)
from contafis_assistant.api.errors import (
    ServiceUnavailable,
    invalid_request_response,
)
from contafis_assistant.api.user_auth import UserContext, get_user_context_from_session
from contafis_assistant.domain.contracts import ChatResponse, ScopeInfo
from contafis_assistant.domain.digitize import (
    DigitizeBatchItemResult,
    DigitizeBatchRequest,
    DigitizeBatchResponse,
    DigitizeRequest,
    DigitizeResponse,
)
from contafis_assistant.domain.request import validate_chat_request

router = APIRouter()

# Pausa entre imágenes para no saturar NIM (límites de rate del proveedor).
DIGITIZE_BATCH_DELAY_SECONDS = 2.0


def _digitize_error_detail(exc: Exception) -> str:
    """Mensaje accionable por tipo de fallo (espeja el mapeo de /digitize)."""
    if isinstance(exc, NIMTimeoutError):
        return "El servicio de digitalización tardó demasiado con esta imagen."
    if isinstance(exc, NIMParsingError):
        return "El servicio devolvió una respuesta ilegible para esta imagen."
    if isinstance(exc, NIMError):
        return "El servicio de digitalización no pudo procesar esta imagen."
    if isinstance(exc, ValueError):
        return str(exc)
    return "Error inesperado al digitalizar esta imagen."


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
    user_ctx: UserContext = Depends(get_user_context_from_session),
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
    # Usar run_in_threadpool para no bloquear el event loop con código síncrono
    try:
        return await run_in_threadpool(
            runtime.chat_with_user,
            user_ctx,
            decision.message or "",
            thread_id=None if decision.thread_id_generated else decision.thread_id,
            request_id=str(uuid.uuid4()),
        )
    except Exception as exc:
        # Print full traceback for debugging classifier/LLM issues
        print(f"CHAT ERROR: {exc}", flush=True)
        traceback.print_exc()
        raise


@router.get("/scope", response_model=ScopeInfo)
def scope(user_ctx: UserContext = Depends(get_user_context_from_session)) -> ScopeInfo:
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


@router.get("/livez")
def livez() -> dict[str, str]:
    """Liveness probe: responde 200 sin autenticación ni dependencias de runtime."""
    from contafis_assistant import __version__
    return {"status": "alive", "app_version": __version__}


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


@router.post("/digitize", response_model=DigitizeResponse)
async def digitize(
    request: Request,
    digitize_request: DigitizeRequest,
    user_ctx: UserContext = Depends(get_user_context_from_session),
) -> DigitizeResponse:
    """Digitaliza factura venezolana desde imagen base64 usando LLM Vision (NIM).
    
    Requiere autenticación por X-Odoo-Session o X-User-Token (legacy).
    """
    runtime = _runtime(request)
    service = get_digitize_service(runtime.settings)
    
    try:
        return await run_in_threadpool(service.digitize, digitize_request)
    except NIMTimeoutError:
        raise HTTPException(status_code=503, detail="service_unavailable") from None
    except NIMParsingError:
        raise HTTPException(status_code=503, detail="service_unavailable") from None
    except NIMError:
        raise HTTPException(status_code=503, detail="service_unavailable") from None
    except ValueError as e:
        # Errores de validación de request (base64 inválido, etc.)
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.post("/digitize_batch", response_model=DigitizeBatchResponse)
async def digitize_batch(
    request: Request,
    digitize_request: DigitizeBatchRequest,
    user_ctx: UserContext = Depends(get_user_context_from_session),
) -> DigitizeBatchResponse:
    """Digitaliza hasta 20 imágenes en un solo turno (Spec 006, batch).

    Secuencial a propósito: NIM tiene límites de rate y una ráfaga de 20
    peticiones en paralelo se convierte en 503 masivos. Cada imagen se procesa
    con la misma lógica de ``/digitize`` y un fallo aislado no invalida el lote.

    Nota de latencia: cada imagen tarda ~30-60s contra NIM vision, así que un
    lote de 20 tarda ~10-20 minutos. El cliente debe usar un timeout acorde.
    """
    runtime = _runtime(request)
    service = get_digitize_service(runtime.settings)

    images = digitize_request.images
    results: list[DigitizeBatchItemResult] = []
    succeeded = 0

    for index, image_request in enumerate(images):
        try:
            single = await run_in_threadpool(service.digitize, image_request)
        except Exception as exc:
            # Un fallo puntual se reporta por ítem: el resto del lote continúa.
            results.append(
                DigitizeBatchItemResult(
                    index=index,
                    filename=image_request.filename,
                    status="error",
                    error=_digitize_error_detail(exc),
                )
            )
        else:
            succeeded += 1
            results.append(
                DigitizeBatchItemResult(
                    index=index,
                    filename=image_request.filename,
                    status="ok",
                    result=single,
                )
            )

        if index < len(images) - 1:
            await asyncio.sleep(DIGITIZE_BATCH_DELAY_SECONDS)

    return DigitizeBatchResponse(
        total=len(images),
        succeeded=succeeded,
        failed=len(images) - succeeded,
        results=results,
    )


__all__ = ["router"]