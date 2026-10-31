"""Servicio de digitalización de facturas con LLM Vision (Spec 006).

Usa NVIDIA NIM (llama-3.2-90b-vision-instruct) via API OpenAI-compatible.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_openai import ChatOpenAI

from contafis_assistant.config import Settings
from contafis_assistant.domain.digitize import (
    DigitizeRequest,
    DigitizeResponse,
    ExtractedInvoice,
    ExtractedLine,
    add_low_confidence_warning,
    add_missing_field_warnings,
    add_rif_warning,
    validate_amounts,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Prompts
# ============================================================================

SYSTEM_PROMPT = """Eres un extractor experto de facturas venezolanas (tipo Z, notas de crédito/débito).
Recibes una imagen y devuelves SOLO un objeto JSON válido con los campos especificados.
NO incluyas prosa, explicaciones, ni markdown. Solo el JSON.

Reglas críticas:
1. RIF venezolano: formato J-12345678-9, V-12345678-9, E-12345678-9, G-12345678-9 (módulo 11).
2. Fechas en YYYY-MM-DD.
3. Montos en número decimal (punto decimal), sin separadores de miles.
4. IVA estándar 16% (0.16), IGTF 3% (0.03) si aplica.
5. Si un campo no es legible, usa null. NO inventes.
6. `document_type`: "factura_z", "nota_credito" o "nota_debito".
7. Para nota_credito/debito: incluir `credit_note_ref` o `debit_note_ref` con número de factura original.
8. `confidence`: 0.0–1.0 según legibilidad global de la imagen.
"""

FEWSHOT_FACTURA_Z = {
    "role": "user",
    "content": "Extrae los datos de esta factura tipo Z.",
}
FEWSHOT_FACTURA_Z_RESPONSE = {
    "role": "assistant",
    "content": json.dumps({
        "document_type": "factura_z",
        "confidence": 0.95,
        "extracted": {
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
            "lines": [{"description": "Servicios contables oct 2026", "quantity": 1, "unit_price": 100.00, "total": 100.00}],
            "credit_note_ref": None,
            "debit_note_ref": None,
        }
    }, ensure_ascii=False),
}

FEWSHOT_NOTA_CREDITO = {
    "role": "user",
    "content": "Extrae los datos de esta nota de crédito.",
}
FEWSHOT_NOTA_CREDITO_RESPONSE = {
    "role": "assistant",
    "content": json.dumps({
        "document_type": "nota_credito",
        "confidence": 0.92,
        "extracted": {
            "supplier_name": "Distribuidora El Sol C.A.",
            "supplier_rif": "J-12345678-9",
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
    }, ensure_ascii=False),
}

FEWSHOT_NOTA_DEBITO = {
    "role": "user",
    "content": "Extrae los datos de esta nota de débito.",
}
FEWSHOT_NOTA_DEBITO_RESPONSE = {
    "role": "assistant",
    "content": json.dumps({
        "document_type": "nota_debito",
        "confidence": 0.90,
        "extracted": {
            "supplier_name": "Distribuidora El Sol C.A.",
            "supplier_rif": "J-12345678-9",
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
    }, ensure_ascii=False),
}


# ============================================================================
# Excepciones
# ============================================================================

class DigitizeError(Exception):
    """Error base de digitalización."""
    pass


class NIMError(DigitizeError):
    """Error llamando a NIM vision."""
    pass


class NIMTimeoutError(NIMError):
    """Timeout llamando a NIM."""
    pass


class NIMParsingError(NIMError):
    """Respuesta de NIM no es JSON válido."""
    pass


# ============================================================================
# Servicio principal
# ============================================================================

class DigitizeService:
    """Servicio de digitalización de facturas con LLM Vision."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._llm: ChatOpenAI | None = None
    
    def _get_llm(self) -> ChatOpenAI:
        """Inicializa ChatOpenAI para NIM vision (lazy)."""
        if self._llm is None:
            self._llm = ChatOpenAI(
                model=self.settings.llm_model,  # llama-3.2-90b-vision-instruct
                api_key=self.settings.openai_api_key.get_secret_value(),
                base_url=self.settings.openai_base_url,
                timeout=self.settings.digitize_timeout_seconds,
                max_retries=1,
                model_kwargs={
                    "response_format": {"type": "json_object"},
                },
            )
        return self._llm
    
    def _build_messages(self, request: DigitizeRequest) -> list[dict[str, Any]]:
        """Construye lista de messages para LLM Vision.
        
        Estructura:
        1. System prompt
        2. Few-shot 1: Factura Z (user + assistant)
        3. Few-shot 2: Nota de crédito (user + assistant)
        4. Few-shot 3: Nota de débito (user + assistant)
        5. User final con imagen
        """
        # Determinar MIME type desde extensión
        ext = request.filename.lower().split('.')[-1]
        mime_map = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'webp': 'image/webp',
        }
        mime_type = mime_map.get(ext, 'image/jpeg')
        image_url = f"data:{mime_type};base64,{request.image_base64}"
        
        # Prompt de usuario final
        if request.document_type == "auto":
            user_text = "Extrae los datos de esta factura venezolana. Detecta automáticamente si es factura Z, nota de crédito o nota de débito."
        else:
            type_labels = {
                "factura_z": "factura tipo Z",
                "nota_credito": "nota de crédito",
                "nota_debito": "nota de débito",
            }
            label = type_labels.get(request.document_type, "documento")
            user_text = f"Extrae los datos de esta {label}."
        
        user_message = {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
        
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            FEWSHOT_FACTURA_Z,
            FEWSHOT_FACTURA_Z_RESPONSE,
            FEWSHOT_NOTA_CREDITO,
            FEWSHOT_NOTA_CREDITO_RESPONSE,
            FEWSHOT_NOTA_DEBITO,
            FEWSHOT_NOTA_DEBITO_RESPONSE,
            user_message,
        ]
    
    def _call_nim_vision(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Llama a NIM vision y parsea respuesta JSON.
        
        Returns:
            Dict con document_type, confidence, extracted.
            
        Raises:
            NIMTimeoutError: Timeout en la llamada.
            NIMParsingError: Respuesta no es JSON válido.
            NIMError: Otros errores de NIM.
        """
        llm = self._get_llm()
        
        try:
            response = llm.invoke(messages)
        except Exception as e:
            logger.exception("Error llamando a NIM vision")
            # Detectar timeout
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                raise NIMTimeoutError(f"Timeout llamando a NIM: {e}") from e
            raise NIMError(f"Error llamando a NIM: {e}") from e
        
        content = response.content
        if not content or not isinstance(content, str):
            raise NIMParsingError("Respuesta vacía o no string de NIM")
        
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error("Respuesta NIM no es JSON válido: %s", content[:500])
            raise NIMParsingError(f"Respuesta NIM no es JSON válido: {e}") from e
        
        # Validar estructura mínima
        required_keys = {"document_type", "confidence", "extracted"}
        if not all(k in parsed for k in required_keys):
            raise NIMParsingError(f"Respuesta NIM faltan claves requeridas: {required_keys - set(parsed.keys())}")
        
        return parsed
    
    def _parse_extracted(self, extracted_dict: dict[str, Any]) -> ExtractedInvoice:
        """Parsea dict extraído a ExtractedInvoice, tolerando campos faltantes."""
        # Asegurar lines es lista
        lines_data = extracted_dict.get("lines", [])
        if not isinstance(lines_data, list):
            lines_data = []
        
        lines = []
        for line_data in lines_data:
            if isinstance(line_data, dict):
                lines.append(ExtractedLine(
                    description=line_data.get("description", ""),
                    quantity=float(line_data.get("quantity", 0) or 0),
                    unit_price=float(line_data.get("unit_price", 0) or 0),
                    total=float(line_data.get("total", 0) or 0),
                ))
        
        return ExtractedInvoice(
            supplier_name=extracted_dict.get("supplier_name"),
            supplier_rif=extracted_dict.get("supplier_rif"),
            invoice_number=extracted_dict.get("invoice_number"),
            control_number=extracted_dict.get("control_number"),
            invoice_date=extracted_dict.get("invoice_date"),
            due_date=extracted_dict.get("due_date"),
            currency=extracted_dict.get("currency", "VES"),
            subtotal=extracted_dict.get("subtotal"),
            iva_rate=extracted_dict.get("iva_rate"),
            iva_amount=extracted_dict.get("iva_amount"),
            igtf_rate=extracted_dict.get("igtf_rate"),
            igtf_amount=extracted_dict.get("igtf_amount"),
            total=extracted_dict.get("total"),
            lines=lines,
            credit_note_ref=extracted_dict.get("credit_note_ref"),
            debit_note_ref=extracted_dict.get("debit_note_ref"),
        )
    
    def digitize(self, request: DigitizeRequest) -> DigitizeResponse:
        """Ejecuta digitalización completa: LLM → parseo → validaciones → response."""
        # 1. Construir messages
        messages = self._build_messages(request)
        
        # 2. Llamar NIM vision
        try:
            parsed = self._call_nim_vision(messages)
        except NIMTimeoutError:
            raise
        except NIMParsingError:
            raise
        except NIMError:
            raise
        
        # 3. Parsear extracted
        extracted = self._parse_extracted(parsed.get("extracted", {}))
        
        # 4. Confidence
        confidence = float(parsed.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))  # clamp 0-1
        
        # 5. Document type (validar que sea uno de los esperados)
        doc_type = parsed.get("document_type", "factura_z")
        if doc_type not in ("factura_z", "nota_credito", "nota_debito"):
            doc_type = "factura_z"  # fallback
        
        # 6. Validaciones post-LLM
        warnings: list[Any] = []
        
        # RIF validation
        add_rif_warning(extracted, warnings)
        
        # Amounts validation
        warnings.extend(validate_amounts(extracted))
        
        # Low confidence
        add_low_confidence_warning(confidence, warnings)
        
        # Missing critical fields
        add_missing_field_warnings(extracted, warnings)
        
        # 7. Construir response
        return DigitizeResponse(
            document_type=doc_type,
            confidence=confidence,
            extracted=extracted,
            warnings=warnings,
        )


def get_digitize_service(settings: Settings) -> DigitizeService:
    """Factory para DigitizeService (inyectable en tests)."""
    return DigitizeService(settings)


__all__ = [
    "DigitizeService",
    "get_digitize_service",
    "DigitizeError",
    "NIMError",
    "NIMTimeoutError",
    "NIMParsingError",
]