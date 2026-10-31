# Plan de Implementación — Spec 006: Endpoint /digitize con LLM Vision

## Fases

| Fase | Descripción | Tareas |
|---|---|---|
| **F1** | Preparación y configuración | T01–T04 |
| **F2** | Modelos Pydantic (domain) | T05–T07 |
| **F3** | Service layer: DigitizeService | T08–T12 |
| **F4** | Endpoint /digitize en routes | T13–T15 |
| **F5** | Tests unitarios (TDD primero) | T16–T22 |
| **F6** | Tests de API | T23–T29 |
| **F7** | Validación y regresión | T30–T32 |

---

## Detalle de tareas

### F1: Preparación y configuración

**T01** — Añadir settings `digitize_timeout_seconds` y `digitize_max_image_mb` a `Settings` (`config.py`)

**T02** — Crear estructura de tests
- `tests/unit/test_digitize.py`
- `tests/api/test_digitize.py`

**T03** — Añadir fixture `make_fake_invoice_image` a `tests/api/conftest.py` (PIL sintético)

**T04** — Añadir mock de NIM vision a `tests/api/conftest.py`
- `mock_nim_vision_success(document_type, confidence, extracted_dict)`
- `mock_nim_vision_low_confidence()`
- `mock_nim_vision_timeout()`
- `mock_nim_vision_invalid_json()`

---

### F2: Modelos Pydantic (domain)

**T05** — Crear `domain/digitize.py` con:
- `ExtractedLine`
- `ExtractedInvoice`
- `DigitizeWarning`
- `DigitizeRequest`
- `DigitizeResponse`

**T06** — Exportar en `domain/__init__.py` y `api/__init__.py`

**T07** — Validaciones Pydantic en modelos (pattern filename, confidence 0-1, etc.)

---

### F3: Service layer — DigitizeService

**T08** — Crear `api/digitize.py` con clase `DigitizeService`
- `__init__(settings, nim_client)` — inyecta config y cliente LLM
- `digitize(request: DigitizeRequest) -> DigitizeResponse` — método principal

**T09** — Implementar `_build_messages(request)` 
- System prompt + 3 few-shots + user message con `image_url: data:image/jpeg;base64,...`

**T10** — Implementar `_call_nim_vision(messages)` 
- `ChatOpenAI` con `model="llama-3.2-90b-vision-instruct"`, `response_format={"type": "json_object"}`, timeout
- Manejo errores: timeout, 5xx, JSON inválido → lanzar `DigitizeError` / `NIMError`

**T11** — Implementar validaciones post-LLM
- `_validate_rif(rif)` → módulo 11
- `_validate_amounts(extracted)` → coherencia subtotal+iva+igtf≈total

**T12** — Ensamblar `DigitizeResponse` con warnings agregados

---

### F4: Endpoint /digitize en routes

**T13** — Importar `DigitizeRequest`, `DigitizeResponse`, `DigitizeService` en `routes.py`

**T14** — Añadir endpoint `POST /digitize`
- Dependency `get_user_context_from_session` (Spec 005)
- Validar request con `DigitizeRequest`
- Instanciar `DigitizeService` (factory o inyectado en runtime)
- `run_in_threadpool(service.digitize, request)` (código síncrono LLM)

**T15** — Registrar factory de `DigitizeService` en `main.py` / `ChatRuntime`

---

### F5: Tests unitarios (TDD — escribir ANTES de implementar)

**T16** — `test_validate_rif_valid_j` / `test_validate_rif_valid_v` / `test_validate_rif_invalid_*`

**T17** — `test_validate_amounts_ok` / `test_validate_amounts_mismatch` / `test_validate_amounts_no_igtf`

**T18** — `test_parse_llm_response_valid` / `test_parse_llm_response_missing_fields`

**T19** — `test_build_messages_structure` (system + few-shots + user con image)

**T20** — `test_digitize_service_integration` (mock NIM → response completa)

**T21** — `test_digitize_request_validation` (filename pattern, base64, document_type enum)

**T22** — `pytest tests/unit/test_digitize.py -v` → verde

---

### F6: Tests de API

**T23** — `test_digitize_factura_z_success` (mock NIM + auth session)

**T24** — `test_digitize_nota_credito_success` (montos negativos + ref)

**T25** — `test_digitize_nota_debito_success` (montos positivos + ref)

**T26** — `test_digitize_low_confidence` (warning low_confidence)

**T27** — `test_digitize_invalid_rif_warning` (RIF extraído inválido → warning)

**T28** — `test_digitize_amount_mismatch_warning` (montos incoherentes → warning)

**T29** — Tests de error: sin auth (401), base64 inválido (400), extensión inválida (400), NIM timeout (503), auto-detect type

**T30** — `pytest tests/api/test_digitize.py -v` → verde

---

### F7: Validación y regresión

**T31** — `pytest tests/unit/ tests/api/ tests/integration/ tests/acceptance/ -v` → suite completa verde

**T32** — `/tdd-check` pasa

---

## Orden de ejecución (TDD estricto)

1. **T01–T04** (setup + mocks + fixtures)
2. **T16–T21** (tests unitarios RED)
3. **T05–T07** (modelos GREEN)
4. **T08–T12** (service GREEN)
5. **T13–T15** (endpoint GREEN)
6. **T23–T30** (tests API GREEN)
7. **T31–T32** (validación final)

---

## Criterios de done por fase

| Fase | Done cuando |
|---|---|
| F1 | Settings actualizados, archivos de test creados, mocks y fixtures en conftest |
| F2 | Modelos Pydantic definidos, validaciones integradas, exportados |
| F3 | `DigitizeService` funcional: build messages, llama NIM, parsea, valida RIF/montos, tests unitarios pasan |
| F4 | Endpoint `/digitize` expuesto, usa auth session, integra service, maneja errores HTTP |
| F5 | Tests unitarios cubren todos los AC de sección 11 (unit level) |
| F6 | Tests API cubren todos los AC de sección 11 (integration level) |
| F7 | Suite completa verde, `/tdd-check` pasa |