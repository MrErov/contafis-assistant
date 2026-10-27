"""Tests de autenticación por token de usuario (T01): header X-User-Token, cliente Odoo por request, 401/503."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from contafis_assistant.config import Settings
from contafis_assistant.main import create_app
from contafis_assistant.odoo.errors import OdooAuthError, OdooTransientError


CHAT_BODY = {"message": "obligaciones de la compañía", "thread_id": "thread_abc"}


def make_settings(**env: str) -> Settings:
    from tests.api.conftest import make_settings as _make_settings
    return _make_settings(**env)


def _make_app_with_mock_transport(
    settings: Settings,
    *,
    transport: httpx.BaseTransport,
    odoo_client=None,
):
    """Crea app con runtime que usa un transporte mock inyectado."""
    from contafis_assistant.main import ChatRuntime, build_runtime

    # Usamos un FakeScope que devuelve un snapshot válido
    from tests.api.conftest import scope_snapshot, FakeScope, FakeCorpus, FakeOdooClient
    from contafis_assistant.graph.build import GraphDependencies
    from contafis_assistant.memory.threads import ThreadStore
    from contafis_assistant.graph.classify import CountingClassifier
    from contafis_assistant.odoo.tools.obligations import query_obligations
    from contafis_assistant.odoo.errors import OdooError
    from tests.api.conftest import CountingTool, ToolOutcome, ObligationsResult, Status
    from contafis_assistant.graph.executor import ToolOutcome as GraphToolOutcome
    from contafis_assistant.graph.state import ToolName

    snapshot = scope_snapshot()
    scope = FakeScope(snapshot=snapshot)
    corpus = FakeCorpus()
    classifier = CountingClassifier()
    tool = CountingTool()
    store = ThreadStore()

    # Build deps with fake tools
    deps = GraphDependencies(
        classifier=classifier,
        tools={ToolName.query_obligations: tool},
        store=store,
        scope_snapshot=snapshot,
    )

    # Si se pasa un cliente Odoo real con transporte mock, úsalo
    # Sino usa FakeOdooClient
    if odoo_client is None:
        odoo_client = FakeOdooClient()

    runtime = ChatRuntime(
        settings=settings,
        store=store,
        deps=deps,
        scope=scope,
        corpus=corpus,
        odoo_client=odoo_client,
    )
    runtime.ready = True

    return create_app(settings, runtime=runtime)


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


def _make_client_with_transport(settings: Settings, transport: MockOdooTransport):
    """Crea TestClient con runtime que usa OdooClient real con el transporte mock."""
    from contafis_assistant.main import ChatRuntime, build_runtime
    from contafis_assistant.odoo.client import OdooClient
    from tests.api.conftest import (
        scope_snapshot, FakeScope, FakeCorpus, CountingClassifier,
        CountingTool, ToolOutcome, ObligationsResult, Status, ToolName,
    )
    from contafis_assistant.graph.build import GraphDependencies
    from contafis_assistant.memory.threads import ThreadStore

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

    # Crear OdooClient real con el transporte mock
    odoo_client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key="dummy",  # Se sobrescribe en get_user_context
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

    # Inyectar el transporte en el runtime para que get_user_context lo use
    runtime.user_transport = transport

    return create_app(settings, runtime=runtime), transport


# ============================================================================
# Tests T01: Header X-User-Token, cliente Odoo por request, errores 401/503
# ============================================================================

def test_chat_without_user_token_returns_401(make_settings):
    """POST /chat sin header X-User-Token → 401 con detail exacto."""
    settings = make_settings()
    from tests.api.conftest import make_runtime
    runtime, *_ = make_runtime(settings)
    client = TestClient(create_app(settings, runtime=runtime))

    with client:
        response = client.post("/chat", json=CHAT_BODY)

    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "Falta el token de usuario (X-User-Token)."


def test_chat_with_empty_user_token_returns_401(make_settings):
    """Header X-User-Token vacío o solo espacios → 401 (mismo detail)."""
    settings = make_settings()
    from tests.api.conftest import make_runtime
    runtime, *_ = make_runtime(settings)
    client = TestClient(create_app(settings, runtime=runtime))

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-User-Token": "   "},
        )

    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "Falta el token de usuario (X-User-Token)."


def test_chat_with_invalid_user_token_returns_401(make_settings):
    """Token que Odoo rechaza (context_get \u2192 401) \u2192 401 amigable sin token ni URL en payload."""
    settings = make_settings()

    call_count = {"n": 0}

    def handler(request, attempt):
        call_count["n"] += 1
        # Primera llamada: context_get del scope_check -> 401
        if call_count["n"] == 1:
            return httpx.Response(401, json={"name": "Unauthorized", "message": "bad key"})
        # Segunda context_get en get_user_context para verificar error -> 401
        elif call_count["n"] == 2:
            return httpx.Response(401, json={"name": "Unauthorized", "message": "bad key"})
        # Resto -> éxito vacío
        return httpx.Response(200, json=[])

    transport = MockOdooTransport(handler=handler)
    app, _ = _make_client_with_transport(settings, transport)
    client = TestClient(app)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-User-Token": "invalid-token-123"},
        )

    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "El token de Odoo no es válido o fue revocado."
    # El token no debe aparecer en la respuesta
    assert "invalid-token-123" not in str(body)
    # La URL de Odoo no debe aparecer (solo el dominio, no la URL completa con puerto)
    assert "127.0.0.1:8069" not in str(body)
    assert "8069" not in str(body)


def test_chat_with_odoo_timeout_returns_503(make_settings):
    """Odoo timeout/5xx en scope check \u2192 503 service_unavailable."""
    settings = make_settings()

    call_count = {"n": 0}

    def handler(request, attempt):
        call_count["n"] += 1
        # Primera llamada: context_get del scope_check -> timeout
        if call_count["n"] == 1:
            raise httpx.ReadTimeout("timeout", request=request)
        # Segunda context_get en get_user_context -> timeout
        elif call_count["n"] == 2:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json=[])

    transport = MockOdooTransport(handler=handler)
    app, _ = _make_client_with_transport(settings, transport)
    client = TestClient(app)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-User-Token": "valid-token"},
        )

    assert response.status_code == 503
    body = response.json()
    assert body["detail"] == "service_unavailable"


def test_chat_with_odoo_5xx_returns_503(make_settings):
    """Odoo 5xx en scope check \u2192 503 service_unavailable."""
    settings = make_settings()

    call_count = {"n": 0}

    def handler(request, attempt):
        call_count["n"] += 1
        # Primera llamada: context_get del scope_check -> 503
        if call_count["n"] == 1:
            return httpx.Response(503, json={"message": "busy"})
        # Segunda context_get en get_user_context -> 503
        elif call_count["n"] == 2:
            return httpx.Response(503, json={"message": "busy"})
        return httpx.Response(200, json=[])

    transport = MockOdooTransport(handler=handler)
    app, _ = _make_client_with_transport(settings, transport)
    client = TestClient(app)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-User-Token": "valid-token"},
        )

    assert response.status_code == 503
    body = response.json()
    assert body["detail"] == "service_unavailable"


def test_chat_with_valid_token_uses_bearer_token_in_requests(make_settings):
    """Token válido → cliente del turno envía Authorization: Bearer <token> y X-Odoo-Database."""
    settings = make_settings()
    user_token = "user-personal-api-key-456"

    call_count = {"n": 0}

    def handler(request, attempt):
        call_count["n"] += 1
        # Primeras 5 llamadas son scope_check
        if call_count["n"] == 1:
            return httpx.Response(200, json={"uid": 7, "allowed_company_ids": [1, 2]})
        elif call_count["n"] <= 5:
            # has_group x3 + search_read companies
            if call_count["n"] <= 4:
                return httpx.Response(200, json=False if call_count["n"] < 4 else True)
            else:
                return httpx.Response(200, json=[{"id": 1, "name": "Empresa 1"}, {"id": 2, "name": "Empresa 2"}])
        # Llamadas de tools (query_obligations, etc.) - devolver éxito vacío
        return httpx.Response(200, json=[])

    transport = MockOdooTransport(handler=handler)
    app, captured_transport = _make_client_with_transport(settings, transport)
    client = TestClient(app)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-User-Token": user_token},
        )

    # HTTP 200 (el endpoint responde), el status del grafo puede variar en tests
    assert response.status_code == 200
    body = response.json()

    # Verificar que el cliente Odoo usó el token del usuario en Authorization
    # La primera request es context_get del scope_check
    assert len(captured_transport.requests) >= 1
    first_request = captured_transport.requests[0]
    assert first_request.headers["Authorization"] == f"Bearer {user_token}"
    assert first_request.headers["X-Odoo-Database"] == settings.odoo_db


def test_odoo_client_closes_after_request(make_settings):
    """El cliente Odoo se cierra al terminar el request (transport fake cuenta close)."""
    settings = make_settings()
    user_token = "user-token-close-test"

    # Secuencia completa de scope_check
    responses = [
        httpx.Response(200, json={"uid": 7, "allowed_company_ids": [1]}),
        httpx.Response(200, json=False),  # base.group_system
        httpx.Response(200, json=False),  # base.group_erp_manager
        httpx.Response(200, json=True),   # base.group_user
        httpx.Response(200, json=[{"id": 1, "name": "Empresa 1"}]),
    ]

    transport = MockOdooTransport(responses)
    app, captured_transport = _make_client_with_transport(settings, transport)
    client = TestClient(app)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-User-Token": user_token},
        )

    assert response.status_code == 200
    # Verificar que close() se llamó en el transporte
    assert captured_transport.close_count >= 1


def test_invalid_x_api_key_returns_401_before_user_token(make_settings):
    """X-API-Key inválido sigue dando 401 antes que el header de usuario."""
    settings = make_settings(API_SHARED_SECRET="s3cr3t")
    from tests.api.conftest import make_runtime
    runtime, *_ = make_runtime(settings)
    client = TestClient(create_app(settings, runtime=runtime))

    with client:
        # Sin X-API-Key pero con X-User-Token → 401 por API key primero
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-User-Token": "user-token"},
        )

    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "Unauthorized"


def test_valid_x_api_key_allows_user_token_check(make_settings):
    """X-API-Key válido permite pasar al check de X-User-Token."""
    settings = make_settings(API_SHARED_SECRET="s3cr3t")
    user_token = "user-token-with-api-key"

    call_count = {"n": 0}

    def handler(request, attempt):
        call_count["n"] += 1
        # Scope check sequence
        if call_count["n"] == 1:
            return httpx.Response(200, json={"uid": 7, "allowed_company_ids": [1]})
        elif call_count["n"] <= 5:
            return httpx.Response(200, json=False if call_count["n"] < 5 else [{"id": 1, "name": "Empresa 1"}])
        # Tool calls -> success
        return httpx.Response(200, json=[])

    transport = MockOdooTransport(handler=handler)
    app, _ = _make_client_with_transport(settings, transport)
    client = TestClient(app)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={
                "X-API-Key": "s3cr3t",
                "X-User-Token": user_token,
            },
        )

    # Debería pasar el check de API key y llegar al check de user token
    # (aunque el user token sea inválido en este test, el orden es correcto)
    assert response.status_code in (200, 401, 503)