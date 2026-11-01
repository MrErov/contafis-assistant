"""Dependencia de autenticación por sesión de Odoo (Spec 005).

Soporta dos modos:
1. **Session** (nuevo): header ``X-Odoo-Session`` con ``session_id`` de Odoo web.
   Se valida contra ``/web/session/get_session_info`` y se usa cookie para RPC.
2. **Token** (legacy): header ``X-User-Token`` con API key personal de Odoo.
   Comportamiento original preservado para compatibilidad.

Prioridad: ``X-Odoo-Session`` > ``X-User-Token``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Mapping
from typing import Annotated, Generator, Literal

import httpx
from fastapi import Depends, Header, HTTPException, Request
from pydantic import SecretStr

from contafis_assistant.config import Settings
from contafis_assistant.domain.time import Clock
from contafis_assistant.odoo.allowlist import RpcCall
from contafis_assistant.odoo.client import OdooClient
from contafis_assistant.odoo.errors import OdooAuthError, OdooTransientError
from contafis_assistant.odoo.scope import ScopeReason, ScopeService, ScopeSnapshot


SESSION_HEADER = "X-Odoo-Session"
USER_TOKEN_HEADER = "X-User-Token"

MISSING_AUTH_DETAIL = "Falta autenticación (X-Odoo-Session o X-User-Token)."
INVALID_SESSION_DETAIL = "Sesión de Odoo expirada o inválida."
EXPIRED_SESSION_DETAIL = "Sesión de Odoo expirada (24h)."
INVALID_USER_TOKEN_DETAIL = "El token de Odoo no es válido o fue revocado."
SERVICE_UNAVAILABLE_DETAIL = "service_unavailable"


@dataclass
class UserContext:
    """Contexto de usuario autenticado para el turno actual."""

    token: str
    user_key: str
    client: OdooClient
    snapshot: ScopeSnapshot
    companies: list
    scope_service: ScopeService
    auth_mode: Literal["session", "token"]
    session_uid: int | None
    session_company_id: int | None


def _user_key_for(token: str) -> str:
    """Deriva una clave determinista (SHA-256 hex) a partir del token.

    El token crudo nunca se persiste en caches ni logs.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_odoo_datetime(dt_str: str) -> datetime:
    """Parsea datetime en formato Odoo: 'YYYY-MM-DD HH:MM:SS' (UTC)."""
    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _is_session_expired(last_activity_str: str, max_age_hours: int, clock: Clock) -> bool:
    """Verifica si la sesión ha expirado según last_activity y max_age_hours."""
    try:
        last_activity = _parse_odoo_datetime(last_activity_str)
    except (ValueError, TypeError):
        # Formato inválido o ausente → tratar como expirada (seguridad)
        return True
    age = clock.now() - last_activity
    return age > timedelta(hours=max_age_hours)


def _as_company_id(value: object) -> int | None:
    """Convierte un id de compañía (int, string numérico o dict con ``id``)."""
    if isinstance(value, Mapping):
        value = value.get("id")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _company_id_list(values: object) -> list[int]:
    """Normaliza una colección heterogénea de compañías a una lista de ids.

    Acepta ``int``/``str``, listas de ids, listas de dicts con ``id`` y dicts
    (tanto indexados por id como dicts de compañía).
    """
    if values is None:
        return []

    if isinstance(values, Mapping):
        # Caso Odoo: {"1": {...}, "2": {...}} (claves = id) o dicts donde el id
        # vive en el valor.
        result = _company_id_list(list(values.keys()))
        if result:
            return result
        return _company_id_list(list(values.values()))

    if not isinstance(values, (list, tuple, set)):
        values = [values]

    result: list[int] = []
    for item in values:
        if isinstance(item, Mapping):
            item = item.get("id")
        company_id = _as_company_id(item)
        if company_id is not None and company_id not in result:
            result.append(company_id)
    return result


def _extract_companies(result: Mapping[str, object]) -> tuple[int | None, list[int]]:
    """Extrae ``(company_id, allowed_company_ids)`` del payload de sesión.

    Odoo 19 devuelve ``user_companies`` como dict
    (``current_company`` + ``allowed_companies``) o como lista de ids. Si no
    viene, se mantiene el fallback legacy a ``user_context``.
    """
    company_id: int | None = None
    allowed_company_ids: list[int] = []

    user_companies = result.get("user_companies")
    if isinstance(user_companies, Mapping):
        company_id = _as_company_id(user_companies.get("current_company"))
        allowed_company_ids = _company_id_list(
            user_companies.get("allowed_companies")
        )
    elif user_companies is not None:
        allowed_company_ids = _company_id_list(user_companies)

    user_context = result.get("user_context")
    if isinstance(user_context, Mapping):
        if company_id is None:
            company_id = _as_company_id(user_context.get("company_id"))
        if not allowed_company_ids:
            allowed_company_ids = _company_id_list(
                user_context.get("allowed_company_ids")
            )

    if company_id is None and allowed_company_ids:
        company_id = allowed_company_ids[0]
    if company_id is not None and company_id not in allowed_company_ids:
        allowed_company_ids.insert(0, company_id)

    return company_id, allowed_company_ids


