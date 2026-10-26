"""Redacción de PII y secretos para logs, warnings y trazas (RF-051, RF-053)."""

from __future__ import annotations

import json

import pytest

from contafis_assistant.domain.contracts import Warning
from contafis_assistant.domain.enums import WarningCode, WarningSource
from contafis_assistant.observability.redaction import (
    redact_exception,
    redact_payload,
    redact_text,
    safe_error_message,
)

KNOWN_NAMES = ["Servicios Andinos, S.A.", "Distribuidora El Sol, C.A."]

RIFS = ["J-12345678-9", "V-12345678", "E-12345678-9", "G-12345678-9"]
EMAILS = ["contacto@andinos.com", "usuario.nombre@empresa.com.ve"]
PHONES = ["+58 212-1234567", "0212-123.45.67", "0412-1234567", "0412-123.45.67"]
DOCUMENTS = ["documento=12345678", "cédula V-12345678", "dni: 1234567"]


@pytest.mark.parametrize("rif", RIFS)
def test_redacts_venezuelan_rif_formats(rif):
    text = f"la empresa rif {rif} declaró"

    redacted = redact_text(text, KNOWN_NAMES)

    assert rif not in redacted
    assert "[rif]" in redacted


@pytest.mark.parametrize("email", EMAILS)
def test_redacts_emails(email):
    redacted = redact_text(f"escribir a {email} por favor", KNOWN_NAMES)

    assert email not in redacted
    assert "[email]" in redacted


@pytest.mark.parametrize("phone", PHONES)
def test_redacts_phone_formats(phone):
    redacted = redact_text(f"llamar al {phone} en horario", KNOWN_NAMES)

    assert phone not in redacted
    assert "[telefono]" in redacted


@pytest.mark.parametrize("document", DOCUMENTS)
def test_redacts_document_numbers(document):
    redacted = redact_text(f"registro {document} del cliente", KNOWN_NAMES)

    assert document not in redacted
    assert "[documento]" in redacted


def test_redacts_known_client_names():
    message = "obligaciones de Servicios Andinos, S.A. y Distribuidora El Sol, C.A."

    redacted = redact_text(message, KNOWN_NAMES)

    assert "Servicios Andinos" not in redacted
    assert "Distribuidora El Sol" not in redacted
    assert redacted.count("[nombre]") == 2


def test_redacts_complete_user_message():
    message = (
        "obligaciones de Servicios Andinos, S.A. rif J-12345678-9 "
        "contacto@andinos.com tel 0412-1234567"
    )

    redacted = redact_text(message, KNOWN_NAMES)

    assert "J-12345678-9" not in redacted
    assert "contacto@andinos.com" not in redacted
    assert "0412-1234567" not in redacted
    assert "Servicios Andinos" not in redacted
    assert "[rif]" in redacted
    assert "[email]" in redacted
    assert "[telefono]" in redacted
    assert "[nombre]" in redacted
    assert "obligaciones de" in redacted


def test_redacts_raw_tool_payload_without_touching_identifiers():
    payload = {
        "total": 6,
        "returned": 6,
        "truncated": False,
        "items": [
            {
                "id": 91,
                "client_id": 12,
                "company_id": 85,
                "client_name": "Servicios Andinos, S.A.",
                "rif": "J-12345678-9",
                "email": "contacto@andinos.com",
                "phone": "0412-1234567",
                "documento": "12345678",
                "compliance_status": "overdue",
            },
            {
                "id": 92,
                "client_id": 13,
                "company_id": 86,
                "client_name": "Distribuidora El Sol, C.A.",
                "rif": "V-12345678",
                "documento": "87654321",
                "compliance_status": "pending",
            },
        ],
        "warnings": [{"code": "count_unavailable", "message": "total estimado"}],
    }

    redacted = redact_payload(payload, KNOWN_NAMES)
    serialized = json.dumps(redacted, ensure_ascii=False)

    assert redacted["total"] == 6
    assert redacted["returned"] == 6
    assert redacted["truncated"] is False
    assert redacted["items"][0]["id"] == 91
    assert redacted["items"][0]["client_id"] == 12
    assert redacted["items"][0]["company_id"] == 85
    assert redacted["items"][0]["compliance_status"] == "overdue"
    assert redacted["items"][0]["client_name"] == "[nombre]"
    assert redacted["items"][0]["rif"] == "[rif]"
    assert redacted["items"][0]["email"] == "[email]"
    assert redacted["items"][0]["phone"] == "[telefono]"
    assert redacted["items"][0]["documento"] == "[documento]"
    assert "J-12345678-9" not in serialized
    assert "V-12345678" not in serialized
    assert "contacto@andinos.com" not in serialized
    assert "0412-1234567" not in serialized
    assert "Servicios Andinos" not in serialized
    assert "Distribuidora El Sol" not in serialized
    assert "12345678" not in serialized
    assert "count_unavailable" in serialized


