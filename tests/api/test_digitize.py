"""Tests de API para endpoint /digitize (Spec 006).

Cubren: factura Z, nota crédito, nota débito, baja confianza, RIF inválido,
montos incoherentes, errores de validación, auth, timeouts.
"""

from __future__ import annotations

import json
import httpx
import pytest
from fastapi.testclient import TestClient

from contafis_assistant.config import Settings
from contafis_assistant.main import create_app
from contafis_assistant.domain.digitize import DigitizeRequest
from tests.api.conftest import (
    make_fake_invoice_image,
    mock_nim_vision_success,
    mock_nim_vision_low_confidence,
    mock_nim_vision_timeout,
    mock_nim_vision_invalid_json,
    mock_nim_vision_nota_credito,
    mock_nim_vision_nota_debito,
    mock_nim_vision_invalid_rif,
    mock_nim_vision_amount_mismatch,
)


# ============================================================================
# Mock helpers para session validation
# ============================================================================

def make_session_info_response(
    uid: int = 7,
    company_id: int = 1,
    allowed_company_ids: list[int] | None = None,
    last_activity: str | None = None,
) -> httpx.Response:
    """Construye respuesta exitosa de /web/session/get_session_info."""
    from datetime import datetime, timezone
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
            "session_id": "test-session-abc",
            "last_activity": last_activity
        }
    })


# ============================================================================
# Helpers
# ============================================================================

def make_settings(**env: str) -> Settings:
    from tests.api.conftest import make_settings as _make_settings
    return _make_settings(**env)


def _make_mock_chatopenai(response_content: str):
    """Crea una clase Mock para ChatOpenAI que retorna response_content."""
    class MockChatOpenAI:
        def __init__(self, *args, **kwargs):
            pass
        
        def invoke(self, messages):
            class MockResponse:
                content = response_content
            return MockResponse()
    
    return MockChatOpenAI


