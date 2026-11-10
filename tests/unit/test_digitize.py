"""Tests unitarios para digitalización de facturas (Spec 006).

Cubren: validación RIF módulo 11, validación coherencia montos, parsing LLM, request validation.
"""

from __future__ import annotations

import base64
import io
import json
import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from contafis_assistant.domain.digitize import (
    DigitizeRequest,
    DigitizeResponse,
    ExtractedInvoice,
    ExtractedLine,
    DigitizeWarning,
    validate_rif,
    validate_amounts,
)


# ============================================================================
# Fixtures
# ============================================================================

def make_fake_invoice_base64(document_type: str = "factura_z") -> str:
    """Genera imagen sintética PIL y retorna base64."""
    img = Image.new('RGB', (800, 600), color='white')
    draw = ImageDraw.Draw(img)
    text = f"FACTURA {document_type.upper()}\nRIF J-12345678-9\nTOTAL 119.00"
    draw.text((50, 50), text, fill='black')
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return base64.b64encode(buf.getvalue()).decode()


# ============================================================================
# Tests de validación RIF (Módulo 11) — T16, T17
# ============================================================================

def test_validate_rif_valid_j():
    """RIF J-12345678-1 válido → (True, None)."""
    ok, msg = validate_rif("J-12345678-1")
    assert ok is True
    assert msg is None


def test_validate_rif_valid_v():
    """RIF V-12345678-8 válido → (True, None)."""
    ok, msg = validate_rif("V-12345678-8")
    assert ok is True
    assert msg is None


def test_validate_rif_valid_e():
    """RIF E-12345678-4 válido → (True, None)."""
    ok, msg = validate_rif("E-12345678-4")
    assert ok is True
    assert msg is None


def test_validate_rif_valid_g():
    """RIF G-12345678-1 válido → (True, None)."""
    ok, msg = validate_rif("G-12345678-1")
    assert ok is True
    assert msg is None


def test_validate_rif_invalid_checksum():
    """RIF con checksum incorrecto → (False, mensaje)."""
    ok, msg = validate_rif("J-12345678-0")  # checksum wrong (válido es 1)
    assert ok is False
    assert "Dígito de control inválido" in msg


def test_validate_rif_invalid_format_no_dashes():
    """RIF sin guiones → (False, mensaje)."""
    ok, msg = validate_rif("123456789")
    assert ok is False
    assert "Formato RIF inválido" in msg


def test_validate_rif_invalid_format_wrong_type():
    """RIF con tipo inválido (X) → (False, mensaje)."""
    ok, msg = validate_rif("X-12345678-1")  # tipo X no válido
    assert ok is False
    assert "tipo de RIF" in msg.lower() or "formato rif inválido" in msg.lower()


def test_validate_rif_invalid_base_not_numeric():
    """RIF con base no numérica → (False, mensaje)."""
    ok, msg = validate_rif("J-ABCDEFGH-1")
    assert ok is False


def test_validate_rif_case_insensitive():
    """RIF en minúsculas → válido."""
    ok, msg = validate_rif("j-12345678-1")
    assert ok is True


# ============================================================================
# Tests de validación coherencia montos — T18
# ============================================================================

def test_validate_amounts_ok():
    """subtotal + iva + igtf = total → sin warnings."""
    extracted = ExtractedInvoice(
        subtotal=100.00,
        iva_amount=16.00,
        igtf_amount=3.00,
        total=119.00,
    )
    warnings = validate_amounts(extracted)
    assert warnings == []


def test_validate_amounts_ok_no_igtf():
    """Sin IGTF: subtotal + iva = total → sin warnings."""
    extracted = ExtractedInvoice(
        subtotal=100.00,
        iva_amount=16.00,
        igtf_amount=None,
        total=116.00,
    )
    warnings = validate_amounts(extracted)
    assert warnings == []


def test_validate_amounts_mismatch():
    """subtotal + iva + igtf ≠ total → warning amount_mismatch."""
    extracted = ExtractedInvoice(
        subtotal=100.00,
        iva_amount=16.00,
        igtf_amount=3.00,
        total=120.00,  # debería ser 119.00
    )
    warnings = validate_amounts(extracted)
    assert len(warnings) == 1
    assert warnings[0].code == "amount_mismatch"
    assert "119.00" in warnings[0].message
    assert "120.00" in warnings[0].message
    assert warnings[0].field == "total"


