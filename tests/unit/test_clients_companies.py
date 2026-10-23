"""Clientes y compañías: contadores, compliance, agregados y lectura en lote (T03)."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from contafis_assistant.domain.enums import ComplianceStatus, WarningCode
from contafis_assistant.odoo.tools.clients import query_clients
from contafis_assistant.odoo.tools.companies import query_companies

TODAY = date(2026, 1, 15)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


class FakeGateway:
    """Doble del gateway Odoo; cada RPC se registra para verificar el contrato."""

    def __init__(self, *, companies, clients, obligations, alerts):
        self.companies = companies
        self.clients = clients
        self.obligations = obligations
        self.alerts = alerts
        self.calls = []

    def execute(self, call, **_kwargs):
        self.calls.append(call)
        if call.model == "res.company":
            return deepcopy(self.companies)
        if call.model == "l10n.ve.compliance.client":
            return deepcopy(self.clients)
        if call.model == "l10n.ve.obligation":
            return deepcopy(self.obligations)
        if call.model == "l10n.ve.alert":
            return deepcopy(self.alerts)
        raise AssertionError(f"modelo inesperado: {call.model}")


def company(record_id, name, *, active=True):
    return {"id": record_id, "name": name, "active": active}


def client(record_id, name, company_id, *, rif="J-12345678-9", company_name=""):
    return {
        "id": record_id,
        "name": name,
        "rif": rif,
        "company_id": company_id,
        "company_name": company_name,
    }


def obligation(record_id, client_id, *, state="pending", due_date=TOMORROW):
    return {
        "id": record_id,
        "client_id": client_id,
        "obligation_type_id": 1,
        "period": "2026-01",
        "due_date": due_date,
        "state": state,
    }


def alert(record_id, client_id, *, state="pending"):
    return {"id": record_id, "client_id": client_id, "state": state}


def make_gateway():
    return FakeGateway(
        companies=[
            company(1, "Compañía Activa"),
            company(2, "Compañía Archivada", active=False),
        ],
        clients=[
            client(10, "Cliente A", 1),
            client(20, "Cliente B", 1),
            client(30, "Cliente C", 2, company_name="Compañía Archivada"),
        ],
        obligations=[
            obligation(1, 10, state="pending", due_date=TOMORROW),
            obligation(2, 10, state="overdue", due_date=YESTERDAY),
            obligation(3, 10, state="paid", due_date=YESTERDAY),
            obligation(4, 20, state="pending", due_date=TOMORROW),
            obligation(5, 30, state="overdue", due_date=YESTERDAY),
        ],
        alerts=[
            alert(1, 10, state="pending"),
            alert(2, 10, state="sent"),
            alert(3, 20, state="pending"),
            alert(4, 30, state="pending"),
        ],
    )


def run_clients(gateway, **kwargs):
    return query_clients(
        gateway,
        today=TODAY,
        allowed_company_ids=[1, 2],
        **kwargs,
    )


def run_companies(gateway, **kwargs):
    return query_companies(gateway, today=TODAY, **kwargs)


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------


def test_client_counters_and_compliance_are_computed_from_effective_states():
    gateway = make_gateway()

    result = run_clients(gateway)
    by_id = {item.id: item for item in result.items}

    assert set(by_id) == {10, 20}
    assert by_id[10].obligations_total == 2
    assert by_id[10].obligations_overdue == 1
    assert by_id[10].alerts_pending == 1
    assert by_id[10].compliance_status is ComplianceStatus.vencido

    assert by_id[20].obligations_total == 1
    assert by_id[20].obligations_overdue == 0
    assert by_id[20].alerts_pending == 1
    assert by_id[20].compliance_status is ComplianceStatus.pendiente

    assert result.total == 2
    assert result.returned == 2
    assert result.truncated is False


def test_client_counters_use_one_batch_read_without_per_client_queries():
    gateway = make_gateway()

    run_clients(gateway)

    obligation_calls = [
        call for call in gateway.calls if call.model == "l10n.ve.obligation"
    ]
    assert len(obligation_calls) == 1
    domain = obligation_calls[0].domain or []
    assert ("client_id.company_id", "in", [1]) in domain
    # No hay una llamada por cada cliente (10/20) en el lote.
    assert not any(
        token == ("client_id", "=", client_id)
        for token in domain
        for client_id in (10, 20)
    )

    alert_calls = [call for call in gateway.calls if call.model == "l10n.ve.alert"]
    assert len(alert_calls) == 1


def test_clients_truncate_detail_but_keep_total():
    gateway = make_gateway()

    result = run_clients(gateway, detail_limit=1)

    assert result.total == 2
    assert result.returned == 1
    assert result.truncated is True


def test_archived_company_clients_are_excluded_from_client_queries():
    gateway = make_gateway()

    result = run_clients(gateway)

    assert 30 not in {item.id for item in result.items}


# ---------------------------------------------------------------------------
# Compañías
# ---------------------------------------------------------------------------


def test_company_aggregate_includes_only_active_companies():
    gateway = make_gateway()

    result = run_companies(gateway, allowed_company_ids=[1, 2])

    assert result.total == 1
    assert result.returned == 1
    assert [item.id for item in result.items] == [1]

    item = result.items[0]
    assert item.clients_total == 2
    assert item.obligations_total == 3
    assert item.obligations_overdue == 1
    assert item.alerts_pending == 2
    assert item.company_archived is False


def test_company_breakdown_matches_active_obligations():
    gateway = make_gateway()

    result = run_companies(gateway, allowed_company_ids=[1, 2])

    assert len(result.breakdown) == 1
    breakdown = result.breakdown[0]
    assert breakdown.company_id == 1
    assert breakdown.total == 3
    assert breakdown.counts == {"pending": 2, "overdue": 1}


def test_no_active_companies_returns_warning_not_a_misleading_zero():
    gateway = FakeGateway(
        companies=[company(2, "Compañía Archivada", active=False)],
        clients=[],
        obligations=[],
        alerts=[],
    )

    result = run_companies(gateway, allowed_company_ids=[2])

    assert result.total == 0
    assert result.returned == 0
    assert result.items == []
    assert WarningCode.no_active_companies in {
        warning.code for warning in result.warnings
    }


# ---------------------------------------------------------------------------
# Contrato: campos computados y agregación en Python
# ---------------------------------------------------------------------------


def test_no_computed_fields_are_queried_or_filtered():
    gateway = make_gateway()

    run_clients(gateway)
    run_companies(gateway, allowed_company_ids=[1, 2])

    forbidden = {
        "compliance_status",
        "cartelera_pending_count",
        "compliance_score",
    }
    for call in gateway.calls:
        assert call.method != "read_group"
        assert not forbidden.intersection(call.fields or [])
        for token in call.domain or []:
            if isinstance(token, tuple):
                assert token[0] not in forbidden


def test_compliance_is_calculated_in_python_not_requested_from_odoo():
    gateway = make_gateway()

    result = run_clients(gateway)

    # El estado se deriva de una lectura mínima de obligaciones.
    obligation_call = next(
        call for call in gateway.calls if call.model == "l10n.ve.obligation"
    )
    assert set(obligation_call.fields) == {
        "id",
        "client_id",
        "obligation_type_id",
        "period",
        "due_date",
        "state",
    }
    assert result.items[0].compliance_status in {
        ComplianceStatus.al_dia,
        ComplianceStatus.pendiente,
        ComplianceStatus.vencido,
    }


def test_all_results_validate_contract_and_company_filter():
    gateway = make_gateway()

    clients_result = run_clients(gateway)
    companies_result = run_companies(gateway, allowed_company_ids=[1, 2])

    for result in (clients_result, companies_result):
        assert result.returned == len(result.items)
        assert result.total >= result.returned
        assert result.source == "odoo"

    for call in gateway.calls:
        if call.model in {
            "l10n.ve.compliance.client",
            "l10n.ve.obligation",
            "l10n.ve.alert",
        }:
            assert any(
                isinstance(token, tuple) and token[0] == "company_id"
                or isinstance(token, tuple)
                and token[0] == "client_id.company_id"
                for token in (call.domain or [])
            )