def _validate_session(
    session_id: str,
    settings: Settings,
    clock: Clock,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Valida session_id contra /web/session/get_session_info de Odoo.

    Returns:
        dict con: uid, company_id, allowed_company_ids, session_id

    Raises:
        OdooAuthError: sesión inválida, uid nulo, o expirada (>24h)
        OdooTransientError: error transitorio de red/Odoo (5xx, timeout)
    """
    url = f"{settings.odoo_url}/web/session/get_session_info"
    body = {"jsonrpc": "2.0", "method": "call", "params": {}}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Odoo-Database": settings.odoo_db,
        "Cookie": f"session_id={session_id}",
    }

    if transport is None:
        transport = httpx.HTTPTransport()

    try:
        response = transport.handle_request(
            httpx.Request("POST", url, headers=headers, json=body)
        )
        # ``HTTPTransport.handle_request`` devuelve la respuesta con el stream
        # sin consumir; leerla evita ``httpx.ResponseNotRead`` al parsear JSON.
        response.read()
    except httpx.TimeoutException as exc:
        raise OdooTransientError("timeout al validar sesión en Odoo") from exc
    except httpx.TransportError as exc:
        raise OdooTransientError("error de transporte al validar sesión en Odoo") from exc
    except httpx.HTTPError as exc:
        raise OdooTransientError(
            "error al leer la respuesta de sesión de Odoo"
        ) from exc

    status = response.status_code
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if status in {401, 403}:
        raise OdooAuthError(
            f"Odoo rechazó la sesión (HTTP {status})", status=status
        )

    if status >= 500:
        raise OdooTransientError(
            f"Odoo no disponible al validar sesión (HTTP {status})", status=status
        )

    if payload is None:
        raise OdooTransientError("Respuesta no JSON al validar sesión", status=status)

    # Estructura esperada: {"jsonrpc": "2.0", "id": 1, "result": {...}}
    result = payload.get("result")
    if not isinstance(result, dict):
        raise OdooAuthError("Respuesta de get_session_info inválida")

    uid = result.get("uid")
    if uid is None:
        raise OdooAuthError("Sesión sin uid (inválida o expirada)")

    # Odoo 19 entrega ``user_companies``; versiones previas usan
    # ``company_id``/``allowed_company_ids`` dentro de ``user_context``.
    company_id, allowed_company_ids = _extract_companies(result)

    # ``last_activity`` es opcional: Odoo 19 ya no lo devuelve. Si viene, se
    # sigue validando la expiración de 24h por seguridad.
    last_activity = result.get("last_activity")
    if last_activity is not None and _is_session_expired(
        last_activity, settings.session_max_age_hours, clock
    ):
        raise OdooAuthError(f"Sesión expirada ({settings.session_max_age_hours}h)")

    return {
        "uid": uid,
        "company_id": company_id,
        "allowed_company_ids": allowed_company_ids,
        "session_id": session_id,
    }


def _build_user_context_from_session(
    request: Request,
    session_id: str,
    session_info: dict,
    transport,
) -> UserContext:
    """Construye UserContext a partir de sesión validada."""
    settings: Settings = request.app.state.settings
    runtime = request.app.state.runtime

    uid = session_info["uid"]
    company_id = session_info["company_id"]
    allowed_company_ids = session_info["allowed_company_ids"]

    # Cliente Odoo con cookie de sesión (sin api_key)
    client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key=None,
        timeout=settings.odoo_timeout_seconds,
        transport=transport,
        allowed_company_ids=allowed_company_ids,
        uid=uid,
        session_id=session_id,
    )

    # ScopeService con el cliente de la sesión
    scope_service = ScopeService(
        client,
        app_env=settings.app_env,
        attested=settings.odoo_readonly_attested,
        ttl_seconds=settings.scope_check_ttl_seconds,
        clock=runtime.clock,
    )

    # Forzar refresh para que use el uid y allowed_company_ids ya seteados
    snapshot = scope_service.refresh(force=True)

    # Obtener compañías visibles (nombres)
    companies = []
    try:
        companies_raw = client.execute(
            RpcCall(
                model="res.company",
                method="search_read",
                domain=[("active", "=", True)],
                fields=["id", "name"],
            )
        )
        companies = [
            {"id": row["id"], "name": row["name"]}
            for row in (companies_raw or [])
            if isinstance(row, dict) and row.get("id") is not None
        ]
    except Exception:
        companies = []

    return UserContext(
        token=session_id,
        user_key=_user_key_for(session_id),
        client=client,
        snapshot=snapshot,
        companies=companies,
        scope_service=scope_service,
        auth_mode="session",
        session_uid=uid,
        session_company_id=company_id,
    )


def _legacy_user_context(
    request: Request,
    x_user_token: str,
    transport,
) -> UserContext:
    """Flujo legacy: autenticación por X-User-Token (API key personal)."""
    settings: Settings = request.app.state.settings
    runtime = request.app.state.runtime

    token = x_user_token.strip()
    user_key = _user_key_for(token)

    client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key=token,
        timeout=settings.odoo_timeout_seconds,
        transport=transport,
    )

    scope_service = ScopeService(
        client,
        app_env=settings.app_env,
        attested=settings.odoo_readonly_attested,
        ttl_seconds=settings.scope_check_ttl_seconds,
        clock=runtime.clock,
    )

    snapshot = scope_service.refresh(force=True)

    if snapshot.reason is ScopeReason.odoo_error:
        try:
            client.execute(RpcCall(model="res.users", method="context_get"))
        except OdooAuthError:
            client.close()
            raise HTTPException(status_code=401, detail=INVALID_USER_TOKEN_DETAIL) from None
        except OdooTransientError:
            client.close()
            raise HTTPException(status_code=503, detail=SERVICE_UNAVAILABLE_DETAIL) from None
        except Exception:
            client.close()
            raise HTTPException(status_code=503, detail=SERVICE_UNAVAILABLE_DETAIL) from None

    companies = []
    try:
        companies_raw = client.execute(
            RpcCall(
                model="res.company",
                method="search_read",
                domain=[("active", "=", True)],
                fields=["id", "name"],
            )
        )
        companies = [
            {"id": row["id"], "name": row["name"]}
            for row in (companies_raw or [])
            if isinstance(row, dict) and row.get("id") is not None
        ]
    except Exception:
        companies = []

    return UserContext(
        token=token,
        user_key=user_key,
        client=client,
        snapshot=snapshot,
        companies=companies,
        scope_service=scope_service,
        auth_mode="token",
        session_uid=None,
        session_company_id=None,
    )


def get_user_context_from_session(
    request: Request,
    x_odoo_session: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
    x_user_token: Annotated[str | None, Header(alias=USER_TOKEN_HEADER)] = None,
) -> Generator[UserContext, None, None]:
    """Dependencia FastAPI que autentica al usuario por sesión de Odoo (preferido) o token legacy.

    Orden de compuertas:
    1. Middleware X-API-Key (ya ejecutado antes de llegar aquí).
    2. Header X-Odoo-Session presente → validar con get_session_info.
    3. Header X-Odoo-Session ausente pero X-User-Token presente → flujo legacy.
    4. Ninguno → 401.
    """
    # 1. Prioridad: X-Odoo-Session
    if x_odoo_session and x_odoo_session.strip():
        session_id = x_odoo_session.strip()
        settings: Settings = request.app.state.settings
        runtime = request.app.state.runtime
        transport = getattr(runtime, "user_transport", None)

        try:
            session_info = _validate_session(
                session_id=session_id,
                settings=settings,
                clock=runtime.clock,
                transport=transport,
            )
        except OdooAuthError:
            raise HTTPException(status_code=401, detail=INVALID_SESSION_DETAIL) from None
        except OdooTransientError:
            raise HTTPException(status_code=503, detail=SERVICE_UNAVAILABLE_DETAIL) from None

        user_ctx = _build_user_context_from_session(request, session_id, session_info, transport)
        try:
            yield user_ctx
        finally:
            user_ctx.client.close()
        return

    # 2. Fallback: X-User-Token (legacy)
    if x_user_token and x_user_token.strip():
        runtime = request.app.state.runtime
        transport = getattr(runtime, "user_transport", None)
        user_ctx = _legacy_user_context(request, x_user_token, transport)
        try:
            yield user_ctx
        finally:
            user_ctx.client.close()
        return

    # 3. Sin autenticación
    raise HTTPException(status_code=401, detail=MISSING_AUTH_DETAIL)


# Mantener compatibilidad: get_user_context como alias legacy (deprecado)
def get_user_context(
    request: Request,
    x_user_token: Annotated[str | None, Header(alias=USER_TOKEN_HEADER)] = None,
) -> Generator[UserContext, None, None]:
    """Dependencia legacy (deprecated). Usar get_user_context_from_session."""
    runtime = request.app.state.runtime
    transport = getattr(runtime, "user_transport", None)
    if not x_user_token or not x_user_token.strip():
        raise HTTPException(status_code=401, detail="Falta el token de usuario (X-User-Token).")
    user_ctx = _legacy_user_context(request, x_user_token, transport)
    try:
        yield user_ctx
    finally:
        user_ctx.client.close()


__all__ = [
    "SESSION_HEADER",
    "USER_TOKEN_HEADER",
    "UserContext",
    "get_user_context_from_session",
    "get_user_context",  # legacy
    "_validate_session",
    "_legacy_user_context",
]