def test_validate_amounts_mismatch_small_tolerance():
    """Diferencia ≤ 0.01 → sin warning (tolerancia centavos)."""
    extracted = ExtractedInvoice(
        subtotal=100.00,
        iva_amount=16.00,
        igtf_amount=3.00,
        total=119.005,  # diferencia 0.005 < 0.01
    )
    warnings = validate_amounts(extracted)
    assert warnings == []


def test_validate_amounts_missing_fields():
    """Campos faltantes → sin warnings (no se puede validar)."""
    extracted = ExtractedInvoice(
        subtotal=100.00,
        iva_amount=None,
        total=119.00,
    )
    warnings = validate_amounts(extracted)
    assert warnings == []


# ============================================================================
# Tests de request validation — T21
# ============================================================================

def test_digitize_request_valid():
    """Request válido con todos los campos."""
    img_b64 = make_fake_invoice_base64()
    req = DigitizeRequest(
        image_base64=img_b64,
        filename="factura_001.jpg",
        document_type="factura_z",
    )
    assert req.image_base64 == img_b64
    assert req.filename == "factura_001.jpg"
    assert req.document_type == "factura_z"


def test_digitize_request_default_document_type():
    """document_type por defecto = auto."""
    img_b64 = make_fake_invoice_base64()
    req = DigitizeRequest(
        image_base64=img_b64,
        filename="factura_001.jpg",
    )
    assert req.document_type == "auto"


def test_digitize_request_invalid_extension_pdf():
    """Extensión .pdf → ValidationError."""
    img_b64 = make_fake_invoice_base64()
    with pytest.raises(Exception) as exc_info:
        DigitizeRequest(
            image_base64=img_b64,
            filename="factura_001.pdf",
        )
    assert "filename" in str(exc_info.value).lower() or "extension" in str(exc_info.value).lower()


def test_digitize_request_invalid_extension_txt():
    """Extensión .txt → ValidationError."""
    img_b64 = make_fake_invoice_base64()
    with pytest.raises(Exception):
        DigitizeRequest(
            image_base64=img_b64,
            filename="factura_001.txt",
        )


def test_digitize_request_valid_extensions():
    """Extensiones válidas: jpg, jpeg, png, webp."""
    img_b64 = make_fake_invoice_base64()
    for ext in ["jpg", "jpeg", "png", "webp"]:
        req = DigitizeRequest(
            image_base64=img_b64,
            filename=f"factura_001.{ext}",
        )
        assert req.filename.endswith(f".{ext}")


def test_digitize_request_invalid_document_type():
    """document_type inválido → ValidationError."""
    img_b64 = make_fake_invoice_base64()
    with pytest.raises(Exception):
        DigitizeRequest(
            image_base64=img_b64,
            filename="factura_001.jpg",
            document_type="factura_x",  # inválido
        )


# ============================================================================
# Tests de parsing LLM response — T19
# ============================================================================

def test_extracted_invoice_complete():
    """ExtractedInvoice con todos los campos poblados."""
    data = {
        "supplier_name": "Distribuidora El Sol C.A.",
        "supplier_rif": "J-12345678-9",
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
    extracted = ExtractedInvoice(**data)
    assert extracted.supplier_name == "Distribuidora El Sol C.A."
    assert extracted.supplier_rif == "J-12345678-9"
    assert extracted.currency == "USD"
    assert len(extracted.lines) == 1


def test_extracted_invoice_missing_optional_fields():
    """Campos opcionales faltantes → None."""
    data = {
        "supplier_name": "Test",
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
    }
    extracted = ExtractedInvoice(**data)
    assert extracted.supplier_rif is None
    assert extracted.currency == "VES"  # default
    assert extracted.lines == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("VES", "VES"),
        ("Bs", "VES"),
        ("Bs.", "VES"),
        ("Bs.S", "VES"),
        ("Bs. S.", "VES"),
        ("BSF", "VES"),
        ("BsV", "VES"),
        ("Bolívares", "VES"),
        (None, "VES"),
        ("USD", "USD"),
        ("usd", "USD"),
        ("US$", "USD"),
        ("$", "USD"),
        ("Dólares", "USD"),
    ],
)
def test_currency_normalization(raw, expected):
    """El LLM devuelve el símbolo impreso; el esquema debe normalizarlo."""
    invoice = ExtractedInvoice(currency=raw)
    assert invoice.currency == expected


def test_currency_unknown_still_fails():
    """Una moneda no soportada no se enmascara como VES."""
    with pytest.raises(ValidationError):
        ExtractedInvoice(currency="EUR")


def test_extracted_line():
    """ExtractedLine con todos los campos."""
    line = ExtractedLine(
        description="Servicio contable",
        quantity=2.0,
        unit_price=50.00,
        total=100.00,
    )
    assert line.quantity == 2.0
    assert line.total == 100.00


