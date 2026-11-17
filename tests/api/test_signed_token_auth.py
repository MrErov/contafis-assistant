"""Tests de API para autenticación por token efímero firmado (P0-04).

Cubren: token válido/expirado/alterado, secreto incorrecto o ausente, prioridad
sobre el legacy ``X-User-Token``, fallo de Odoo y endpoint ``/scope``.
"""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from contafis_assistant.api.signed_token import TOKEN_TTL_SECONDS, sign_token
from contafis_assistant.config import Settings
from contafis_assistant.main import create_app
from tests.api.conftest import make_runtime


CHAT_BODY = {"message": "obligaciones de la compañía", "thread_id": "thread_abc"}

SHARED_SECRET = "test-shared-secret"
API_KEY_HEADER = {"X-API-Key": SHARED_SECRET}


def auth_headers(
    *,
    uid: int = 7,
    company_id: int = 1,
    now: float | None = None,
    secret: str = SHARED_SECRET,
    include_api_key: bool = True,
) -> dict[str, str]:
    """Firma un token fresco por request (TTL de 60 s)."""
    headers = {
        "X-ContaFis-Token": sign_token(
            secret, uid=uid, company_id=company_id, now=now
        )
    }
    if include_api_key:
        headers.update(API_KEY_HEADER)
    return headers


class MockOdooTransport:
    """Transporte que simula Odoo JSON/2 y captura requests + close."""

    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []
        self.close_count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request, len(self.requests))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        return self.handle_request(request)

    def close(self) -> None:
        self.close_count += 1


def build_signed_transport(
    *,
    company_id: int = 1,
    company_name: str = "Empresa 1",
    fail_context: int | None = None,
):
    """Transporte JSON/2 para el flujo firmado: scope + compañía.

    El token solo aporta uid/company_id; todas las consultas a Odoo usan la API
    key de servicio (``Authorization: Bearer``), nunca una cookie de sesión.
    """

    def handler(request: httpx.Request, _attempt: int) -> httpx.Response:
        path = request.url.path
        if path == "/json/2/res.users/context_get":
            if fail_context is not None:
                return httpx.Response(fail_context, json={"error": "boom"})
            # Contexto del usuario de servicio: el cliente ya trae los claims.
            return httpx.Response(
                200,
                json={"uid": 99, "allowed_company_ids": [999]},
            )
        if path == "/json/2/res.users/has_group":
            body = json.loads(request.content or b"{}")
            return httpx.Response(
                200, json=body.get("group_ext_id") == "base.group_user"
            )
        if path == "/json/2/res.company/search_read":
            return httpx.Response(
                200, json=[{"id": company_id, "name": company_name}]
            )
        return httpx.Response(200, json=[])

    return MockOdooTransport(handler)


def make_client(settings: Settings, transport: MockOdooTransport) -> TestClient:
    runtime, *_ = make_runtime(settings, transport=transport)
    return TestClient(create_app(settings, runtime=runtime))


# ============================================================================
# Token firmado válido
# ============================================================================


def test_chat_with_valid_signed_token(make_settings):
    """Token válido → 200 y Odoo se consulta con Bearer de servicio, no cookie."""
    settings = make_settings(API_SHARED_SECRET=SHARED_SECRET)
    transport = build_signed_transport()
    client = make_client(settings, transport)

    with client:
        response = client.post("/chat", json=CHAT_BODY, headers=auth_headers())

    assert response.status_code == 200
    assert transport.requests, "debe consultarse Odoo para el scope"
    first = transport.requests[0]
    assert first.url.path == "/json/2/res.users/context_get"
    service_key = settings.odoo_api_key.get_secret_value()
    assert first.headers["Authorization"] == f"Bearer {service_key}"
    assert "Cookie" not in first.headers
    body = json.loads(first.content)
    assert body["context"]["allowed_company_ids"] == [1]


def test_signed_token_uid_drives_scope_checks(make_settings):
    """Las comprobaciones de grupo usan el uid del token, no el del servicio."""
    settings = make_settings(API_SHARED_SECRET=SHARED_SECRET)
    transport = build_signed_transport()
    client = make_client(settings, transport)

    with client:
        response = client.post("/chat", json=CHAT_BODY, headers=auth_headers(uid=7))

    assert response.status_code == 200
    has_group_calls = [
        json.loads(request.content)
        for request in transport.requests
        if request.url.path == "/json/2/res.users/has_group"
    ]
    assert has_group_calls, "el scope debe comprobar grupos del usuario"
    assert all(call["ids"] == [7] for call in has_group_calls)


# ============================================================================
# Token inválido
# ============================================================================


def test_chat_rejects_expired_token(make_settings):
    """Token emitido hace más del TTL → 401 sin consultar Odoo."""
    settings = make_settings(API_SHARED_SECRET=SHARED_SECRET)
    transport = build_signed_transport()
    client = make_client(settings, transport)

    expired = auth_headers(now=time.time() - TOKEN_TTL_SECONDS - 60)

    with client:
        response = client.post("/chat", json=CHAT_BODY, headers=expired)

    assert response.status_code == 401
    assert transport.requests == []


