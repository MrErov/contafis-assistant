"""Tests unitarios para autenticación por session_id de Odoo (Spec 005).

Cubren: validación de sesión, expiración 24h, headers de OdooClient con/sin cookie.
"""

from __future__ import annotations

import httpx
import pytest
from datetime import datetime, timedelta, timezone

from contafis_assistant.api.user_auth import _validate_session
from contafis_assistant.odoo.client import OdooClient
from contafis_assistant.config import Settings
from contafis_assistant.domain.time import Clock
from contafis_assistant.odoo.errors import OdooAuthError, OdooTransientError


class FixedClock(Clock):
    """Reloj fijo para tests deterministas."""

    def __init__(self, fixed_now: datetime):
        self._fixed_now = fixed_now

    def now(self) -> datetime:
        return self._fixed_now

    def today_caracas(self):
        return self._fixed_now.date()


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
        "SESSION_MAX_AGE_HOURS": "24",
    }
    defaults.update(env)
    return Settings(**defaults)


def make_session_info_response(
    uid: int = 7,
    company_id: int = 1,
    allowed_company_ids: list[int] | None = None,
    last_activity: str | None = None,
    session_id: str = "test-session-abc",
) -> httpx.Response:
    """Construye respuesta exitosa de /web/session/get_session_info."""
    if allowed_company_ids is None:
        allowed_company_ids = [1, 2]
    if last_activity is None:
        last_activity = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    return httpx.Response(200, json={
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "uid": uid,
            "is_system": False,
            "is_admin": False,
            "user_context": {
                "lang": "es_VE",
                "tz": "America/Caracas",
                "uid": uid,
                "allowed_company_ids": allowed_company_ids,
                "company_id": company_id,
                "partner_id": 12
            },
            "session_id": session_id,
            "last_activity": last_activity
        }
    })


# ============================================================================
# Tests de OdooClient headers (T07)
# ============================================================================

def test_odoo_client_headers_with_session():
    """OdooClient con session_id envía Cookie y NO Authorization."""
    settings = make_settings()
    transport = MockTransport()
    client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key="",  # vacío porque usamos session
        timeout=settings.odoo_timeout_seconds,
        transport=transport,
        session_id="test-session-123",
    )

    headers = client._headers()

    assert "Cookie" in headers
    assert headers["Cookie"] == "session_id=test-session-123"
    assert "Authorization" not in headers
    assert headers["X-Odoo-Database"] == settings.odoo_db


def test_odoo_client_headers_without_session_uses_bearer():
    """OdooClient sin session_id envía Authorization Bearer."""
    settings = make_settings()
    transport = MockTransport()
    client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key="test-api-key-456",
        timeout=settings.odoo_timeout_seconds,
        transport=transport,
        # sin session_id
    )

    headers = client._headers()

    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer test-api-key-456"
    assert "Cookie" not in headers
    assert headers["X-Odoo-Database"] == settings.odoo_db


def test_odoo_client_session_id_takes_priority_over_api_key():
    """Si se pasan ambos, session_id tiene prioridad (cookie, sin Bearer)."""
    settings = make_settings()
    transport = MockTransport()
    client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key="should-not-be-used",
        timeout=settings.odoo_timeout_seconds,
        transport=transport,
        session_id="session-priority",
    )

    headers = client._headers()

    assert "Cookie" in headers
    assert headers["Cookie"] == "session_id=session-priority"
    assert "Authorization" not in headers


# ============================================================================
# Tests de _validate_session (T08-T11)
# ============================================================================

def test_validate_session_success():
    """Sesión válida con last_activity reciente → retorna uid, company_ids, etc."""
    settings = make_settings()
    clock = FixedClock(datetime.now(timezone.utc))
    transport = MockTransport(responses=[make_session_info_response()])

    result = _validate_session(
        session_id="valid-session",
        settings=settings,
        clock=clock,
        transport=transport,
    )

    assert result["uid"] == 7
    assert result["company_id"] == 1
    assert result["allowed_company_ids"] == [1, 2]
    assert result["session_id"] == "valid-session"
    # Verificar que se llamó a get_session_info
    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert "/web/session/get_session_info" in str(req.url)
    assert req.headers["Cookie"] == "session_id=valid-session"


def test_validate_session_expired_24h():
    """Sesión con last_activity > 24h → lanza OdooAuthError (se convierte a 401)."""
    settings = make_settings(SESSION_MAX_AGE_HOURS="24")
    # Clock fijado a ahora; last_activity hace 25 horas
    fixed_now = datetime.now(timezone.utc)
    clock = FixedClock(fixed_now)
    last_activity = (fixed_now - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    transport = MockTransport(responses=[make_session_info_response(last_activity=last_activity)])

    with pytest.raises(OdooAuthError) as exc_info:
        _validate_session(
            session_id="expired-session",
            settings=settings,
            clock=clock,
            transport=transport,
        )

    assert "expirada" in str(exc_info.value).lower() or "24h" in str(exc_info.value).lower()


def test_validate_session_invalid_uid_null():
    """get_session_info devuelve uid: null → OdooAuthError."""
    settings = make_settings()
    clock = FixedClock(datetime.now(timezone.utc))
    
    def handler(request, attempt):
        if "/web/session/get_session_info" in str(request.url):
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "uid": None,
                    "user_context": {"allowed_company_ids": [], "company_id": 1},
                    "last_activity": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                }
            })
        return httpx.Response(200, json={})
    
    transport = MockTransport(handler=handler)

    with pytest.raises(OdooAuthError):
        _validate_session(
            session_id="invalid-session",
            settings=settings,
            clock=clock,
            transport=transport,
        )


