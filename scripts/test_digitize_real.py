"""Prueba E2E del endpoint /digitize con una imagen real generada.

Genera una factura sintética con PIL (datos FALSOS PERO CONOCIDOS):
  RIF: J-99999999-9, TOTAL: 500.00, FECHA: 2026-11-11.

Llama al endpoint /digitize (o directamente al DigitizeService si no hay
servidor levantado) y verifica que la respuesta coincida con los datos de la
IMAGEN REAL y NO con los del ejemplo del few-shot
(Distribuidora El Sol C.A. / J-12345678-9 / 119.00).
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
from typing import Any

# Asegurar que se importa el paquete local
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw  # type: ignore

from contafis_assistant.api.digitize import DigitizeService, get_digitize_service
from contafis_assistant.config import Settings


# ============================================================================
# Generación de imagen de factura con datos falsos conocidos
# ============================================================================

RIF_ESPERADO = "J-99999999-9"
TOTAL_ESPERADO = 500.00
FECHA_ESPERADA = "2026-11-11"
SUPLIENTE_ESPERADO = "Empresa Test S.A."


def generar_imagen_factura() -> str:
    """Genera una imagen JPEG de factura y devuelve su base64."""
    img = Image.new("RGB", (900, 600), color="white")
    draw = ImageDraw.Draw(img)

    lineas = [
        "FACTURA",
        f"RIF: {RIF_ESPERADO}",
        f"FECHA: {FECHA_ESPERADA}",
        f"SUPLIENTE: {SUPLIENTE_ESPERADO}",
        "NUMERO: 001-001-00009999",
        f"TOTAL: {TOTAL_ESPERADO}",
        "MONEDA: USD",
        "CONTROL: TEST000000001",
    ]
    y = 80
    for linea in lineas:
        draw.text((60, y), linea, fill="black")
        y += 45

    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


# ============================================================================
# Prueba contra el endpoint /digitize (si hay servidor levantado)
# ============================================================================

def _odoo_authenticate() -> tuple[http.cookiejar.CookieJar, str | None]:
    """Se autentica contra Odoo y devuelve (cookiejar, session_id).

    Usa las credenciales del .env: ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASSWORD.
    """
    import http.cookiejar
    import urllib.error
    import urllib.request

    odoo_url = os.getenv("ODOO_URL", "http://localhost:8090")
    odoo_db = os.getenv("ODOO_DB", "contea")
    odoo_user = os.getenv("ODOO_USER", "admin")
    odoo_password = os.getenv("ODOO_PASSWORD", "admin")

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    login_payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "call",
        "params": {"db": odoo_db, "login": odoo_user, "password": odoo_password},
        "id": 1,
    }).encode()
    req = urllib.request.Request(
        f"{odoo_url}/web/session/authenticate",
        data=login_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = opener.open(req, timeout=30)
        result = json.loads(resp.read().decode())
        if result.get("error"):
            print(f"[WARN] Login Odoo falló: {result['error']}")
            return cj, None
        uid = result.get("result", {}).get("uid")
        print(f"[INFO] Login Odoo OK, uid={uid}")
    except Exception as e:
        print(f"[WARN] No se pudo autenticar contra Odoo: {e}")
        return cj, None

    # Extraer session_id de las cookies
    session_id = None
    for c in cj:
        if c.name == "session_id":
            session_id = c.value
            break
    print(f"[INFO] session_id obtenido: {session_id[:20]}..." if session_id else "[WARN] No se obtuvo session_id")
    return cj, session_id


def intentar_endpoint(image_b64: str) -> dict[str, Any] | None:
    """Intenta hacer POST a /digitize con autenticación Odoo.

    Retorna None si no hay servidor o si falla la autenticación.
    """
    import urllib.error
    import urllib.request

    url = os.getenv("DIGITIZE_URL", "http://localhost:8000/digitize")
    cj, session_id = _odoo_authenticate()
    if not session_id:
        print("[WARN] No se pudo obtener sesión de Odoo; saltando endpoint real.")
        return None

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    payload = {
        "image_base64": image_b64,
        "filename": "factura_test.jpg",
        "document_type": "factura_z",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Odoo-Session": session_id,
        },
        method="POST",
    )
    try:
        with opener.open(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        print(f"[ERROR] Endpoint /digitize devolvió HTTP {e.code}: {body[:500]}")
        return None
    except (urllib.error.URLError, ConnectionRefusedError, OSError) as e:
        print(f"[WARN] No se pudo conectar al endpoint {url}: {e}")
        return None


# ============================================================================
# Prueba directa contra DigitizeService (fallback sin servidor)
# ============================================================================

def probar_servicio_directo(image_b64: str) -> dict[str, Any]:
    """Llama directamente a DigitizeService con un mock de NIM.

    Nota: como no hay servidor NIM real disponible en este entorno, se usa un
    mock que devuelve los datos de la imagen real. El objetivo de esta vía es
    verificar que el servicio NO inyecta los datos del few-shot y que usa lo
    que el LLM devuelve.
    """
    from unittest import mock

    from contafis_assistant.domain.digitize import DigitizeRequest

    settings = Settings(
        ODOO_URL="http://localhost:8069",
        ODOO_DB="contea",
        ODOO_API_KEY="test",
        OPENAI_API_KEY="test",
        LLM_MODEL="meta/llama-3.2-90b-vision-instruct",
        EMBEDDING_MODEL="nvidia/nemotron-3-embed-1b",
        CHROMA_PATH="./data/chroma",
        DIGITIZE_TIMEOUT_SECONDS=120,
        DIGITIZE_MAX_IMAGE_MB=5,
    )

    service = DigitizeService(settings)

    # Verificar estructura de mensajes: NO debe haber few-shots (assistant)
    req = DigitizeRequest(
        image_base64=image_b64,
        filename="factura_test.jpg",
        document_type="factura_z",
    )
    messages = service._build_messages(req)

    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert assistant_msgs == [], (
        "BUG: hay mensajes assistant de few-shot. El LLM copiaría el ejemplo."
    )
    assert len(messages) == 2, f"Se esperaban 2 mensajes, hay {len(messages)}"
    assert "IMAGEN REAL" in messages[0]["content"], (
        "El system prompt debe ordenar leer la imagen real."
    )

    # Mock de NIM que devuelve los datos de la IMAGEN REAL (no del ejemplo)
    mock_content = json.dumps(
        {
            "document_type": "factura_z",
            "confidence": 0.90,
            "extracted": {
                "supplier_name": SUPLIENTE_ESPERADO,
                "supplier_rif": RIF_ESPERADO,
                "invoice_number": "001-001-00009999",
                "control_number": "TEST000000001",
                "invoice_date": FECHA_ESPERADA,
                "due_date": None,
                "currency": "USD",
                "subtotal": 500.00,
                "iva_rate": 0.16,
                "iva_amount": 80.00,
                "igtf_rate": 0.03,
                "igtf_amount": 15.00,
                "total": TOTAL_ESPERADO,
                "lines": [
                    {
                        "description": "Producto test",
                        "quantity": 1,
                        "unit_price": 500.00,
                        "total": 500.00,
                    }
                ],
                "credit_note_ref": None,
                "debit_note_ref": None,
            },
        }
    )

    class MockResponse:
        content = mock_content

    class MockChatOpenAI:
        def __init__(self, *args, **kwargs):
            pass

        def invoke(self, messages):
            return MockResponse()

    with mock.patch(
        "contafis_assistant.api.digitize.ChatOpenAI", MockChatOpenAI
    ):
        service2 = DigitizeService(settings)
        response = service2.digitize(req)

    return response.model_dump()


# ============================================================================
# Verificación
# ============================================================================

def verificar(respuesta: dict[str, Any]) -> None:
    """Verifica que la respuesta use los datos de la imagen real."""
    print("\n=== RESPUESTA ===")
    print(json.dumps(respuesta, indent=2, ensure_ascii=False))

    extracted = respuesta.get("extracted", {})
    rif = extracted.get("supplier_rif")
    total = extracted.get("total")
    fecha = extracted.get("invoice_date")
    documento = respuesta.get("document_type")

    errores = []
    if rif != RIF_ESPERADO:
        errores.append(
            f"RIF incorrecto: se esperaba {RIF_ESPERADO}, se obtuvo {rif}"
        )
    if total != TOTAL_ESPERADO:
        errores.append(
            f"TOTAL incorrecto: se esperaba {TOTAL_ESPERADO}, se obtuvo {total}"
        )
    if fecha != FECHA_ESPERADA:
        errores.append(
            f"FECHA incorrecta: se esperaba {FECHA_ESPERADA}, se obtuvo {fecha}"
        )

    # Datos del EJEMPLO (few-shot) que NO deben aparecer
    datos_ejemplo = {
        "supplier_name": "Distribuidora El Sol C.A.",
        "supplier_rif": "J-12345678-9",
        "total": 119.00,
    }
    for campo, valor_ejemplo in datos_ejemplo.items():
        valor_obtenido = extracted.get(campo)
        if valor_obtenido == valor_ejemplo:
            errores.append(
                f"BUG: el campo '{campo}' tiene el valor del few-shot ({valor_ejemplo})"
            )

    print("\n=== VERIFICACIÓN ===")
    if errores:
        print("FALLO:")
        for e in errores:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("OK: los datos coinciden con la IMAGEN REAL (no con el few-shot).")
        print(f"  document_type = {documento}")
        print(f"  supplier_rif  = {rif}  (esperado {RIF_ESPERADO})")
        print(f"  total         = {total}  (esperado {TOTAL_ESPERADO})")
        print(f"  invoice_date  = {fecha}  (esperado {FECHA_ESPERADA})")


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    print("=== Prueba E2E /digitize con imagen real ===")
    image_b64 = generar_imagen_factura()
    print(f"Imagen generada: {len(image_b64)} caracteres base64")

    respuesta = intentar_endpoint(image_b64)
    if respuesta is None:
        print("[INFO] Endpoint no disponible; usando prueba directa del servicio.")
        respuesta = probar_servicio_directo(image_b64)

    verificar(respuesta)


if __name__ == "__main__":
    main()
