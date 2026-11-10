"""Modelos Pydantic para digitalización de facturas (Spec 006)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ============================================================================
# Modelos de request/response
# ============================================================================

class DigitizeRequest(BaseModel):
    """Request para endpoint /digitize."""
    
    image_base64: str = Field(
        ...,
        description="Imagen codificada en base64 (sin prefix data:image/...;)",
        min_length=1,
    )
    filename: str = Field(
        ...,
        description="Nombre del archivo con extensión",
        pattern=r".*\.(jpg|jpeg|png|webp)$",
    )
    document_type: Literal["factura_z", "nota_credito", "nota_debito", "auto"] = Field(
        default="auto",
        description="Tipo de documento esperado (auto = detección automática)",
    )
    
    @field_validator("image_base64")
    @classmethod
    def validate_base64(cls, v: str) -> str:
        """Valida que sea base64 decodificable."""
        try:
            import base64
            base64.b64decode(v, validate=True)
        except Exception as e:
            raise ValueError("image_base64 no es base64 válido") from e
        return v


class ExtractedLine(BaseModel):
    """Línea de factura extraída."""
    
    description: str = Field(..., description="Descripción del ítem")
    quantity: float = Field(..., description="Cantidad")
    unit_price: float = Field(..., description="Precio unitario")
    total: float = Field(..., description="Total de la línea")


class ExtractedInvoice(BaseModel):
    """Factura extraída estructurada."""
    
    supplier_name: str | None = Field(default=None, description="Nombre del proveedor")
    supplier_rif: str | None = Field(default=None, description="RIF del proveedor (J-12345678-9)")
    invoice_number: str | None = Field(default=None, description="Número de factura")
    control_number: str | None = Field(default=None, description="Número de control fiscal")
    invoice_date: str | None = Field(default=None, description="Fecha factura (YYYY-MM-DD)")
    due_date: str | None = Field(default=None, description="Fecha vencimiento (YYYY-MM-DD)")
    currency: Literal["VES", "USD"] = Field(default="VES", description="Moneda")
    subtotal: float | None = Field(default=None, description="Subtotal (base imponible)")
    iva_rate: float | None = Field(default=None, description="Tasa IVA (ej: 0.16)")
    iva_amount: float | None = Field(default=None, description="Monto IVA")
    igtf_rate: float | None = Field(default=None, description="Tasa IGTF (ej: 0.03)")
    igtf_amount: float | None = Field(default=None, description="Monto IGTF")
    total: float | None = Field(default=None, description="Total factura")
    lines: list[ExtractedLine] = Field(default_factory=list, description="Líneas de detalle")
    credit_note_ref: str | None = Field(default=None, description="Ref factura original (nota crédito)")
    debit_note_ref: str | None = Field(default=None, description="Ref factura original (nota débito)")


class DigitizeWarning(BaseModel):
    """Advertencia estructurada de digitalización."""
    
    code: Literal[
        "low_confidence",
        "invalid_rif",
        "amount_mismatch",
        "missing_field",
        "unknown_document_type",
        "parsing_error",
    ] = Field(..., description="Código de warning")
    message: str = Field(..., description="Mensaje legible")
    field: str | None = Field(default=None, description="Campo afectado")
    extracted_value: str | None = Field(default=None, description="Valor extraído que causó el warning")


class DigitizeResponse(BaseModel):
    """Response del endpoint /digitize."""
    
    document_type: Literal["factura_z", "nota_credito", "nota_debito"] = Field(
        ..., description="Tipo de documento detectado"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confianza global 0.0-1.0"
    )
    extracted: ExtractedInvoice = Field(..., description="Datos extraídos")
    warnings: list[DigitizeWarning] = Field(default_factory=list, description="Advertencias")


# ============================================================================
# Batch (hasta 20 imágenes por turno)
# ============================================================================

DIGITIZE_BATCH_MAX_IMAGES = 20


class DigitizeBatchItemResult(BaseModel):
    """Resultado de un recibo dentro del lote.

    Un fallo aislado (timeout de NIM, JSON ilegible) no invalida el resto del
    lote: cada ítemReporta su propio estado.
    """

    index: int = Field(..., ge=0, description="Posición en el lote (0-based)")
    filename: str = Field(..., description="Nombre de archivo enviado")
    status: Literal["ok", "error"] = Field(
        ..., description="'ok' si se extrajo, 'error' si falló"
    )
    result: DigitizeResponse | None = Field(
        default=None, description="Datos extraídos cuando status='ok'"
    )
    error: str | None = Field(
        default=None, description="Mensaje de error cuando status='error'"
    )


class DigitizeBatchRequest(BaseModel):
    """Lote de imágenes para digitalizar en un solo turno."""

    images: list[DigitizeRequest] = Field(
        ...,
        min_length=1,
        max_length=DIGITIZE_BATCH_MAX_IMAGES,
        description=f"Entre 1 y {DIGITIZE_BATCH_MAX_IMAGES} imágenes",
    )


class DigitizeBatchResponse(BaseModel):
    """Resumen del lote más el resultado por imagen (en el orden enviado)."""

    total: int = Field(..., ge=0, description="Cantidad de imágenes recibidas")
    succeeded: int = Field(..., ge=0, description="Cantidad digitalizada correctamente")
    failed: int = Field(..., ge=0, description="Cantidad que falló")
    results: list[DigitizeBatchItemResult] = Field(
        default_factory=list, description="Un resultado por imagen, en orden"
    )


# ============================================================================
# Validaciones post-LLM (deterministas)
# ============================================================================

def validate_rif(rif: str) -> tuple[bool, str | None]:
    """Valida RIF venezolano con algoritmo módulo 11.
    
    Args:
        rif: RIF en formato J-12345678-9, V-12345678-9, etc.
        
    Returns:
        (True, None) si válido, (False, "mensaje de error") si inválido.
    """
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


def validate_amounts(extracted: ExtractedInvoice) -> list[DigitizeWarning]:
    """Valida coherencia de montos: subtotal + iva + igtf ≈ total.
    
    Tolerancia: 0.01 (centavos).
    """
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


def add_rif_warning(extracted: ExtractedInvoice, warnings: list[DigitizeWarning]) -> None:
    """Añade warning de RIF inválido si aplica."""
    if extracted.supplier_rif:
        ok, msg = validate_rif(extracted.supplier_rif)
        if not ok:
            warnings.append(DigitizeWarning(
                code="invalid_rif",
                message=msg or "RIF inválido",
                field="supplier_rif",
                extracted_value=extracted.supplier_rif,
            ))


def add_low_confidence_warning(confidence: float, warnings: list[DigitizeWarning], threshold: float = 0.5) -> None:
    """Añade warning de baja confianza si confidence < threshold."""
    if confidence < threshold:
        warnings.append(DigitizeWarning(
            code="low_confidence",
            message=f"Confianza de extracción baja ({confidence:.2f}). Verificar campos manualmente.",
            field="confidence",
            extracted_value=str(confidence),
        ))


def add_missing_field_warnings(extracted: ExtractedInvoice, warnings: list[DigitizeWarning]) -> None:
    """Añade warnings por campos críticos faltantes."""
    critical_fields = {
        "supplier_name": "Nombre del proveedor",
        "supplier_rif": "RIF del proveedor",
        "invoice_number": "Número de factura",
        "invoice_date": "Fecha de factura",
        "total": "Total",
    }
    
    for field, label in critical_fields.items():
        value = getattr(extracted, field, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            warnings.append(DigitizeWarning(
                code="missing_field",
                message=f"Campo crítico faltante: {label}",
                field=field,
                extracted_value=str(value) if value is not None else None,
            ))


__all__ = [
    "DIGITIZE_BATCH_MAX_IMAGES",
    "DigitizeBatchItemResult",
    "DigitizeBatchRequest",
    "DigitizeBatchResponse",
    "DigitizeRequest",
    "DigitizeResponse",
    "ExtractedInvoice",
    "ExtractedLine",
    "DigitizeWarning",
    "validate_rif",
    "validate_amounts",
    "add_rif_warning",
    "add_low_confidence_warning",
    "add_missing_field_warnings",
]