def test_chat_rejects_tampered_token(make_settings):
    """Payload alterado conservando la firma → 401."""
    settings = make_settings(API_SHARED_SECRET=SHARED_SECRET)
    transport = build_signed_transport()
    client = make_client(settings, transport)

    token = auth_headers()["X-ContaFis-Token"]
    _payload, signature = token.split(".")
    forged = base64.urlsafe_b64encode(
        b'{"company_id":1,"exp":9999999999,"uid":99}'
    ).rstrip(b"=").decode("ascii")
    headers = {**API_KEY_HEADER, "X-ContaFis-Token": f"{forged}.{signature}"}

    with client:
        response = client.post("/chat", json=CHAT_BODY, headers=headers)

    assert response.status_code == 401
    assert transport.requests == []


def test_chat_rejects_token_signed_with_other_secret(make_settings):
    """Un token firmado con otro secreto no se acepta (secreto compartido)."""
    settings = make_settings(API_SHARED_SECRET=SHARED_SECRET)
    transport = build_signed_transport()
    client = make_client(settings, transport)

    headers = auth_headers(secret="otro-secreto-distinto")

    with client:
        response = client.post("/chat", json=CHAT_BODY, headers=headers)

    assert response.status_code == 401
    assert transport.requests == []


def test_signed_token_requires_server_secret(make_settings):
    """Fail-closed: sin API_SHARED_SECRET no se puede validar ningún token → 503."""
    settings = make_settings()  # sin API_SHARED_SECRET (app_env=test)
    transport = build_signed_transport()
    client = make_client(settings, transport)

    token = sign_token(SHARED_SECRET, uid=7, company_id=1)
    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-ContaFis-Token": token},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "service_unavailable"
    assert transport.requests == []


def test_signed_token_requires_api_key_header(make_settings):
    """Configurado el secreto, el middleware exige X-API-Key antes del token."""
    settings = make_settings(API_SHARED_SECRET=SHARED_SECRET)
    transport = build_signed_transport()
    client = make_client(settings, transport)

    headers = auth_headers(include_api_key=False)

    with client:
        response = client.post("/chat", json=CHAT_BODY, headers=headers)

    assert response.status_code == 401
    assert transport.requests == []


def test_chat_returns_503_when_odoo_rejects_service_key(make_settings):
    """Si Odoo rechaza la API key de servicio, el turno responde 503, no 500."""
    settings = make_settings(API_SHARED_SECRET=SHARED_SECRET)
    transport = build_signed_transport(fail_context=401)
    client = make_client(settings, transport)

    with client:
        response = client.post("/chat", json=CHAT_BODY, headers=auth_headers())

    assert response.status_code == 503
    assert response.json()["detail"] == "service_unavailable"


# ============================================================================
# Compatibilidad legacy y prioridad
# ============================================================================


def test_chat_with_legacy_user_token_still_works(make_settings):
    """Sin token firmado, X-User-Token sigue funcionando con Bearer propio."""
    settings = make_settings()
    transport = build_signed_transport()
    client = make_client(settings, transport)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-User-Token": "legacy-user-token-456"},
        )

    assert response.status_code == 200
    assert transport.requests
    assert (
        transport.requests[0].headers["Authorization"]
        == "Bearer legacy-user-token-456"
    )


def test_signed_token_takes_priority_over_legacy_token(make_settings):
    """Con ambos headers, el token firmado gana y el legacy se ignora."""
    settings = make_settings(API_SHARED_SECRET=SHARED_SECRET)
    transport = build_signed_transport()
    client = make_client(settings, transport)

    headers = {
        **auth_headers(uid=7),
        "X-User-Token": "should-be-ignored",
    }

    with client:
        response = client.post("/chat", json=CHAT_BODY, headers=headers)

    assert response.status_code == 200
    service_key = settings.odoo_api_key.get_secret_value()
    assert transport.requests[0].headers["Authorization"] == f"Bearer {service_key}"
    assert all(
        "should-be-ignored" not in request.headers.get("Authorization", "")
        for request in transport.requests
    )


def test_chat_without_auth_returns_unified_detail(make_settings):
    """Sin token firmado ni legacy → 401 con detalle único."""
    settings = make_settings(API_SHARED_SECRET=SHARED_SECRET)
    transport = build_signed_transport()
    client = make_client(settings, transport)

    with client:
        response = client.post("/chat", json=CHAT_BODY, headers=API_KEY_HEADER)

    assert response.status_code == 401
    assert response.json()["detail"] == (
        "Falta autenticación (X-ContaFis-Token o X-User-Token)."
    )


# ============================================================================
# /scope y /health
# ============================================================================


def test_scope_endpoint_uses_signed_claims(make_settings):
    """GET /scope devuelve el uid y solo la compañía del token firmado."""
    settings = make_settings(API_SHARED_SECRET=SHARED_SECRET)
    transport = build_signed_transport(company_id=2, company_name="Empresa 2")
    client = make_client(settings, transport)

    with client:
        response = client.get("/scope", headers=auth_headers(uid=7, company_id=2))

    assert response.status_code == 200
    body = response.json()
    assert body["uid"] == 7
    assert body["companies"] == [{"id": 2, "name": "Empresa 2"}]


@pytest.mark.parametrize("path", ["/health"])
def test_health_endpoint_does_not_require_user_token(make_settings, path):
    """``/health`` no exige token de usuario (solo el middleware X-API-Key)."""
    settings = make_settings(API_SHARED_SECRET=SHARED_SECRET)
    transport = build_signed_transport()
    client = make_client(settings, transport)

    with client:
        response = client.get(path, headers=API_KEY_HEADER)

    assert response.status_code in (200, 503)
    assert transport.requests == []
