"""Integración de las tools ERP contra un gateway fake y contrato ToolResult (T03)."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

from contafis_assistant.domain.contracts import (
    AlertsResult,
    ClientsResult,
    CompaniesResult,
    DocumentsResult,
    EntityRef,
    ObligationsResult,
)
from contafis_assistant.domain.enums import EntityType, Status
from contafis_assistant.odoo.tools.alerts import query_alerts
from contafis_assistant.odoo.tools.clients import query_clients
from contafis_assistant.odoo.tools.companies import query_companies
from contafis_assistant.odoo.tools.documents import query_documents
from contafis_assistant.odoo.tools.entities import resolve_entity
from contafis_assistant.odoo.tools.obligations import query_obligations

TODAY = date(2026, 1, 15)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


class FakeGateway:
    def __init__(self):
        self.calls = []

    def execute(self, call, **_kwargs):
        self.calls.append(call)
        if call.model == "res.company":
            return [
                {"id": 1, "name": "Compañía Uno", "active": True},
            ]
        if call.model == "l10n.ve.compliance.client":
            return [
                {
                    "id": 100,
                    "name": "Distribuidora El Sol",
                    "rif": "J-12345678-9",
                    "company_id": 1,
                }
            ]
        if call.model == "l10n.ve.obligation.type":
            return [{"id": 1, "name": "IVA", "tax_type": "IVA"}]
        if call.model == "l10n.ve.obligation":
            return [
                {
                    "id": 1,
                    "client_id": 100,
                    "obligation_type_id": 1,
                    "period": "2026-01",
                    "due_date": TOMORROW,
                    "state": "pending",
                },
                {
                    "id": 2,
                    "client_id": 100,
                    "obligation_type_id": 1,
                    "period": "2026-01",
                    "due_date": YESTERDAY,
                    "state": "overdue",
                },
            ]
        if call.model == "l10n.ve.alert":
            return [
                {
                    "id": 1,
                    "client_id": 100,
                    "obligation_id": 1,
                    "document_id": None,
                    "alert_type": "due_soon",
                    "date": datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
                    "state": "pending",
                    "channel": "email",
                    "message": "Monto: 123,45 USD https://wa.me/584141234567",
                }
            ]
        if call.model == "l10n.ve.document.type":
            return [
                {"id": 1, "name": "RIF", "code": "C01", "required_for": "company"}
            ]
        if call.model == "l10n.ve.cartelera.status":
            return [
                {
                    "id": 1,
                    "client_id": 100,
                    "year": TODAY.year,
                    "month": str(TODAY.month),
                    "document_type_id": 1,
                    "document_id": 9,
                    "state": "valid",
                    "expiry_date": TOMORROW,
                    "date_generated": datetime(2026, 1, 10, 12),
                }
            ]
        if call.model == "l10n.ve.document":
            return []
        raise AssertionError(f"modelo inesperado: {call.model}")


def assert_valid_contract(result):
    assert result.returned == len(result.items)
    assert result.total is not None
    assert result.total >= result.returned
    assert result.source == "odoo"


def test_entity_resolution_and_obligations_tool_end_to_end():
    gateway = FakeGateway()

    resolution = resolve_entity(
        gateway,
        "distribuidora el sol",
        allowed_company_ids=[1],
    )
    assert resolution.status is Status.ok
    assert resolution.entity is not None
    assert resolution.entity.type is EntityType.client

    result = query_obligations(
        gateway,
        today=TODAY,
        allowed_company_ids=[1],
        client_id=resolution.entity.id,
    )

    assert isinstance(result, ObligationsResult)
    assert result.kind == "obligations"
    assert_valid_contract(result)
    assert result.total == 2
    assert result.returned == 2
    assert all(item.client_id == 100 for item in result.items)
    assert all(item.company_id == 1 for item in result.items)
    assert all(item.company_name == "Compañía Uno" for item in result.items)


def test_clients_tool_returns_valid_tool_result():
    gateway = FakeGateway()

    result = query_clients(
        gateway,
        today=TODAY,
        allowed_company_ids=[1],
    )

    assert isinstance(result, ClientsResult)
    assert result.kind == "clients"
    assert_valid_contract(result)
    assert result.total == 1
    assert result.items[0].id == 100
    assert result.items[0].obligations_total == 2
    assert result.items[0].obligations_overdue == 1
    assert result.items[0].alerts_pending == 1


def test_companies_tool_returns_valid_tool_result():
    gateway = FakeGateway()

    result = query_companies(
        gateway,
        today=TODAY,
        allowed_company_ids=[1],
    )

    assert isinstance(result, CompaniesResult)
    assert result.kind == "companies"
    assert_valid_contract(result)
    assert result.total == 1
    assert result.items[0].id == 1
    assert result.items[0].clients_total == 1
    assert result.items[0].obligations_total == 2
    assert result.breakdown[0].company_id == 1


def test_documents_tool_returns_valid_tool_result():
    gateway = FakeGateway()

    result = query_documents(
        gateway,
        today=TODAY,
        allowed_company_ids=[1],
    )

    assert isinstance(result, DocumentsResult)
    assert result.kind == "documents"
    assert_valid_contract(result)
    assert result.total == 1
    assert result.pending_count == 0
    assert result.items[0].client_id == 100
    assert result.items[0].document_type_code == "C01"
    assert result.items[0].source.value == "current_snapshot"


def test_alerts_tool_returns_valid_tool_result_and_clean_message():
    gateway = FakeGateway()

    result = query_alerts(
        gateway,
        today=TODAY,
        allowed_company_ids=[1],
    )

    assert isinstance(result, AlertsResult)
    assert result.kind == "alerts"
    assert_valid_contract(result)
    assert result.total == 1
    assert result.items[0].client_id == 100
    assert result.items[0].company_id == 1
    assert result.items[0].message_clean is None


def test_all_tool_results_are_pydantic_and_serializable():
    gateway = FakeGateway()

    obligations = query_obligations(
        gateway, today=TODAY, allowed_company_ids=[1]
    )
    clients = query_clients(gateway, today=TODAY, allowed_company_ids=[1])
    companies = query_companies(gateway, today=TODAY, allowed_company_ids=[1])

    for result in (obligations, clients, companies):
        dumped = result.model_dump()
        assert dumped["source"] == "odoo"
        assert dumped["returned"] == len(dumped["items"])
        assert isinstance(dumped["warnings"], list)


def test_entity_ref_for_clients_always_carries_company():
    gateway = FakeGateway()

    resolution = resolve_entity(
        gateway, "distribuidora el sol", allowed_company_ids=[1]
    )

    assert isinstance(resolution.entity, EntityRef)
    assert resolution.entity.company_id == 1
    assert resolution.entity.company_name == "Compañía Uno"