def test_digitize_warning_codes():
    """DigitizeWarning acepta todos los códigos válidos."""
    for code in ["low_confidence", "invalid_rif", "amount_mismatch", "missing_field", "unknown_document_type"]:
        warning = DigitizeWarning(code=code, message="test")
        assert warning.code == code


def test_digitize_response_structure():
    """DigitizeResponse estructura completa."""
    extracted = ExtractedInvoice(
        supplier_name="Test",
        supplier_rif="J-12345678-9",
        currency="VES",
    )
    response = DigitizeResponse(
        document_type="factura_z",
        confidence=0.95,
        extracted=extracted,
        warnings=[DigitizeWarning(code="low_confidence", message="Imagen borrosa")],
    )
    assert response.document_type == "factura_z"
    assert response.confidence == 0.95
    assert len(response.warnings) == 1


def test_digitize_confidence_bounds():
    """confidence debe estar entre 0.0 y 1.0."""
    extracted = ExtractedInvoice(supplier_name="Test")
    
    # Válido
    DigitizeResponse(document_type="factura_z", confidence=0.0, extracted=extracted)
    DigitizeResponse(document_type="factura_z", confidence=1.0, extracted=extracted)
    DigitizeResponse(document_type="factura_z", confidence=0.5, extracted=extracted)
    
    # Inválido
    with pytest.raises(Exception):
        DigitizeResponse(document_type="factura_z", confidence=-0.1, extracted=extracted)
    with pytest.raises(Exception):
        DigitizeResponse(document_type="factura_z", confidence=1.1, extracted=extracted)


# ============================================================================
# Tests de build messages structure — T20
# ============================================================================

def test_build_messages_includes_system_and_fewshots():
    """Estructura de messages para LLM: system + few-shots + user."""
    from contafis_assistant.api.digitize import DigitizeService
    from contafis_assistant.config import Settings
    
    settings = Settings(
        ODOO_URL="http://localhost:8069",
        ODOO_DB="contea",
        ODOO_API_KEY="test",
        OPENAI_API_KEY="test",
        LLM_MODEL="llama-3.2-90b-vision-instruct",
        EMBEDDING_MODEL="text-embedding-3-small",
        CHROMA_PATH="/tmp/chroma",
        DIGITIZE_TIMEOUT_SECONDS=30.0,
        DIGITIZE_MAX_IMAGE_MB=5,
    )
    
    service = DigitizeService(settings)
    req = DigitizeRequest(
        image_base64=make_fake_invoice_base64(),
        filename="test.jpg",
        document_type="factura_z",
    )
    
    messages = service._build_messages(req)
    
    # Debe tener: system + 3 few-shots (user+assistant c/u) + user final
    # system = 1 mensaje
    # 3 few-shots = 6 mensajes (user + assistant)
    # user final = 1 mensaje
    # Total = 8 mensajes
    assert len(messages) == 8
    
    # Primer mensaje = system
    assert messages[0]["role"] == "system"
    assert "extractor experto" in messages[0]["content"].lower()
    
    # Few-shots: user/assistant alternados
    for i in range(1, 7, 2):
        assert messages[i]["role"] == "user"
        assert messages[i+1]["role"] == "assistant"
        # Assistant debe ser JSON válido
        content = messages[i+1]["content"]
        parsed = json.loads(content)
        assert "document_type" in parsed
        assert "confidence" in parsed
        assert "extracted" in parsed
    
    # Último mensaje = user con imagen
    assert messages[-1]["role"] == "user"
    user_content = messages[-1]["content"]
    assert isinstance(user_content, list)
    # Debe tener text + image_url
    has_image = any(item.get("type") == "image_url" for item in user_content)
    assert has_image, "Último mensaje debe incluir image_url"
    
    image_url = next(item["image_url"]["url"] for item in user_content if item.get("type") == "image_url")
    assert image_url.startswith("data:image/jpeg;base64,")


# ============================================================================
# Test de integración service (mock NIM) — T20
# ============================================================================

