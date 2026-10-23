"""Contrato de entrada/salida: validación manual y modelos Pydantic (T01)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from pydantic import TypeAdapter, ValidationError

from contafis_assistant.domain.contracts import (
    ChatRequest,
    ChatResponse,
    ClientsResult,
    DocumentsResult,
    EntityRef,
    ObligationItem,
    ObligationsResult,
    ToolResult,
    Warning,
)
from contafis_assistant.domain.enums import (
    ComplianceStatus,
    EntityType,
    ObligationState,
    Status,
    WarningCode,
)
from contafis_assistant.domain.request import validate_chat_request


def _uuid4(value: str) -> uuid.UUID:
    parsed = uuid.UUID(value)
    assert parsed.version == 4
    return parsed


def _obligation_item(item_id: int = 1) -> ObligationItem:
    return ObligationItem(
        id=item_id,
        client_id=10,
        client_name="Cliente Uno",
        company_id=20,
        company_name="Compañía Uno",
        obligation_type_id=1,
        obligation_type_name="IVA",
        tax_type="IVA",
        period="2026-01",
        due_date=date(2026, 1, 20),
        state=ObligationState.pending,
        effective_state=ObligationState.pending,
    )


# ---------------------------------------------------------------------------
# message
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({}, "missing_message"),
        ({"message": None}, "non_textual_message"),
        ({"message": 123}, "non_textual_message"),
        ({"message": []}, "non_textual_message"),
        ({"message": {}}, "non_textual_message"),
        ({"message": ""}, "blank_message"),
        ({"message": "   "}, "blank_message"),
        ({"message": "x" * 2001}, "too_long_message"),
    ],
)
def test_invalid_message_returns_invalid_request_and_uuid4(payload, reason):
    decision = validate_chat_request(payload, max_message_chars=2000)

    assert decision.ok is False
    assert decision.reason == reason
    assert decision.message is None
    _uuid4(decision.thread_id)


@pytest.mark.parametrize("message", ["a", "x" * 2000, "  hola  "])
def test_message_boundaries_are_valid(message):
    decision = validate_chat_request({"message": message}, max_message_chars=2000)

    assert decision.ok is True
    assert decision.message == message
    assert decision.reason is None


def test_absent_message_is_missing():
    decision = validate_chat_request({"thread_id": "abc"})

    assert decision.ok is False
    assert decision.reason == "missing_message"
    assert decision.thread_id == "abc"


# ---------------------------------------------------------------------------
# thread_id
# ---------------------------------------------------------------------------


def test_absent_thread_id_generates_uuid4_without_error():
    decision = validate_chat_request({"message": "hola"})

    assert decision.ok is True
    assert decision.thread_id_generated is True
    _uuid4(decision.thread_id)


@pytest.mark.parametrize("thread_id", ["a", "abc_DEF-123", "A" * 64])
def test_valid_thread_id_is_preserved(thread_id):
    decision = validate_chat_request({"message": "hola", "thread_id": thread_id})

    assert decision.ok is True
    assert decision.thread_id == thread_id
    assert decision.thread_id_generated is False


@pytest.mark.parametrize("thread_id", ["", "a b", "a/b", "a.b", "x" * 65, 12, True])
def test_invalid_thread_id_generates_uuid4_and_invalidates_request(thread_id):
    decision = validate_chat_request({"message": "hola", "thread_id": thread_id})

    assert decision.ok is False
    assert decision.reason == "invalid_thread_id"
    assert decision.message is None
    _uuid4(decision.thread_id)


@pytest.mark.parametrize("body", [None, [], "texto", 42])
def test_non_object_body_is_invalid_with_generated_thread(body):
    decision = validate_chat_request(body)

    assert decision.ok is False
    assert decision.reason == "invalid_body"
    assert decision.message is None
    _uuid4(decision.thread_id)


def test_permissive_chat_request_keeps_extra_fields_only_at_the_boundary():
    request = ChatRequest.model_validate(
        {"message": "hola", "thread_id": "abc", "company_name": "ignorado"}
    )

    assert request.message == "hola"
    assert request.thread_id == "abc"
    assert request.model_extra == {"company_name": "ignorado"}


# ---------------------------------------------------------------------------
# ToolResult y validaciones cruzadas
# ---------------------------------------------------------------------------


def test_obligations_result_valid_contract():
    result = ObligationsResult(
        total=2,
        returned=1,
        items=[_obligation_item()],
        truncated=True,
    )

    assert result.kind == "obligations"
    assert result.returned == len(result.items)
    assert result.source == "odoo"


def test_returned_must_match_items_count():
    with pytest.raises(ValidationError):
        ObligationsResult(total=2, returned=2, items=[_obligation_item()], truncated=False)


def test_total_must_be_greater_or_equal_than_returned():
    with pytest.raises(ValidationError):
        ObligationsResult(total=0, returned=1, items=[_obligation_item()], truncated=False)


def test_total_none_requires_truncated_and_count_unavailable():
    with pytest.raises(ValidationError):
        ObligationsResult(total=None, returned=0, items=[], truncated=False)

    with pytest.raises(ValidationError):
        ObligationsResult(total=None, returned=0, items=[], truncated=True)

    result = ObligationsResult(
        total=None,
        returned=0,
        items=[],
        truncated=True,
        warnings=[
            Warning(
                code=WarningCode.count_unavailable,
                message="No se pudo confirmar el total.",
            )
        ],
    )
    assert result.total is None
    assert result.truncated is True


def test_documents_result_pending_count_is_validated():
    result = DocumentsResult(
        total=10,
        returned=0,
        items=[],
        truncated=False,
        pending_count=3,
    )
    assert result.pending_count == 3

    with pytest.raises(ValidationError):
        DocumentsResult(
            total=10,
            returned=0,
            items=[],
            truncated=False,
            pending_count=11,
        )

    with pytest.raises(ValidationError):
        DocumentsResult(
            total=10,
            returned=0,
            items=[],
            truncated=False,
            pending_count=-1,
        )


def test_discriminated_union_selects_concrete_result():
    adapter = TypeAdapter(ToolResult)
    obligations = adapter.validate_python(
        ObligationsResult(total=1, returned=1, items=[_obligation_item()], truncated=False).model_dump()
    )
    assert isinstance(obligations, ObligationsResult)

    clients = adapter.validate_python(
        ClientsResult(total=0, returned=0, items=[], truncated=False).model_dump()
    )
    assert isinstance(clients, ClientsResult)

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "kind": "desconocido",
                "total": 0,
                "returned": 0,
                "items": [],
                "truncated": False,
            }
        )


def test_warning_rejects_unknown_code():
    with pytest.raises(ValidationError):
        Warning(code="codigo_inventado", message="x")


def test_entity_ref_client_requires_company_data():
    with pytest.raises(ValidationError):
        EntityRef(id=1, name="Cliente", type=EntityType.client)

    ref = EntityRef(
        id=1,
        name="Cliente",
        type=EntityType.client,
        company_id=2,
        company_name="Compañía",
        company_archived=False,
    )
    assert ref.company_id == 2


# ---------------------------------------------------------------------------
# ChatResponse
# ---------------------------------------------------------------------------


def _response(**overrides) -> ChatResponse:
    values = {
        "request_id": str(uuid.uuid4()),
        "thread_id": "thread_abc",
        "status": Status.ok,
        "answer": "Respuesta",
        "tool_call_count": 0,
        "truncated": False,
    }
    values.update(overrides)
    return ChatResponse(**values)


def test_chat_response_valid_and_defaults():
    response = _response()

    assert response.data == []
    assert response.sources == []
    assert response.options == []
    assert response.tools_used == []
    assert response.warnings == []


def test_chat_response_request_id_must_be_uuid4():
    with pytest.raises(ValidationError):
        _response(request_id="no-es-uuid")

    with pytest.raises(ValidationError):
        _response(request_id=str(uuid.uuid1()))


def test_options_only_allowed_for_clarification_or_not_found():
    ref = EntityRef(id=1, name="Compañía", type=EntityType.company, company_archived=False)
    with pytest.raises(ValidationError):
        _response(options=[ref])


def test_not_found_is_invalid_with_incomplete_scope():
    response = _response(
        status=Status.not_found,
        options=[EntityRef(id=1, name="Compañía", type=EntityType.company, company_archived=False)],
    )

    with pytest.raises(ValueError):
        response.validate_scope(scope_complete=False)

    assert response.validate_scope(scope_complete=True) is None


def test_data_uses_discriminated_union():
    response = _response(
        data=[ObligationsResult(total=1, returned=1, items=[_obligation_item()], truncated=False)]
    )
    assert isinstance(response.data[0], ObligationsResult)

    with pytest.raises(ValidationError):
        _response(
            data=[
                {
                    "kind": "obligations",
                    "total": 0,
                    "returned": 0,
                    "items": [],
                    "truncated": False,
                    "source": "rag",
                }
            ]
        )


def test_compliance_status_enum_is_part_of_contract():
    assert ComplianceStatus.al_dia == "al_dia"
