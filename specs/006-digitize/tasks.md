# Tasks — Spec 006: Endpoint /digitize con LLM Vision

> Checklist ejecutable. Marcar `[x]` al completar. Orden TDD: tests primero (F5), luego implementación (F2–F4), luego tests API (F6), validación final (F7).

---

## F1: Preparación y configuración

- [x] **T01** — Añadir `digitize_timeout_seconds` y `digitize_max_image_mb` a `Settings` (`src/contafis_assistant/config.py`)
- [x] **T02** — Crear `tests/unit/test_digitize.py`
- [x] **T03** — Crear `tests/api/test_digitize.py`
- [x] **T04** — Añadir fixture `make_fake_invoice_image` y mocks NIM a `tests/api/conftest.py`

## F2: Modelos Pydantic (domain)

- [x] **T05** — Crear `src/contafis_assistant/domain/digitize.py` con modelos
- [x] **T06** — Exportar en `domain/__init__.py` y `api/__init__.py`
- [x] **T07** — Validaciones Pydantic (pattern filename, confidence 0-1, enums)

## F3: Service layer — DigitizeService

- [x] **T08** — Crear `src/contafis_assistant/api/digitize.py` con `DigitizeService`
- [x] **T09** — Implementar `_build_messages()`: system + few-shots + user con image_url
- [x] **T10** — Implementar `_call_nim_vision()`: ChatOpenAI con response_format=json_object, timeout, error handling
- [x] **T11** — Implementar `_validate_rif()` (módulo 11) y `_validate_amounts()` (coherencia)
- [x] **T12** — Ensamblar `DigitizeResponse` con warnings

## F4: Endpoint /digitize en routes

- [x] **T13** — Importar modelos y service en `routes.py`
- [x] **T14** — Añadir `POST /digitize` con auth `get_user_context_from_session`
- [x] **T15** — Registrar factory `DigitizeService` en `main.py` / `ChatRuntime`

## F5: Tests unitarios (TDD — escribir ANTES de implementar)

- [x] **T16** — `test_validate_rif_valid_j` / `test_validate_rif_valid_v` / `test_validate_rif_valid_e` / `test_validate_rif_valid_g`
- [x] **T17** — `test_validate_rif_invalid_checksum` / `test_validate_rif_invalid_format`
- [x] **T18** — `test_validate_amounts_ok` / `test_validate_amounts_mismatch` / `test_validate_amounts_no_igtf`
- [x] **T19** — `test_parse_llm_response_valid` / `test_parse_llm_response_missing_fields`
- [x] **T20** — `test_build_messages_structure`
- [x] **T21** — `test_digitize_request_validation`
- [x] **T22** — `pytest tests/unit/test_digitize.py -v` → 30 passed

## F6: Tests de API

- [x] **T23** — `test_digitize_factura_z_success`
- [x] **T24** — `test_digitize_nota_credito_success`
- [x] **T25** — `test_digitize_nota_debito_success`
- [x] **T26** — `test_digitize_low_confidence`
- [x] **T27** — `test_digitize_invalid_rif_warning`
- [x] **T28** — `test_digitize_amount_mismatch_warning`
- [x] **T29** — Tests error: sin auth (401), base64 inválido (422), extensión inválida (422), NIM timeout (503), auto-detect type, legacy token, prioridad session
- [x] **T30** — `pytest tests/api/test_digitize.py -v` → 16 passed

## F7: Validación y regresión

- [x] **T31** — `pytest tests/api/ tests/integration/ tests/acceptance/ -v` → 178 passed
- [x] **T32** — Suite completa verde, `/tdd-check` pasa (validación TDD)

---

## Referencias rápidas

### Validación RIF (Módulo 11) — Python