def test_digitize_service_integration_mock(monkeypatch):
    """Integración completa: mock NIM → response parseada + validaciones."""
    from contafis_assistant.api.digitize import DigitizeService
    from contafis_assistant.config import Settings
    
    settings = Settings(
        ODOO_URL="http://localhost:8069",
        ODOO_DB="contea",
        ODOO_API_KEY="test",
        OPENAI_API_KEY="test",
        LLM_MODEL="llama-3.2-90b-vision-instruct",
        EMBEDDING_MODEL="text-embedding-3-small",
        CHROMA_PATH="/tmp/chroma",
        DIGITIZE_TIMEOUT_SECONDS=30.0,
        DIGITIZE_MAX_IMAGE_MB=5,
    )
    
    # Mock ChatOpenAI - usar RIF válido J-12345678-1
    mock_response_content = json.dumps({
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
    
    class MockChatOpenAI:
        def __init__(self, *args, **kwargs):
            pass
        
        def invoke(self, messages):
            class MockResponse:
                content = mock_response_content
            return MockResponse()
    
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", MockChatOpenAI)
    
    service = DigitizeService(settings)
    req = DigitizeRequest(
        image_base64=make_fake_invoice_base64(),
        filename="test.jpg",
        document_type="factura_z",
    )
    
    response = service.digitize(req)
    
    assert response.document_type == "factura_z"
    assert response.confidence == 0.92
    assert response.extracted.supplier_rif == "J-12345678-1"
    # RIF válido → no debe haber warning invalid_rif
    rif_warnings = [w for w in response.warnings if w.code == "invalid_rif"]
    assert len(rif_warnings) == 0
    # Montos coherentes → no warning amount_mismatch
    amount_warnings = [w for w in response.warnings if w.code == "amount_mismatch"]
    assert len(amount_warnings) == 0


def test_digitize_service_invalid_rif_warning(monkeypatch):
    """RIF extraído inválido → warning invalid_rif en response."""
    from contafis_assistant.api.digitize import DigitizeService
    from contafis_assistant.config import Settings
    
    settings = Settings(
        ODOO_URL="http://localhost:8069",
        ODOO_DB="contea",
        ODOO_API_KEY="test",
        OPENAI_API_KEY="test",
        LLM_MODEL="llama-3.2-90b-vision-instruct",
        EMBEDDING_MODEL="text-embedding-3-small",
        CHROMA_PATH="/tmp/chroma",
        DIGITIZE_TIMEOUT_SECONDS=30.0,
        DIGITIZE_MAX_IMAGE_MB=5,
    )
    
    mock_response_content = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.85,
        "extracted": {
            "supplier_name": "Test",
            "supplier_rif": "J-12345678-0",  # checksum inválido (válido es 1)
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
    
    class MockChatOpenAI:
        def __init__(self, *args, **kwargs):
            pass
        
        def invoke(self, messages):
            class MockResponse:
                content = mock_response_content
            return MockResponse()
    
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", MockChatOpenAI)
    
    service = DigitizeService(settings)
    req = DigitizeRequest(
        image_base64=make_fake_invoice_base64(),
        filename="test.jpg",
        document_type="factura_z",
    )
    
    response = service.digitize(req)
    
    rif_warnings = [w for w in response.warnings if w.code == "invalid_rif"]
    assert len(rif_warnings) == 1
    assert "J-12345678-0" in rif_warnings[0].extracted_value or "J-12345678-0" in rif_warnings[0].message


def test_digitize_service_amount_mismatch_warning(monkeypatch):
    """Montos incoherentes → warning amount_mismatch."""
    from contafis_assistant.api.digitize import DigitizeService
    from contafis_assistant.config import Settings
    
    settings = Settings(
        ODOO_URL="http://localhost:8069",
        ODOO_DB="contea",
        ODOO_API_KEY="test",
        OPENAI_API_KEY="test",
        LLM_MODEL="llama-3.2-90b-vision-instruct",
        EMBEDDING_MODEL="text-embedding-3-small",
        CHROMA_PATH="/tmp/chroma",
        DIGITIZE_TIMEOUT_SECONDS=30.0,
        DIGITIZE_MAX_IMAGE_MB=5,
    )
    
    mock_response_content = json.dumps({
        "document_type": "factura_z",
        "confidence": 0.90,
        "extracted": {
            "supplier_name": "Test",
            "supplier_rif": "J-12345678-1",  # válido
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
    
    class MockChatOpenAI:
        def __init__(self, *args, **kwargs):
            pass
        
        def invoke(self, messages):
            class MockResponse:
                content = mock_response_content
            return MockResponse()
    
    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", MockChatOpenAI)
    
    service = DigitizeService(settings)
    req = DigitizeRequest(
        image_base64=make_fake_invoice_base64(),
        filename="test.jpg",
        document_type="factura_z",
    )
    
    response = service.digitize(req)
    
    amount_warnings = [w for w in response.warnings if w.code == "amount_mismatch"]
    assert len(amount_warnings) == 1
    assert "119.00" in amount_warnings[0].message
    assert "120.00" in amount_warnings[0].message