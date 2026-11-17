"""Autenticación de usuario por token efímero firmado (P0-04).

Modos soportados:

1. **Firmado** (preferido): header ``X-ContaFis-Token`` con el token HMAC que
   emite Odoo con ``l10n_ve_chat.auth_token`` / ``API_SHARED_SECRET``. El token
   solo aporta ``uid`` y ``company_id``; Odoo se consulta server-to-server con
   la API key de servicio y el alcance se acota a la compañía del token.
2. **Token legacy**: header ``X-User-Token`` con una API key personal de Odoo.

El ``sid`` de la sesión web de Odoo ya no se acepta (P0-04).
Prioridad: ``X-ContaFis-Token`` > ``X-User-Token``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Generator, Literal

from fastapi import Depends, Header, HTTPException, Request
from pydantic import SecretStr

from contafis_assistant.api.signed_token import (
    TOKEN_HEADER,
    SignedTokenClaims,
    SignedTokenError,
    verify_token,
)
from contafis_assistant.config import Settings
from contafis_assistant.odoo.allowlist import RpcCall
from contafis_assistant.odoo.client import OdooClient
from contafis_assistant.odoo.errors import OdooAuthError, OdooTransientError
from contafis_assistant.odoo.scope import ScopeReason, ScopeService, ScopeSnapshot


USER_TOKEN_HEADER = "X-User-Token"

MISSING_AUTH_DETAIL = "Falta autenticación (X-ContaFis-Token o X-User-Token)."
INVALID_TOKEN_DETAIL = "El token firmado no es válido o expiró."
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
    auth_mode: Literal["signed", "token"]
    signed_uid: int | None
    signed_company_id: int | None


def _secret_text(value: SecretStr | str | None) -> str | None:
    """Normaliza un secreto de Pydantic a texto no vacío."""
    if value is None:
        return None
    if isinstance(value, SecretStr):
        text = value.get_secret_value()
    else:
        text = str(value)
    return text if text.strip() else None


def _user_key_for(token: str) -> str:
    """Deriva una clave determinista (SHA-256 hex) a partir del token legacy.

    El token crudo nunca se persiste en caches ni logs.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user_key_for_claims(uid: int, company_id: int) -> str:
    """Clave estable por usuario+compañía (el token efímero cambia cada minuto)."""
    material = f"signed-uid:{uid}:company:{company_id}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _build_user_context_from_claims(
    request: Request,
    token: str,
    claims: SignedTokenClaims,
    transport,
) -> UserContext:
    """Construye el UserContext usando el uid/compañía del token firmado."""
    settings: Settings = request.app.state.settings
    runtime = request.app.state.runtime

    api_key = _secret_text(settings.odoo_api_key)
    if not api_key:
        # Sin API key de servicio no se puede consultar Odoo server-to-server.
        raise HTTPException(status_code=503, detail=SERVICE_UNAVAILABLE_DETAIL)

    client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key=api_key,
        timeout=settings.odoo_timeout_seconds,
        transport=transport,
        allowed_company_ids=[claims.company_id],
        uid=claims.uid,
    )

    # La API key de servicio es la que debe estar atestada como read-only.
    scope_service = ScopeService(
        client,
        app_env=settings.app_env,
        attested=settings.odoo_readonly_attested,
        ttl_seconds=settings.scope_check_ttl_seconds,
        clock=runtime.clock,
    )

    # ``ScopeService`` prefiere el uid/compañía preseleccionados en el cliente,
    # porque ``context_get`` devuelve el contexto del usuario de servicio.
    snapshot = scope_service.refresh(force=True)

    if snapshot.reason is ScopeReason.odoo_error:
        client.close()
        raise HTTPException(status_code=503, detail=SERVICE_UNAVAILABLE_DETAIL)

    companies = []
    try:
        companies_raw = client.execute(
            RpcCall(
                model="res.company",
                method="search_read",
                domain=[
                    ("active", "=", True),
                    ("id", "=", claims.company_id),
                ],
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
        user_key=_user_key_for_claims(claims.uid, claims.company_id),
        client=client,
        snapshot=snapshot,
        companies=companies,
        scope_service=scope_service,
        auth_mode="signed",
        signed_uid=claims.uid,
        signed_company_id=claims.company_id,
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
        signed_uid=None,
        signed_company_id=None,
    )


def get_user_context_from_token(
    request: Request,
    x_contafis_token: Annotated[str | None, Header(alias=TOKEN_HEADER)] = None,
    x_user_token: Annotated[str | None, Header(alias=USER_TOKEN_HEADER)] = None,
) -> Generator[UserContext, None, None]:
    """Dependencia FastAPI que autentica por token firmado o token legacy.

    Orden de compuertas:
    1. Middleware X-API-Key (ya ejecutado antes de llegar aquí).
    2. Header X-ContaFis-Token presente → verificar HMAC y usar sus claims.
    3. Header X-ContaFis-Token ausente pero X-User-Token presente → legacy.
    4. Ninguno → 401.
    """
    if x_contafis_token and x_contafis_token.strip():
        settings: Settings = request.app.state.settings
        secret = _secret_text(settings.api_shared_secret)
        if secret is None:
            # Fail-closed: sin secreto no se puede validar ningún token.
            raise HTTPException(status_code=503, detail=SERVICE_UNAVAILABLE_DETAIL)

        try:
            claims = verify_token(x_contafis_token.strip(), secret)
        except SignedTokenError:
            raise HTTPException(status_code=401, detail=INVALID_TOKEN_DETAIL) from None

        runtime = request.app.state.runtime
        transport = getattr(runtime, "user_transport", None)
        user_ctx = _build_user_context_from_claims(
            request, x_contafis_token.strip(), claims, transport
        )
        try:
            yield user_ctx
        finally:
            user_ctx.client.close()
        return

    if x_user_token and x_user_token.strip():
        runtime = request.app.state.runtime
        transport = getattr(runtime, "user_transport", None)
        user_ctx = _legacy_user_context(request, x_user_token, transport)
        try:
            yield user_ctx
        finally:
            user_ctx.client.close()
        return

    raise HTTPException(status_code=401, detail=MISSING_AUTH_DETAIL)


# Mantener compatibilidad: get_user_context como alias legacy (deprecado)
def get_user_context(
    request: Request,
    x_user_token: Annotated[str | None, Header(alias=USER_TOKEN_HEADER)] = None,
) -> Generator[UserContext, None, None]:
    """Dependencia legacy (deprecated). Usar get_user_context_from_token."""
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
    "TOKEN_HEADER",
    "USER_TOKEN_HEADER",
    "UserContext",
    "get_user_context_from_token",
    "get_user_context",  # legacy
    "_legacy_user_context",
]