def test_validate_session_odoo_error_500():
    """Odoo devuelve 500 en get_session_info → OdooTransientError (se convierte a 503)."""
    settings = make_settings()
    clock = FixedClock(datetime.now(timezone.utc))
    transport = MockTransport(responses=[httpx.Response(500, json={"error": "internal"})])

    with pytest.raises(OdooTransientError):
        _validate_session(
            session_id="error-session",
            settings=settings,
            clock=clock,
            transport=transport,
        )


def test_validate_session_timeout():
    """Timeout en get_session_info → OdooTransientError."""
    settings = make_settings()
    clock = FixedClock(datetime.now(timezone.utc))
    
    def handler(request, attempt):
        if "/web/session/get_session_info" in str(request.url):
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json={})
    
    transport = MockTransport(handler=handler)

    with pytest.raises(OdooTransientError):
        _validate_session(
            session_id="timeout-session",
            settings=settings,
            clock=clock,
            transport=transport,
        )


def test_validate_session_missing_last_activity():
    """Odoo 19 no envía last_activity: la sesión se acepta (uid + user_companies)."""
    settings = make_settings()
    clock = FixedClock(datetime.now(timezone.utc))

    def handler(request, attempt):
        if "/web/session/get_session_info" in str(request.url):
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "uid": 7,
                    "user_companies": {
                        "current_company": 3,
                        "allowed_companies": {
                            "3": {"id": 3, "name": "Empresa 3"},
                            "5": {"id": 5, "name": "Empresa 5"},
                        },
                    },
                    # sin last_activity (Odoo 19)
                }
            })
        return httpx.Response(200, json={})

    transport = MockTransport(handler=handler)

    result = _validate_session(
        session_id="no-last-activity",
        settings=settings,
        clock=clock,
        transport=transport,
    )

    assert result["uid"] == 7
    assert result["company_id"] == 3
    assert result["allowed_company_ids"] == [3, 5]
    assert result["session_id"] == "no-last-activity"

    # Variante Odoo 19: ``user_companies`` como lista (ids o dicts con id).
    transport_list = MockTransport(responses=[httpx.Response(200, json={
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "uid": 9,
            "user_companies": [{"id": 4, "name": "A"}, 6],
            # sin last_activity
        },
    })])

    result_list = _validate_session(
        session_id="list-companies",
        settings=settings,
        clock=clock,
        transport=transport_list,
    )

    assert result_list["uid"] == 9
    assert result_list["company_id"] == 4
    assert result_list["allowed_company_ids"] == [4, 6]


# ============================================================================
# Tests de UserContext dataclass (T14)
# ============================================================================

def test_user_context_has_auth_mode_fields():
    """UserContext incluye auth_mode, session_uid, session_company_id."""
    from contafis_assistant.api.user_auth import UserContext
    from contafis_assistant.odoo.client import OdooClient
    from contafis_assistant.odoo.scope import ScopeService, ScopeSnapshot, ScopeReason
    from contafis_assistant.domain.enums import ScopeState
    from contafis_assistant.domain.time import Clock

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
        user_company_ids=[1, 2],
        active_company_ids=[1],
        is_admin=False,
        is_internal=True,
        attested=True,
        app_env="test",
        checked_at=Clock().now(),
    )
    scope_service = ScopeService(client, app_env="test", attested=True)

    ctx = UserContext(
        token="test-token",
        user_key="abc123",
        client=client,
        snapshot=snapshot,
        companies=[{"id": 1, "name": "Test"}],
        scope_service=scope_service,
        auth_mode="session",
        session_uid=7,
        session_company_id=1,
    )

    assert ctx.auth_mode == "session"
    assert ctx.session_uid == 7
    assert ctx.session_company_id == 1


def test_user_context_legacy_mode_defaults():
    """UserContext en modo legacy (token) tiene auth_mode='token' y session_* = None."""
    from contafis_assistant.api.user_auth import UserContext
    from contafis_assistant.odoo.client import OdooClient
    from contafis_assistant.odoo.scope import ScopeService, ScopeSnapshot, ScopeReason
    from contafis_assistant.domain.enums import ScopeState
    from contafis_assistant.domain.time import Clock

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
        user_company_ids=[1, 2],
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
        session_uid=None,
        session_company_id=None,
    )

    assert ctx.auth_mode == "token"
    assert ctx.session_uid is None
    assert ctx.session_company_id is None