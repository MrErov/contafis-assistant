"""Núcleo puro de dominio: contratos, tiempo, estados, dominios y agregación."""

from contafis_assistant.domain.digitize import (
    DigitizeRequest,
    DigitizeResponse,
    ExtractedInvoice,
    ExtractedLine,
    DigitizeWarning,
    validate_rif,
    validate_amounts,
)

__all__ = [
    "DigitizeRequest",
    "DigitizeResponse",
    "ExtractedInvoice",
    "ExtractedLine",
    "DigitizeWarning",
    "validate_rif",
    "validate_amounts",
]
