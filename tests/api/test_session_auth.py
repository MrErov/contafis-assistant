"""Tests de API para autenticación por session_id de Odoo (Spec 005).

Cubren: flujo completo con session válida/expirada/inválida, fallback legacy, prioridad.
"""

from __future__ import annotations

import httpx
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from contafis_assistant.config import Settings
from contafis_assistant.main import create_app
from contafis_assistant.odoo.client import OdooClient


CHAT_BODY = {"message": "obligaciones de la compañía", "thread_id": "thread_abc"}


def make_settings(**env: str) -> Settings:
    from tests.api.conftest import make_settings as _make_settings
    return _make_settings(**env)


class MockOdooTransport:
    """Transporte que simula Odoo JSON/2 y captura requests + close."""

    def __init__(self, responses: list[httpx.Response] | None = None, handler=None):
        self.responses = responses or []
        self.handler = handler
        self.requests = []
        self.close_count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.handler:
            return self.handler(request, len(self.requests))
        if self.responses:
            return self.responses.pop(0)
        return httpx.Response(200, json=[])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        return self.handle_request(request)

    def close(self) -> None:
        self.close_count += 1


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


def make_scope_check_responses():
    """Secuencia estándar de scope_check (5 llamadas tras get_session_info)."""
    return [
        # context_get (ya no se llama si get_session_info dio uid)
        # has_group x3
        httpx.Response(200, json=False),  # base.group_system
        httpx.Response(200, json=False),  # base.group_erp_manager
        httpx.Response(200, json=True),   # base.group_user
        # search_read companies
        httpx.Response(200, json=[{"id": 1, "name": "Empresa 1"}, {"id": 2, "name": "Empresa 2"}]),
    ]


def make_tool_responses():
    """Respuestas para tool calls (query_obligations, etc.)."""
    return [httpx.Response(200, json=[]) for _ in range(10)]


def build_full_mock_transport(
    session_response: httpx.Response,
    settings: Settings,
):
    """Construye transporte con handler que responde a get_session_info + scope_check + tools."""
    call_count = {"n": 0}
    scope_responses = make_scope_check_responses()
    tool_responses = make_tool_responses()

    def handler(request, attempt):
        call_count["n"] += 1
        url = str(request.url)

        # 1. get_session_info
        if "/web/session/get_session_info" in url:
            return session_response

        # 2-5. scope_check (has_group x3 + search_read)
        if call_count["n"] <= 5:
            if scope_responses:
                return scope_responses.pop(0)

        # 6+. tool calls
        if tool_responses:
            return tool_responses.pop(0)

        return httpx.Response(200, json=[])

    transport = MockOdooTransport(handler=handler)
    return transport, transport


def make_app_with_session_transport(settings: Settings, transport: MockOdooTransport):
    """Crea app con runtime que usa OdooClient real con el transporte mock."""
    from contafis_assistant.main import ChatRuntime
    from tests.api.conftest import (
        scope_snapshot, FakeScope, FakeCorpus, CountingClassifier,
        CountingTool, ToolOutcome, ObligationsResult, Status, ToolName,
    )
    from contafis_assistant.graph.build import GraphDependencies
    from contafis_assistant.memory.threads import ThreadStore
    from contafis_assistant.domain.time import Clock

    snapshot = scope_snapshot()
    scope = FakeScope(snapshot=snapshot)
    corpus = FakeCorpus()
    classifier = CountingClassifier()
    tool = CountingTool()
    store = ThreadStore()

    deps = GraphDependencies(
        classifier=classifier,
        tools={ToolName.query_obligations: tool},
        store=store,
        scope_snapshot=snapshot,
    )

    odoo_client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key="dummy",  # Se sobrescribe en get_user_context_from_session
        timeout=settings.odoo_timeout_seconds,
        transport=transport,
    )

    runtime = ChatRuntime(
        settings=settings,
        store=store,
        deps=deps,
        scope=scope,
        corpus=corpus,
        odoo_client=odoo_client,
        clock=Clock(),
    )
    runtime.ready = True
    runtime.user_transport = transport

    return create_app(settings, runtime=runtime), transport


