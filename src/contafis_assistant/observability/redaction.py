"""Redacción pura y determinista de PII y secretos.

Regla única para logs, warnings y trazas (RNF-005/008/015): la misma política
de enmascarado se aplica en las tres superficies. La redacción no usa red ni
LLM, es idempotente y nunca altera identificadores, conteos, estados ni
nombres de tools.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

RIF_MASK = "[rif]"
EMAIL_MASK = "[email]"
PHONE_MASK = "[telefono]"
DOCUMENT_MASK = "[documento]"
NAME_MASK = "[nombre]"
SECRET_MASK = "[secreto]"
URL_MASK = "[url]"

DATA_OPEN = "<dato>"
DATA_CLOSE = "</dato>"

_URL = re.compile(
    r"(?i)\b(?:https?|ftp|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://\S+"
)
_HEADER = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization|x-api-key|api-key|x-auth-token"
    r"|x-odoo-database|odoo-database|cookie|set-cookie|x-forwarded-for)\b"
    r"\s*:\s*[^\s,;]+(?:\s+[^\s,;]+)?"
)
_BEARER = re.compile(
    r"(?i)\b(?:bearer|basic|token)\s+[A-Za-z0-9._\-]{4,}"
)
_TOKEN = re.compile(
    r"(?i)\b(?:nvapi|sk|pk|ghp_|github_pat_|xoxb|xoxp|AIza)[-_][A-Za-z0-9._\-]{4,}"
)
_KEY_VALUE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|credential[s]?"
    r"|connection[_-]?string|conn[_-]?str)\b\s*[:=]\s*[^\s,;]+"
)
_DOCUMENT = re.compile(
    r"(?i)\b(?:documento|c[eé]dula|document|doc|dni|pasaporte|ci)\b"
    r"\s*[:=#]?\s*(?:\"|\')?\s*"
    r"(?:[JVEG][\s.-]*)?"
    r"(?:\d{2,3}(?:[.\s-]\d{3}){1,2}|\d{6,9})"
    r"(?:[.\s-]\d)?"
    r"(?![\w.])"
)
_RIF = re.compile(
    r"(?i)(?<![\w.-])[JVEG][\s.-]*\d{7,9}(?:[.\s-]*\d)?(?![\w])"
)
_EMAIL = re.compile(
    r"(?<![\w.])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![\w])"
)
_PHONE = re.compile(
    r"(?<![\w.])"
    r"(?:\+?58[\s.-]*)?"
    r"(?:"
    r"\d{3}[\s.-]?\d{7}"
    r"|\d{3}[\s.-]?\d{3}[\s.-]?\d{4}"
    r"|\d{3}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}"
    r"|\d{4}[\s.-]?\d{7}"
    r"|\d{4}[\s.-]?\d{3}[\s.-]?\d{4}"
    r"|\d{4}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}"
    r")"
    r"(?![\w.])"
)

_TEXT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_URL, URL_MASK),
    (_HEADER, SECRET_MASK),
    (_BEARER, SECRET_MASK),
    (_TOKEN, SECRET_MASK),
    (_KEY_VALUE, SECRET_MASK),
    (_DOCUMENT, DOCUMENT_MASK),
    (_RIF, RIF_MASK),
    (_EMAIL, EMAIL_MASK),
    (_PHONE, PHONE_MASK),
)

_RIF_KEYS = frozenset({"rif", "tax_id", "vat", "vat_number"})
_EMAIL_KEYS = frozenset({"email", "correo", "mail", "email_from", "email_to"})
_PHONE_KEYS = frozenset(
    {"phone", "telefono", "teléfono", "mobile", "celular", "whatsapp", "fax"}
)
_DOCUMENT_KEYS = frozenset(
    {
        "documento",
        "document_number",
        "doc_number",
        "cedula",
        "cédula",
        "dni",
        "pasaporte",
        "passport",
        "national_id",
    }
)
_NAME_KEYS = frozenset(
    {
        "name",
        "nombre",
        "client_name",
        "company_name",
        "partner_name",
        "customer_name",
        "razon_social",
    }
)

_KEY_MASKS: tuple[tuple[frozenset[str], str], ...] = (
    (_RIF_KEYS, RIF_MASK),
    (_EMAIL_KEYS, EMAIL_MASK),
    (_PHONE_KEYS, PHONE_MASK),
    (_DOCUMENT_KEYS, DOCUMENT_MASK),
    (_NAME_KEYS, NAME_MASK),
)


def _normalize_key(key: Any) -> str:
    return str(key).strip().lower().replace(" ", "_").replace("-", "_")


def _known_name_patterns(known_names: Iterable[str]) -> list[re.Pattern[str]]:
    ordered = sorted(
        {name.strip() for name in known_names if isinstance(name, str) and name.strip()},
        key=len,
        reverse=True,
    )
    return [re.compile(re.escape(name), re.IGNORECASE) for name in ordered]


def _mask_for_key(key: Any) -> str | None:
    normalized = _normalize_key(key)
    for keys, mask in _KEY_MASKS:
        if normalized in keys:
            return mask
    return None


def redact_text(text: str, known_names: Sequence[str] = ()) -> str:
    """Enmascara RIF, email, teléfono, documentos, URLs y secretos."""

    if not isinstance(text, str):
        return text
    if not text:
        return text

    redacted = text
    for pattern, mask in _TEXT_RULES:
        if pattern is _KEY_VALUE:
            redacted = pattern.sub(lambda match: f"{match.group(1)}: {SECRET_MASK}", redacted)
        else:
            redacted = pattern.sub(mask, redacted)

    for pattern in _known_name_patterns(known_names):
        redacted = pattern.sub(NAME_MASK, redacted)

    return redacted


def redact_payload(obj: Any, known_names: Sequence[str] = ()) -> Any:
    """Redacta de forma recursiva un payload arbitrario."""

    if isinstance(obj, str):
        return redact_text(obj, known_names)
    if isinstance(obj, Mapping):
        redacted: dict[Any, Any] = {}
        for key, value in obj.items():
            mask = _mask_for_key(key)
            if mask is not None and isinstance(value, str):
                redacted[key] = mask
                continue
            if isinstance(value, str):
                redacted[key] = redact_text(value, known_names)
                continue
            redacted[key] = redact_payload(value, known_names)
        return redacted
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [redact_payload(item, known_names) for item in obj]
    return obj


def redact_exception(exc: BaseException, known_names: Sequence[str] = ()) -> str:
    """Mensaje de error saneado: nunca transporta credenciales ni PII."""

    return redact_text(str(exc), known_names)


def safe_error_message(exc: BaseException, known_names: Sequence[str] = ()) -> str:
    """Tipo de error más su mensaje redactado."""

    message = redact_exception(exc, known_names)
    name = type(exc).__name__
    if not message.strip():
        return name
    return f"{name}: {message}"


def wrap_untrusted(text: str) -> str:
    """Delimita contenido no confiable del usuario, del RAG o del ERP."""

    return f"{DATA_OPEN}\n{text}\n{DATA_CLOSE}"


__all__ = [
    "DATA_CLOSE",
    "DATA_OPEN",
    "DOCUMENT_MASK",
    "EMAIL_MASK",
    "NAME_MASK",
    "PHONE_MASK",
    "RIF_MASK",
    "SECRET_MASK",
    "URL_MASK",
    "redact_exception",
    "redact_payload",
    "redact_text",
    "safe_error_message",
    "wrap_untrusted",
]
