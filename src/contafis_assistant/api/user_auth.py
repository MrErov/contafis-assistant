"""Dependencia de autenticación por token de usuario (T01).

Extrae ``X-User-Token``, construye un ``OdooClient`` por request con ese token,
verifica el alcance en Odoo y devuelve un ``UserContext`` con cliente, snapshot
y compañías. El cliente se cierra al terminar el request (``yield`` + ``finally``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated, Generator

from fastapi import Depends, Header, HTTPException, Request
from pydantic import SecretStr

from contafis_assistant.config import Settings
from contafis_assistant.odoo.allowlist import RpcCall
from contafis_assistant.odoo.client import OdooClient
from contafis_assistant.odoo.errors import OdooAuthError, OdooTransientError
from contafis_assistant.odoo.scope import ScopeReason, ScopeService, ScopeSnapshot


USER_TOKEN_HEADER = "X-User-Token"

MISSING_USER_TOKEN_DETAIL = "Falta el token de usuario (X-User-Token)."
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


def _user_key_for(token: str) -> str:
    """Deriva una clave determinista (SHA-256 hex) a partir del token.

    El token crudo nunca se persiste en caches ni logs.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_user_context(
    request: Request,
    x_user_token: Annotated[str | None, Header(alias=USER_TOKEN_HEADER)] = None,
) -> Generator[UserContext, None, None]:
    """Dependencia FastAPI que autentica al usuario por token personal.

    Orden de compuertas:
    1. Middleware X-API-Key (ya ejecutado antes de llegar aquí).
    2. Header X-User-Token ausente/vacío → 401.
    3. Construye OdooClient con el token del usuario.
    4. Verifica alcance (ScopeService).
       - Si el snapshot tiene reason=odoo_error, hace un context_get rápido
         para distinguir auth (401) de transitorio (503).
    5. Yield UserContext; finally cierra el cliente.
    """
    # 1. Validar header
    if not x_user_token or not x_user_token.strip():
        raise HTTPException(status_code=401, detail=MISSING_USER_TOKEN_DETAIL)

    token = x_user_token.strip()
    user_key = _user_key_for(token)

    # 2. Obtener settings y runtime del app state
    settings: Settings = request.app.state.settings
    runtime = request.app.state.runtime

    # 3. Construir cliente Odoo por request con el token del usuario
    transport = getattr(runtime, "user_transport", None)

    client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key=token,
        timeout=settings.odoo_timeout_seconds,
        transport=transport,
    )

    # 4. Verificar alcance con ScopeService sobre el cliente del usuario
    scope_service = ScopeService(
        client,
        app_env=settings.app_env,
        attested=settings.odoo_readonly_attested,
        ttl_seconds=settings.scope_check_ttl_seconds,
    )

    snapshot = scope_service.refresh(force=True)

    # Si el scope check falló por error de Odoo, distinguir auth vs transitorio
    if snapshot.reason is ScopeReason.odoo_error:
        try:
            # Llamada rápida para ver el tipo de error real
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

    # 5. Obtener compañías visibles (nombres)
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

    try:
        yield UserContext(
            token=token,
            user_key=user_key,
            client=client,
            snapshot=snapshot,
            companies=companies,
            scope_service=scope_service,
        )
    finally:
        client.close()