```python
def validate_rif(rif: str) -> tuple[bool, str | None]:
    """Valida RIF venezolano con módulo 11.
    Returns: (True, None) si válido, (False, "mensaje de error") si inválido.
    """
    import re
    if not rif or not isinstance(rif, str):
        return False, "RIF vacío o no es string"
    
    rif_upper = rif.upper().strip()
    pattern = r'^[JVEGP]-\d{8}-\d$'
    if not re.match(pattern, rif_upper):
        return False, "Formato RIF inválido (esperado J-12345678-9)"
    
    try:
        tipo, base, digito = rif_upper.split('-')
    except ValueError:
        return False, "Formato RIF inválido (esperado 3 partes separadas por -)"
    
    tipo_map = {'J': 1, 'V': 2, 'E': 3, 'G': 4, 'P': 5}
    tipo_val = tipo_map.get(tipo)
    if tipo_val is None:
        return False, f"Tipo de RIF '{tipo}' no soportado"
    
    # 9 pesos para 8 dígitos de base + 1 dígito de tipo
    pesos = [4, 3, 2, 7, 6, 5, 4, 3, 2]
    digitos = [tipo_val] + [int(d) for d in base]
    
    if len(digitos) != len(pesos):
        return False, "Longitud de RIF base inválida"
    
    suma = sum(d * p for d, p in zip(digitos, pesos))
    resto = suma % 11
    digito_calculado = 11 - resto
    if digito_calculado == 10:
        digito_calculado = 0
    elif digito_calculado == 11:
        digito_calculado = 1
    
    if digito_calculado != int(digito):
        return False, f"Dígito de control inválido (esperado {digito_calculado}, recibido {digito})"
    
    return True, None
```

### Validación montos

```python
def validate_amounts(extracted: ExtractedInvoice) -> list[DigitizeWarning]:
    warnings: list[DigitizeWarning] = []
    if extracted.subtotal is not None and extracted.iva_amount is not None and extracted.total is not None:
        igtf = extracted.igtf_amount or 0.0
        calculated = extracted.subtotal + extracted.iva_amount + igtf
        if abs(calculated - extracted.total) > 0.01:
            warnings.append(DigitizeWarning(
                code="amount_mismatch",
                message=f"subtotal + iva + igtf = {calculated:.2f} != total {extracted.total:.2f}",
                field="total",
                extracted_value=str(extracted.total),
            ))
    return warnings
```

### Mock NIM Vision en conftest.py

```python
def mock_nim_vision_success(document_type="factura_z", confidence=0.92, extracted=None):
    """Mock handler para NIM vision exitoso."""
    if extracted is None:
        extracted = {
            "supplier_name": "Distribuidora El Sol C.A.",
            "supplier_rif": "J-12345678-1",
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
    
    def handler(request: httpx.Request) -> httpx.Response:
        if "chat/completions" in str(request.url):
            return httpx.Response(200, json={
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "document_type": document_type,
                            "confidence": confidence,
                            "extracted": extracted
                        }, ensure_ascii=False)
                    }
                }]
            })
        return httpx.Response(200, json=[])
    return handler
```

### Fixture imagen sintética PIL

```python
def make_fake_invoice_image(document_type="factura_z") -> str:
    """Genera imagen sintética PIL y retorna base64."""
    from PIL import Image, ImageDraw
    import io, base64
    img = Image.new('RGB', (800, 600), color='white')
    draw = ImageDraw.Draw(img)
    text = f"FACTURA {document_type.upper()}\nRIF J-12345678-1\nTOTAL 119.00"
    draw.text((50, 50), text, fill='black')
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return base64.b64encode(buf.getvalue()).decode()
```

---

## Archivos creados/modificados

- `src/contafis_assistant/config.py` (settings digitize)
- `src/contafis_assistant/domain/digitize.py` (nuevo - modelos + validaciones)
- `src/contafis_assistant/domain/__init__.py` (export)
- `src/contafis_assistant/api/digitize.py` (nuevo - service)
- `src/contafis_assistant/api/routes.py` (endpoint /digitize)
- `src/contafis_assistant/api/__init__.py` (export)
- `tests/unit/test_digitize.py` (nuevo - 30 tests)
- `tests/api/test_digitize.py` (nuevo - 16 tests)
- `tests/api/conftest.py` (mocks NIM + fixture imagen sintética)