# ============================================================================
# Tests de API (T21-T26)
# ============================================================================

def test_chat_with_valid_session(make_settings):
    """X-Odoo-Session válido → 200, auth_mode='session', session_uid seteado."""
    settings = make_settings()
    session_response = make_session_info_response()
    transport = build_full_mock_transport(session_response, settings)[1]
    app, captured_transport = make_app_with_session_transport(settings, transport)
    client = TestClient(app)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-Odoo-Session": "valid-session-123"},
        )

    assert response.status_code == 200
    body = response.json()
    # El endpoint /chat no expone auth_mode directamente, pero verificamos
    # que la request pasó y el cliente usó la cookie
    assert len(captured_transport.requests) >= 1
    first_req = captured_transport.requests[0]
    assert "/web/session/get_session_info" in str(first_req.url)
    assert first_req.headers["Cookie"] == "session_id=valid-session-123"
    assert "Authorization" not in first_req.headers


def test_chat_with_expired_session(make_settings):
    """X-Odoo-Session con last_activity > 24h → 401."""
    settings = make_settings(SESSION_MAX_AGE_HOURS="24")
    fixed_now = datetime.now(timezone.utc)
    last_activity = (fixed_now - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    session_response = make_session_info_response(last_activity=last_activity)
    
    transport = build_full_mock_transport(session_response, settings)[1]
    app, _ = make_app_with_session_transport(settings, transport)
    client = TestClient(app)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-Odoo-Session": "expired-session"},
        )

    assert response.status_code == 401
    body = response.json()
    assert "expirada" in body["detail"].lower() or "24h" in body["detail"].lower()


def test_chat_with_invalid_session(make_settings):
    """X-Odoo-Session rechazado por Odoo (uid null) → 401."""
    settings = make_settings()
    
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
        return httpx.Response(200, json=[])

    transport = MockOdooTransport(handler=handler)
    app, _ = make_app_with_session_transport(settings, transport)
    client = TestClient(app)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-Odoo-Session": "invalid-session"},
        )

    assert response.status_code == 401
    body = response.json()
    assert "expirada" in body["detail"].lower() or "inválida" in body["detail"].lower()


def test_chat_with_legacy_token(make_settings):
    """Sin X-Odoo-Session, con X-User-Token válido → 200 (flujo legacy)."""
    settings = make_settings()
    
    # Mock para flujo legacy: OdooClient con Bearer token
    call_count = {"n": 0}
    
    def handler(request, attempt):
        call_count["n"] += 1
        # Legacy flow: scope_check directo (sin get_session_info)
        if call_count["n"] == 1:
            return httpx.Response(200, json={"uid": 7, "allowed_company_ids": [1, 2]})
        elif call_count["n"] <= 5:
            if call_count["n"] <= 4:
                return httpx.Response(200, json=False if call_count["n"] < 4 else True)
            else:
                return httpx.Response(200, json=[{"id": 1, "name": "Empresa 1"}])
        return httpx.Response(200, json=[])

    transport = MockOdooTransport(handler=handler)
    
    # Para legacy, necesitamos app con runtime que use Bearer
    from contafis_assistant.main import ChatRuntime
    from tests.api.conftest import (
        scope_snapshot, FakeScope, FakeCorpus, CountingClassifier,
        CountingTool, ToolOutcome, ObligationsResult, Status, ToolName,
    )
    from contafis_assistant.graph.build import GraphDependencies
    from contafis_assistant.memory.threads import ThreadStore
    from contafis_assistant.odoo.client import OdooClient

    snapshot = scope_snapshot()
    scope = FakeScope(snapshot=snapshot)
    corpus = FakeCorpus()
    classifier = CountingClassifier()
    tool = CountingTool()
    store = ThreadStore()

    deps = GraphDependencies(
        classifier=classifier,
        tools={ToolName.query_obligations: tool},
        store=store,
        scope_snapshot=snapshot,
    )

    odoo_client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key="dummy",  # Se sobrescribe con X-User-Token
        timeout=settings.odoo_timeout_seconds,
        transport=transport,
    )

    runtime = ChatRuntime(
        settings=settings,
        store=store,
        deps=deps,
        scope=scope,
        corpus=corpus,
        odoo_client=odoo_client,
    )
    runtime.ready = True
    runtime.user_transport = transport

    app = create_app(settings, runtime=runtime)
    client = TestClient(app)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-User-Token": "legacy-user-token-456"},
        )

    assert response.status_code == 200
    # Verificar que usó Authorization Bearer con el token legacy
    assert len(transport.requests) >= 1
    first_req = transport.requests[0]
    assert first_req.headers["Authorization"] == "Bearer legacy-user-token-456"


