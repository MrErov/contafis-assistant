"""Tests unitarios del token efímero firmado (P0-04).

Cubren: formato canónico, round-trip, firma inválida, expiración, vigencia
excesiva, claims inválidos, secreto ausente y estabilidad de ``user_key``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from contafis_assistant.api.signed_token import (
    TOKEN_MAX_FUTURE_SECONDS,
    TOKEN_TTL_SECONDS,
    SignedTokenClaims,
    SignedTokenError,
    sign_token,
    verify_token,
)
from contafis_assistant.api.user_auth import (
    _user_key_for,
    _user_key_for_claims,
)
from contafis_assistant.config import Settings
from contafis_assistant.odoo.client import OdooClient


SECRET = "shared-test-secret"


class MockTransport:
    """Transporte mock para capturar requests y devolver respuestas programadas."""

    def __init__(self, responses: list[httpx.Response] | None = None, handler=None):
        self.responses = responses or []
        self.handler = handler
        self.requests = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.handler:
            return self.handler(request, len(self.requests))
        if self.responses:
            return self.responses.pop(0)
        return httpx.Response(200, json={})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        return self.handle_request(request)

    def close(self) -> None:
        pass


def make_settings(**env: str) -> Settings:
    """Crea Settings con valores por defecto para tests."""
    defaults = {
        "ODOO_URL": "http://127.0.0.1:8069",
        "ODOO_DB": "contea",
        "ODOO_API_KEY": "test-api-key",
        "OPENAI_API_KEY": "test-openai-key",
        "LLM_MODEL": "gpt-4o-mini",
        "EMBEDDING_MODEL": "text-embedding-3-small",
        "CHROMA_PATH": "/tmp/chroma",
    }
    defaults.update(env)
    return Settings(**defaults)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode_payload(token: str) -> dict:
    payload_b64, _signature = token.split(".")
    padding = "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8"))


def _forge_token(secret: str, payload: dict) -> str:
    """Firma un payload arbitrario: sirve para probar claims inválidos."""
    payload_b64 = _b64url(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(
        secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{payload_b64}.{_b64url(signature)}"


# ============================================================================
# Formato y round-trip
# ============================================================================


def test_sign_and_verify_round_trip():
    """El token firmado se verifica y devuelve exactamente los claims emitidos."""
    token = sign_token(SECRET, uid=7, company_id=3)

    claims = verify_token(token, SECRET)

    assert isinstance(claims, SignedTokenClaims)
    assert claims.uid == 7
    assert claims.company_id == 3
    assert claims.exp > 0


def test_token_has_two_urlsafe_parts_without_padding():
    """Formato acordado con Odoo: ``base64url(payload).base64url(firma)``."""
    token = sign_token(SECRET, uid=7, company_id=3)

    parts = token.split(".")
    assert len(parts) == 2
    assert all(part and "=" not in part for part in parts)
    assert all("+" not in part and "/" not in part for part in parts)


def test_payload_is_canonical_and_minimal():
    """El payload solo lleva uid/company_id/exp con claves ordenadas y sin espacios."""
    token = sign_token(SECRET, uid=7, company_id=3, now=1_700_000_000)

    payload_b64 = token.split(".")[0]
    padding = "=" * (-len(payload_b64) % 4)
    raw = base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")

    assert raw == '{"company_id":3,"exp":1700000060,"uid":7}'
    assert set(_decode_payload(token)) == {"uid", "company_id", "exp"}


def test_sign_honors_custom_now_and_ttl():
    """``now`` y ``ttl_seconds`` permiten tokens deterministas en tests."""
    token = sign_token(SECRET, uid=1, company_id=2, ttl_seconds=15, now=1000)

    assert _decode_payload(token)["exp"] == 1015


# ============================================================================
# Firma y manipulación
# ============================================================================


def test_verify_rejects_wrong_secret():
    """Un secreto distinto no valida la firma (el asistente y Odoo deben coincidir)."""
    token = sign_token(SECRET, uid=7, company_id=3)

    with pytest.raises(SignedTokenError) as exc_info:
        verify_token(token, "otro-secreto")

    assert "firma" in str(exc_info.value).lower()


def test_verify_rejects_tampered_payload():
    """Cambiar el payload invalida la firma aunque el formato siga siendo válido."""
    token = sign_token(SECRET, uid=7, company_id=3)
    _payload_b64, signature_b64 = token.split(".")
    forged_payload = _b64url(b'{"company_id":3,"exp":9999999999,"uid":1}')
    tampered = f"{forged_payload}.{signature_b64}"

    with pytest.raises(SignedTokenError) as exc_info:
        verify_token(tampered, SECRET)

    assert "firma" in str(exc_info.value).lower()


@pytest.mark.parametrize("token", ["", "   ", "abc", "a.b.c", ".firma", "payload."])
def test_verify_rejects_malformed_tokens(token):
    """Tokens vacíos o con estructura distinta de ``x.y`` se rechazan."""
    with pytest.raises(SignedTokenError):
        verify_token(token, SECRET)


def test_verify_rejects_invalid_base64_without_crashing():
    """Una firma con base64 inválido se rechaza sin lanzar un error crudo."""
    with pytest.raises(SignedTokenError) as exc_info:
        verify_token("payload.====", SECRET)

    message = str(exc_info.value).lower()
    assert "firma" in message or "formato" in message


def test_verify_rejects_non_object_payload():
    """Un payload JSON que no es objeto se rechaza."""
    token = _forge_token(SECRET, [1, 2, 3])  # type: ignore[arg-type]

    with pytest.raises(SignedTokenError) as exc_info:
        verify_token(token, SECRET)

    assert "payload" in str(exc_info.value).lower()


# ============================================================================
# Expiración y vigencia
# ============================================================================


def test_verify_rejects_expired_token():
    """Un token emitido hace más de su TTL se rechaza (ventana de replay acotada)."""
    token = sign_token(SECRET, uid=7, company_id=3, now=1_000_000)

    with pytest.raises(SignedTokenError) as exc_info:
        verify_token(token, SECRET, now=1_000_000 + TOKEN_TTL_SECONDS + 1)

    assert "expir" in str(exc_info.value).lower()


def test_verify_accepts_token_at_the_expiry_boundary():
    """``exp`` igual al instante actual sigue siendo válido (no expiró todavía)."""
    token = sign_token(SECRET, uid=7, company_id=3, now=1_000_000)

    claims = verify_token(token, SECRET, now=1_000_000 + TOKEN_TTL_SECONDS)

    assert claims.uid == 7


def test_verify_accepts_small_clock_skew():
    """La diferencia de reloj dentro del margen acordado no invalida el token."""
    now = 1_000_000.0
    token = _forge_token(
        SECRET,
        {"uid": 7, "company_id": 3, "exp": int(now) + TOKEN_MAX_FUTURE_SECONDS - 1},
    )

    claims = verify_token(token, SECRET, now=now)

    assert claims.exp == int(now) + TOKEN_MAX_FUTURE_SECONDS - 1


def test_verify_rejects_token_with_excessive_validity():
    """Un token con vigencia mayor a la permitida se rechaza (posible TTL manipulado)."""
    now = 1_000_000.0
    token = _forge_token(
        SECRET,
        {"uid": 7, "company_id": 3, "exp": int(now) + TOKEN_MAX_FUTURE_SECONDS + 60},
    )

    with pytest.raises(SignedTokenError) as exc_info:
        verify_token(token, SECRET, now=now)

    assert "vigencia" in str(exc_info.value).lower()


def test_sign_rejects_invalid_ttl():
    """Solo se emiten tokens de 1..60 s (el contrato con Odoo)."""
    for ttl in (0, -1, TOKEN_TTL_SECONDS + 1):
        with pytest.raises(SignedTokenError):
            sign_token(SECRET, uid=7, company_id=3, ttl_seconds=ttl)


# ============================================================================
# Claims y secreto
# ============================================================================


@pytest.mark.parametrize("field", ["uid", "company_id", "exp"])
def test_verify_rejects_negative_or_zero_claims(field):
    """uid/company_id/exp deben ser enteros positivos."""
    payload = {"uid": 7, "company_id": 3, "exp": 9_999_999_999}
    payload[field] = 0

    with pytest.raises(SignedTokenError) as exc_info:
        verify_token(_forge_token(SECRET, payload), SECRET)

    assert field in str(exc_info.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"company_id": 3, "exp": 9_999_999_999},  # falta uid
        {"uid": 7, "exp": 9_999_999_999},  # falta company_id
        {"uid": 7, "company_id": 3},  # falta exp
        {"uid": "7", "company_id": 3, "exp": 9_999_999_999},  # str
        {"uid": True, "company_id": 3, "exp": 9_999_999_999},  # bool
        {"uid": 7.5, "company_id": 3, "exp": 9_999_999_999},  # float
    ],
)
def test_verify_rejects_invalid_claim_types(payload):
    """Claims ausentes, strings, bools o floats se rechazan."""
    with pytest.raises(SignedTokenError):
        verify_token(_forge_token(SECRET, payload), SECRET)


@pytest.mark.parametrize("uid", [0, -1])
def test_sign_rejects_non_positive_ids(uid):
    """No se emiten tokens con uid/company_id inválidos."""
    with pytest.raises(SignedTokenError):
        sign_token(SECRET, uid=uid, company_id=3)


@pytest.mark.parametrize("secret", [None, "", "   "])
def test_verify_rejects_missing_secret(secret):
    """Fail-closed: sin secreto compartido no se valida ningún token."""
    token = sign_token(SECRET, uid=7, company_id=3)

    with pytest.raises(SignedTokenError) as exc_info:
        verify_token(token, secret)

    assert "secreto" in str(exc_info.value).lower()


def test_sign_rejects_missing_secret():
    """Emitir sin secreto también es un error explícito."""
    with pytest.raises(SignedTokenError):
        sign_token("", uid=7, company_id=3)


# ============================================================================
# user_key: aislamiento de hilos sin depender del token efímero
# ============================================================================


def test_user_key_is_stable_for_same_uid_and_company():
    """Dos tokens distintos del mismo usuario/compañía comparten ``user_key``."""
    first = _user_key_for_claims(7, 3)
    second = _user_key_for_claims(7, 3)

    assert first == second
    assert len(first) == 64
    assert all(char in "0123456789abcdef" for char in first)


def test_user_key_changes_with_company():
    """El aislamiento separa al mismo usuario en compañías distintas."""
    assert _user_key_for_claims(7, 3) != _user_key_for_claims(7, 4)


def test_user_key_never_exposes_the_token():
    """La clave de hilo se deriva de uid+compañía, no del token efímero."""
    token = sign_token(SECRET, uid=7, company_id=3)

    derived = _user_key_for_claims(7, 3)

    assert token not in derived
    assert derived != _user_key_for(token)


# ============================================================================
# OdooClient: la capacidad de sesión se conserva, pero no se usa para autenticar
# ============================================================================


def test_odoo_client_headers_with_bearer():
    """El cliente de servicio sigue autenticando con la API key de servicio."""
    settings = make_settings()
    client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key="service-key",
        timeout=settings.odoo_timeout_seconds,
        transport=MockTransport(),
    )

    headers = client._headers()

    assert headers["Authorization"] == "Bearer service-key"
    assert "Cookie" not in headers
    assert headers["X-Odoo-Database"] == settings.odoo_db


def test_odoo_client_headers_with_session_are_still_supported():
    """``session_id`` sigue siendo una capacidad del cliente, no del flujo P0-04."""
    settings = make_settings()
    client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key="",
        timeout=settings.odoo_timeout_seconds,
        transport=MockTransport(),
        session_id="legacy-session",
    )

    headers = client._headers()

    assert headers["Cookie"] == "session_id=legacy-session"
    assert "Authorization" not in headers


def test_user_context_signed_mode_fields():
    """``UserContext`` expone el modo firmado y los claims del token."""
    from contafis_assistant.api.user_auth import UserContext
    from contafis_assistant.domain.enums import ScopeState
    from contafis_assistant.domain.time import Clock
    from contafis_assistant.odoo.scope import ScopeReason, ScopeService, ScopeSnapshot

    settings = make_settings()
    client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key="test",
        timeout=settings.odoo_timeout_seconds,
    )
    snapshot = ScopeSnapshot(
        state=ScopeState.scope_ok,
        reason=ScopeReason.ok,
        uid=7,
        user_company_ids=[3],
        active_company_ids=[3],
        is_admin=False,
        is_internal=True,
        attested=True,
        app_env="test",
        checked_at=Clock().now(),
    )
    scope_service = ScopeService(client, app_env="test", attested=True)

    ctx = UserContext(
        token=sign_token(SECRET, uid=7, company_id=3),
        user_key="abc123",
        client=client,
        snapshot=snapshot,
        companies=[{"id": 3, "name": "Test"}],
        scope_service=scope_service,
        auth_mode="signed",
        signed_uid=7,
        signed_company_id=3,
    )

    assert ctx.auth_mode == "signed"
    assert ctx.signed_uid == 7
    assert ctx.signed_company_id == 3


def test_user_context_legacy_mode_fields():
    """El modo legacy conserva ``auth_mode='token'`` y claims firmados vacíos."""
    from contafis_assistant.api.user_auth import UserContext
    from contafis_assistant.domain.enums import ScopeState
    from contafis_assistant.domain.time import Clock
    from contafis_assistant.odoo.scope import ScopeReason, ScopeService, ScopeSnapshot

    settings = make_settings()
    client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key="test",
        timeout=settings.odoo_timeout_seconds,
    )
    snapshot = ScopeSnapshot(
        state=ScopeState.scope_ok,
        reason=ScopeReason.ok,
        uid=7,
        user_company_ids=[1],
        active_company_ids=[1],
        is_admin=False,
        is_internal=True,
        attested=True,
        app_env="test",
        checked_at=Clock().now(),
    )
    scope_service = ScopeService(client, app_env="test", attested=True)

    ctx = UserContext(
        token="legacy-token",
        user_key="abc123",
        client=client,
        snapshot=snapshot,
        companies=[{"id": 1, "name": "Test"}],
        scope_service=scope_service,
        auth_mode="token",
        signed_uid=None,
        signed_company_id=None,
    )

    assert ctx.auth_mode == "token"
    assert ctx.signed_uid is None
    assert ctx.signed_company_id is None