def test_redacts_serialized_tool_result_as_text():
    payload = {
        "tool": "query_clients",
        "total": 6,
        "items": [
            {
                "client_name": "Distribuidora El Sol, C.A.",
                "rif": "J-12345678-9",
                "email": "contacto@sol.com",
            }
        ],
    }
    serialized = json.dumps(payload, ensure_ascii=False)

    redacted = redact_text(serialized, KNOWN_NAMES)

    assert "J-12345678-9" not in redacted
    assert "contacto@sol.com" not in redacted
    assert "Distribuidora El Sol" not in redacted
    assert "query_clients" in redacted
    assert '"total": 6' in redacted


@pytest.mark.parametrize(
    "secret",
    [
        "http://usuario:clave@127.0.0.1:8069",
        "https://erp-interno.empresa.local/json/2",
        "postgresql://user:pass@db.interno:5432/contea",
        "Bearer nvapi-super-secreta",
        "Authorization: Bearer nvapi-super-secreta",
        "api_key=test-odoo-key",
        "token=eyJhbGciOiJIUzI1NiIsInR5cCI6",
        "connection_string=postgres://u:p@host:5432/db",
    ],
)
def test_redacts_internal_urls_tokens_and_connection_strings(secret):
    redacted = redact_text(f"el fallo fue: {secret}", KNOWN_NAMES)

    assert secret not in redacted
    assert "[url]" in redacted or "[secreto]" in redacted
    assert "clave" not in redacted.replace("[secreto]", "")
    assert "nvapi-super-secreta" not in redacted
    assert "test-odoo-key" not in redacted


def test_redaction_does_not_touch_ids_counts_states_tools_or_request_id():
    message = (
        "resumen de query_obligations con company_id=85, client_id=12, "
        "total=6, tool_call_count=3, status=partial, request_id="
        "6f1a2b3c-4d5e-4f60-8a91-b2c3d4e5f607, thread_id=hilo-1, "
        "page=4, returned=6, truncated=false"
    )

    redacted = redact_text(message, KNOWN_NAMES)

    assert redacted == message


def test_redaction_is_idempotent():
    message = (
        "Servicios Andinos, S.A. J-12345678-9 contacto@andinos.com "
        "0412-1234567 http://usuario:clave@127.0.0.1:8069 "
        "Bearer nvapi-super-secreta documento=12345678"
    )

    once = redact_text(message, KNOWN_NAMES)
    twice = redact_text(once, KNOWN_NAMES)

    assert once == twice


def test_redact_exception_removes_secrets_and_pii():
    exc = RuntimeError(
        "fallo contra http://admin:clave@127.0.0.1:8069 con "
        "Authorization: Bearer nvapi-super-secreta rif J-12345678-9"
    )

    redacted = redact_exception(exc)

    assert "clave" not in redacted
    assert "nvapi-super-secreta" not in redacted
    assert "J-12345678-9" not in redacted
    assert "[url]" in redacted
    assert "[secreto]" in redacted
    assert "[rif]" in redacted


def test_redact_exception_accepts_known_names():
    exc = ValueError("no se resolvió Servicios Andinos, S.A.")

    redacted = redact_exception(exc, KNOWN_NAMES)

    assert "Servicios Andinos" not in redacted
    assert "[nombre]" in redacted


def test_safe_error_message_keeps_type_and_redacts_payload():
    exc = RuntimeError("Bearer nvapi-super-secreta y rif V-12345678")

    safe = safe_error_message(exc, KNOWN_NAMES)

    assert "RuntimeError" in safe
    assert "nvapi-super-secreta" not in safe
    assert "V-12345678" not in safe


def test_warning_message_is_redacted():
    warning = Warning(
        code=WarningCode.source_unavailable,
        message="No se pudo clasificar la consulta: Bearer nvapi-super-secreta",
        source=WarningSource.system,
    )

    redacted = redact_text(warning.message, KNOWN_NAMES)

    assert "nvapi-super-secreta" not in redacted
    assert "[secreto]" in redacted
    assert redact_text(redacted, KNOWN_NAMES) == redacted


def test_redact_payload_preserves_structure_of_non_text_values():
    payload = {
        "counts": {"total": 6, "returned": 6},
        "flags": [True, False, None],
        "tools": ["query_obligations", "search_normativa"],
        "items": ({"id": 1}, {"id": 2}),
        "score": 0.5,
    }

    redacted = redact_payload(payload, KNOWN_NAMES)

    assert redacted["counts"] == {"total": 6, "returned": 6}
    assert redacted["flags"] == [True, False, None]
    assert redacted["tools"] == ["query_obligations", "search_normativa"]
    assert list(redacted["items"]) == [{"id": 1}, {"id": 2}]
    assert redacted["score"] == 0.5


def test_redact_payload_redacts_nested_free_text():
    payload = {
        "note": "rif V-12345678 y correo@ejemplo.com",
        "items": [{"message": "llamar al 0412-1234567"}],
    }

    redacted = redact_payload(payload, KNOWN_NAMES)

    assert redacted["note"] == "rif [rif] y [email]"
    assert redacted["items"][0]["message"] == "llamar al [telefono]"


def test_redact_text_is_noop_for_clean_text():
    clean = "obligaciones: total 6, mostrados 6, estado pendiente"

    assert redact_text(clean, KNOWN_NAMES) == clean