def test_chat_without_auth(make_settings):
    """Sin X-Odoo-Session ni X-User-Token → 401."""
    settings = make_settings()
    from tests.api.conftest import make_runtime
    runtime, *_ = make_runtime(settings)
    app = create_app(settings, runtime=runtime)
    client = TestClient(app)

    with client:
        response = client.post("/chat", json=CHAT_BODY)

    assert response.status_code == 401
    body = response.json()
    assert "autenticación" in body["detail"].lower() or "x-odoo-session" in body["detail"].lower()


def test_session_priority_over_token(make_settings):
    """Ambos headers presentes → usa X-Odoo-Session (session), ignora X-User-Token."""
    settings = make_settings()
    session_response = make_session_info_response()
    transport = build_full_mock_transport(session_response, settings)[1]
    app, captured_transport = make_app_with_session_transport(settings, transport)
    client = TestClient(app)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={
                "X-Odoo-Session": "session-priority",
                "X-User-Token": "should-be-ignored",
            },
        )

    assert response.status_code == 200
    # Verificar que la PRIMERA request fue get_session_info con la cookie de sesión
    first_req = captured_transport.requests[0]
    assert "/web/session/get_session_info" in str(first_req.url)
    assert first_req.headers["Cookie"] == "session_id=session-priority"
    assert "Authorization" not in first_req.headers


def test_scope_endpoint_with_session(make_settings):
    """GET /scope con X-Odoo-Session válido → 200 con scope_info."""
    settings = make_settings()
    session_response = make_session_info_response()
    transport = build_full_mock_transport(session_response, settings)[1]
    app, _ = make_app_with_session_transport(settings, transport)
    client = TestClient(app)

    with client:
        response = client.get("/scope", headers={"X-Odoo-Session": "scope-session"})

    assert response.status_code == 200
    body = response.json()
    assert "uid" in body
    assert "companies" in body
    assert body["uid"] == 7


def test_health_endpoint_unaffected(make_settings):
    """GET /health no requiere X-Odoo-Session ni X-User-Token (solo X-API-Key)."""
    settings = make_settings()
    from tests.api.conftest import make_runtime
    runtime, *_ = make_runtime(settings)
    app = create_app(settings, runtime=runtime)
    client = TestClient(app)

    with client:
        response = client.get("/health")

    # health solo valida X-API-Key (middleware), no auth de usuario
    assert response.status_code in (200, 503)  # depende de scope_check


# ============================================================================
# Test de estructura del mock (T22)
# ============================================================================

def test_session_info_mock_structure():
    """Verificar que el mock de get_session_info replica estructura real de Odoo."""
    resp = make_session_info_response()
    data = resp.json()
    
    assert data["jsonrpc"] == "2.0"
    assert "result" in data
    result = data["result"]
    assert "uid" in result
    assert isinstance(result["uid"], int)
    assert "user_context" in result
    assert "allowed_company_ids" in result["user_context"]
    assert "company_id" in result["user_context"]
    assert "last_activity" in result
    assert "session_id" in result
    # Formato de fecha Odoo: "YYYY-MM-DD HH:MM:SS"
    assert len(result["last_activity"]) == 19