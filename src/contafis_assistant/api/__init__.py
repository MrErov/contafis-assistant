"""Capa HTTP del asistente fiscal."""

from contafis_assistant.domain.digitize import (
    DigitizeRequest,
    DigitizeResponse,
    ExtractedInvoice,
    ExtractedLine,
    DigitizeWarning,
)

__all__ = [
    "DigitizeRequest",
    "DigitizeResponse",
    "ExtractedInvoice",
    "ExtractedLine",
    "DigitizeWarning",
]