def make_app_with_odoo_transport(settings: Settings):
    """Crea app con runtime que usa transporte mock para Odoo (session + scope_check)."""
    from contafis_assistant.main import ChatRuntime
    from tests.api.conftest import (
        scope_snapshot, FakeScope, FakeCorpus, CountingClassifier,
        CountingTool, ToolOutcome, ObligationsResult, Status, ToolName,
    )
    from contafis_assistant.graph.build import GraphDependencies
    from contafis_assistant.memory.threads import ThreadStore
    from contafis_assistant.domain.time import Clock
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

    # Transporte para Odoo (session validation + scope_check)
    class OdooTransport:
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            
            # Session validation endpoint
            if "/web/session/get_session_info" in url:
                return make_session_info_response()
            
            # Odoo scope_check endpoints
            if "context_get" in url:
                return httpx.Response(200, json={"uid": 7, "allowed_company_ids": [1, 2]})
            if "has_group" in url:
                if "group_system" in url or "group_erp_manager" in url:
                    return httpx.Response(200, json=False)
                if "group_user" in url:
                    return httpx.Response(200, json=True)
            if "res.company" in url and "search_read" in url:
                return httpx.Response(200, json=[{"id": 1, "name": "Test Company"}])
            
            # Default
            return httpx.Response(200, json=[])
        
        def __call__(self, request: httpx.Request) -> httpx.Response:
            return self.handle_request(request)
        
        def close(self) -> None:
            pass

    transport = OdooTransport()
    
    odoo_client = OdooClient(
        url=settings.odoo_url,
        database=settings.odoo_db,
        api_key="dummy",
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

    return create_app(settings, runtime=runtime)


# ============================================================================
# Tests de API — T23 a T29
# ============================================================================

def test_digitize_factura_z_success(make_settings, monkeypatch):
    """Factura Z legible → 200, estructura correcta, sin warnings críticos."""
    settings = make_settings()
    
    # Mock ChatOpenAI para NIM vision
    mock_response = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.92,
        "extracted": {
            "supplier_name": "Distribuidora El Sol C.A.",
            "supplier_rif": "J-12345678-1",  # válido
            "invoice_number": "001-001-00001234",
            "control_number": "ABC1234567890",
            "invoice_date": "2026-10-15",
            "due_date": "2026-11-15",
            "currency": "USD",
            "subtotal": 100.00,
            "iva_rate": 0.16,
            "iva_amount": 16.00,
            "igtf_rate": 0.03,
            "igtf_amount": 3.00,
            "total": 119.00,
            "lines": [{"description": "Servicios", "quantity": 1, "unit_price": 100.00, "total": 100.00}],
            "credit_note_ref": None,
            "debit_note_ref": None,
        }
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("factura_z")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "factura_001.jpg",
                "document_type": "factura_z",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 200
    data = response.json()
    assert data["document_type"] == "factura_z"
    assert data["confidence"] == 0.92
    assert data["extracted"]["supplier_name"] == "Distribuidora El Sol C.A."
    assert data["extracted"]["supplier_rif"] == "J-12345678-1"
    assert data["extracted"]["currency"] == "USD"
    assert data["extracted"]["subtotal"] == 100.00
    assert data["extracted"]["iva_amount"] == 16.00
    assert data["extracted"]["igtf_amount"] == 3.00
    assert data["extracted"]["total"] == 119.00
    assert len(data["extracted"]["lines"]) == 1
    warning_codes = {w["code"] for w in data["warnings"]}
    assert "invalid_rif" not in warning_codes
    assert "amount_mismatch" not in warning_codes


def test_digitize_nota_credito_success(make_settings, monkeypatch):
    """Nota de crédito legible → 200, montos negativos, credit_note_ref poblado."""
    settings = make_settings()
    
    mock_response = json.dumps({
        "document_type": "nota_credito",
        "confidence": 0.90,
        "extracted": {
            "supplier_name": "Distribuidora El Sol C.A.",
            "supplier_rif": "J-12345678-1",
            "invoice_number": "001-001-00001235",
            "control_number": "DEF0987654321",
            "invoice_date": "2026-10-20",
            "due_date": None,
            "currency": "USD",
            "subtotal": -50.00,
            "iva_rate": 0.16,
            "iva_amount": -8.00,
            "igtf_rate": 0.03,
            "igtf_amount": -1.50,
            "total": -59.50,
            "lines": [{"description": "Devolución servicio sep 2026", "quantity": 1, "unit_price": -50.00, "total": -50.00}],
            "credit_note_ref": "001-001-00001234",
            "debit_note_ref": None,
        }
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("nota_credito")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "nota_credito_001.jpg",
                "document_type": "nota_credito",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 200
    data = response.json()
    assert data["document_type"] == "nota_credito"
    assert data["confidence"] == 0.90
    assert data["extracted"]["subtotal"] == -50.00
    assert data["extracted"]["iva_amount"] == -8.00
    assert data["extracted"]["igtf_amount"] == -1.50
    assert data["extracted"]["total"] == -59.50
    assert data["extracted"]["credit_note_ref"] == "001-001-00001234"
    assert data["extracted"]["debit_note_ref"] is None


def test_digitize_nota_debito_success(make_settings, monkeypatch):
    """Nota de débito legible → 200, montos positivos, debit_note_ref poblado."""
    settings = make_settings()
    
    mock_response = json.dumps({
        "document_type": "nota_debito",
        "confidence": 0.88,
        "extracted": {
            "supplier_name": "Distribuidora El Sol C.A.",
            "supplier_rif": "J-12345678-1",
            "invoice_number": "001-001-00001236",
            "control_number": "GHI5555555555",
            "invoice_date": "2026-10-25",
            "due_date": "2026-11-25",
            "currency": "USD",
            "subtotal": 25.00,
            "iva_rate": 0.16,
            "iva_amount": 4.00,
            "igtf_rate": 0.03,
            "igtf_amount": 0.75,
            "total": 29.75,
            "lines": [{"description": "Ajuste por diferencia de tasa", "quantity": 1, "unit_price": 25.00, "total": 25.00}],
            "credit_note_ref": None,
            "debit_note_ref": "001-001-00001234",
        }
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("nota_debito")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "nota_debito_001.jpg",
                "document_type": "nota_debito",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 200
    data = response.json()
    assert data["document_type"] == "nota_debito"
    assert data["confidence"] == 0.88
    assert data["extracted"]["subtotal"] == 25.00
    assert data["extracted"]["iva_amount"] == 4.00
    assert data["extracted"]["igtf_amount"] == 0.75
    assert data["extracted"]["total"] == 29.75
    assert data["extracted"]["debit_note_ref"] == "001-001-00001234"
    assert data["extracted"]["credit_note_ref"] is None


def test_digitize_low_confidence(make_settings, monkeypatch):
    """Imagen borrosa → confidence < 0.5, warning low_confidence."""
    settings = make_settings()
    
    mock_response = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.25,
        "extracted": {
            "supplier_name": None,
            "supplier_rif": None,
            "invoice_number": None,
            "control_number": None,
            "invoice_date": None,
            "due_date": None,
            "currency": "VES",
            "subtotal": None,
            "iva_rate": None,
            "iva_amount": None,
            "igtf_rate": None,
            "igtf_amount": None,
            "total": None,
            "lines": [],
            "credit_note_ref": None,
            "debit_note_ref": None,
        }
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("factura_z")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "borrosa.jpg",
                "document_type": "auto",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 200
    data = response.json()
    assert data["confidence"] == 0.25
    warning_codes = {w["code"] for w in data["warnings"]}
    assert "low_confidence" in warning_codes
    assert data["extracted"]["supplier_name"] is None
    assert data["extracted"]["total"] is None


def test_digitize_invalid_rif_warning(make_settings, monkeypatch):
    """RIF extraído inválido → warning invalid_rif."""
    settings = make_settings()
    
    mock_response = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.85,
        "extracted": {
            "supplier_name": "Test Proveedor",
            "supplier_rif": "J-12345678-0",  # checksum inválido
            "invoice_number": "001",
            "control_number": "CTRL",
            "invoice_date": "2026-10-15",
            "currency": "VES",
            "subtotal": 100.00,
            "iva_amount": 16.00,
            "total": 116.00,
            "lines": [],
        }
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("factura_z")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "factura_rif_invalido.jpg",
                "document_type": "factura_z",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 200
    data = response.json()
    warning_codes = {w["code"] for w in data["warnings"]}
    assert "invalid_rif" in warning_codes
    assert data["extracted"]["supplier_rif"] == "J-12345678-0"
    rif_warning = next(w for w in data["warnings"] if w["code"] == "invalid_rif")
    assert "J-12345678-0" in rif_warning["extracted_value"]


def test_digitize_amount_mismatch_warning(make_settings, monkeypatch):
    """Montos incoherentes → warning amount_mismatch."""
    settings = make_settings()
    
    mock_response = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.90,
        "extracted": {
            "supplier_name": "Test Proveedor",
            "supplier_rif": "J-12345678-1",
            "invoice_number": "001",
            "control_number": "CTRL",
            "invoice_date": "2026-10-15",
            "currency": "VES",
            "subtotal": 100.00,
            "iva_amount": 16.00,
            "igtf_amount": 3.00,
            "total": 120.00,  # incoherente: debería ser 119.00
            "lines": [],
        }
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("factura_z")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "factura_montos_malos.jpg",
                "document_type": "factura_z",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 200
    data = response.json()
    warning_codes = {w["code"] for w in data["warnings"]}
    assert "amount_mismatch" in warning_codes
    mismatch_warning = next(w for w in data["warnings"] if w["code"] == "amount_mismatch")
    assert "119.00" in mismatch_warning["message"]
    assert "120.00" in mismatch_warning["message"]
    assert mismatch_warning["field"] == "total"


def test_digitize_without_auth(make_settings):
    """Sin headers de autenticación → 401."""
    settings = make_settings()
    from tests.api.conftest import make_runtime
    runtime, *_ = make_runtime(settings)
    app = create_app(settings, runtime=runtime)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("factura_z")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "factura_001.jpg",
            },
        )
    
    assert response.status_code == 401


def test_digitize_invalid_base64(make_settings, monkeypatch):
    """base64 corrupto → 400."""
    settings = make_settings()
    mock_response = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.92,
        "extracted": {"supplier_name": "Test", "currency": "VES"}
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": "esto no es base64 válido!!!",
                "filename": "factura_001.jpg",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 422  # FastAPI validation error
    data = response.json()
    assert "base64" in str(data).lower() or "image_base64" in str(data).lower()


def test_digitize_invalid_extension(make_settings, monkeypatch):
    """Extensión .pdf → 422 (FastAPI validation)."""
    settings = make_settings()
    mock_response = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.92,
        "extracted": {"supplier_name": "Test", "currency": "VES"}
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("factura_z")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "factura_001.pdf",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 422


def test_digitize_nim_timeout(make_settings, monkeypatch):
    """NIM timeout → 503."""
    settings = make_settings()
    
    class MockChatOpenAI:
        def __init__(self, *args, **kwargs):
            pass
        
        def invoke(self, messages):
            raise httpx.ReadTimeout("timeout", request=None)
    
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", MockChatOpenAI)
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("factura_z")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "factura_001.jpg",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 503
    assert response.json()["detail"] == "service_unavailable"


def test_digitize_nim_invalid_json(make_settings, monkeypatch):
    """NIM devuelve JSON inválido → 503."""
    settings = make_settings()
    
    class MockChatOpenAI:
        def __init__(self, *args, **kwargs):
            pass
        
        def invoke(self, messages):
            class MockResponse:
                content = "esto no es json válido {{{"
            return MockResponse()
    
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", MockChatOpenAI)
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("factura_z")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "factura_001.jpg",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 503


def test_digitize_auto_detect_type(make_settings, monkeypatch):
    """document_type=auto → detecta tipo correctamente."""
    settings = make_settings()
    
    # Test factura_z
    mock_response_fz = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.92,
        "extracted": {
            "supplier_name": "Test", "supplier_rif": "J-12345678-1",
            "currency": "USD", "subtotal": 100.00, "iva_amount": 16.00,
            "igtf_amount": 3.00, "total": 119.00, "lines": []
        }
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response_fz))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("factura_z")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "factura_001.jpg",
                "document_type": "auto",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 200
    data = response.json()
    assert data["document_type"] == "factura_z"
    
    # Test nota_credito
    mock_response_nc = json.dumps({
        "document_type": "nota_credito",
        "confidence": 0.90,
        "extracted": {
            "supplier_name": "Test", "supplier_rif": "J-12345678-1",
            "currency": "USD", "subtotal": -50.00, "iva_amount": -8.00,
            "total": -58.00, "lines": [], "credit_note_ref": "001"
        }
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response_nc))
    
    app2 = make_app_with_odoo_transport(settings)
    client2 = TestClient(app2)
    img_b64_nc = make_fake_invoice_image("nota_credito")
    
    with client2:
        response2 = client2.post(
            "/digitize",
            json={
                "image_base64": img_b64_nc,
                "filename": "nota_credito_001.jpg",
                "document_type": "auto",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["document_type"] == "nota_credito"


def test_digitize_legacy_token_auth(make_settings, monkeypatch):
    """Autenticación legacy con X-User-Token funciona."""
    settings = make_settings()
    
    mock_response = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.92,
        "extracted": {"supplier_name": "Test", "currency": "VES", "subtotal": 100.00, "iva_amount": 16.00, "total": 116.00, "lines": []}
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("factura_z")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "factura_001.jpg",
            },
            headers={"X-User-Token": "legacy-token-123"},
        )
    
    assert response.status_code == 200


def test_digitize_session_priority_over_token(make_settings, monkeypatch):
    """X-Odoo-Session tiene prioridad sobre X-User-Token."""
    settings = make_settings()
    
    mock_response = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.92,
        "extracted": {"supplier_name": "Test", "currency": "VES", "subtotal": 100.00, "iva_amount": 16.00, "total": 116.00, "lines": []}
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("factura_z")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
                "filename": "factura_001.jpg",
            },
            headers={
                "X-Odoo-Session": "session-priority",
                "X-User-Token": "should-be-ignored",
            },
        )
    
    assert response.status_code == 200


def test_digitize_missing_image_base64(make_settings, monkeypatch):
    """Request sin image_base64 → 422 (FastAPI validation error)."""
    settings = make_settings()
    mock_response = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.92,
        "extracted": {"supplier_name": "Test", "currency": "VES"}
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "filename": "factura_001.jpg",
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 422


def test_digitize_missing_filename(make_settings, monkeypatch):
    """Request sin filename → 422 (FastAPI validation error)."""
    settings = make_settings()
    mock_response = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.92,
        "extracted": {"supplier_name": "Test", "currency": "VES"}
    })
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", _make_mock_chatopenai(mock_response))
    
    app = make_app_with_odoo_transport(settings)
    client = TestClient(app)
    img_b64 = make_fake_invoice_image("factura_z")
    
    with client:
        response = client.post(
            "/digitize",
            json={
                "image_base64": img_b64,
            },
            headers={"X-Odoo-Session": "valid-session"},
        )
    
    assert response.status_code == 422