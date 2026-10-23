"""Obligaciones: dominios, estados efectivos, impuestos, cubetas y truncado (T03)."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

import pytest

from contafis_assistant.domain.enums import ObligationState, WarningCode
from contafis_assistant.odoo.tools.obligations import (
    DEFAULT_DETAIL_LIMIT,
    query_obligations,
)

TODAY = date(2026, 1, 15)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


class FakeGateway:
    """Doble del gateway: devuelve datos por modelo y registra cada RPC."""

    def __init__(self, *, companies, clients, types, obligations, null_obligations=None):
        self.companies = companies
        self.clients = clients
        self.types = types
        self.obligations = obligations
        self.null_obligations = null_obligations
        self.calls = []

    def execute(self, call, **_kwargs):
        self.calls.append(call)
        if call.model == "res.company":
            return deepcopy(self.companies)
        if call.model == "l10n.ve.compliance.client":
            return deepcopy(self.clients)
        if call.model == "l10n.ve.obligation.type":
            return deepcopy(self.types)
        if call.model == "l10n.ve.obligation":
            if self.null_obligations is not None and _contains_leaf(
                call.domain, ("obligation_type_id", "=", False)
            ):
                return deepcopy(self.null_obligations)
            return deepcopy(self.obligations)
        raise AssertionError(f"modelo inesperado: {call.model}")


def _contains_leaf(domain, leaf):
    for token in domain or []:
        if isinstance(token, tuple) and tuple(token) == tuple(leaf):
            return True
    return False


def company(record_id, name):
    return {"id": record_id, "name": name, "active": True}


def client(record_id, name, company_id=1):
    return {
        "id": record_id,
        "name": name,
        "rif": "J-12345678-9",
        "company_id": company_id,
    }


def obligation_type(record_id, name, tax_type):
    return {"id": record_id, "name": name, "tax_type": tax_type}


def obligation(
    record_id,
    client_id,
    *,
    state="pending",
    due_date=TOMORROW,
    type_id=1,
    period="2026-01",
):
    return {
        "id": record_id,
        "client_id": client_id,
        "obligation_type_id": type_id,
        "period": period,
        "due_date": due_date,
        "state": state,
    }


def make_gateway(obligations, *, null_obligations=None, clients=None, companies=None):
    return FakeGateway(
        companies=companies
        or [company(1, "Compañía Uno"), company(2, "Compañía Dos")],
        clients=clients
        or [
            client(100, "Cliente Uno", 1),
            client(200, "Cliente Dos", 2),
        ],
        types=[
            obligation_type(1, "IVA", "IVA"),
            obligation_type(2, "ISLR", "ISLR"),
        ],
        obligations=obligations,
        null_obligations=null_obligations,
    )


def run(gateway, **kwargs):
    return query_obligations(
        gateway,
        today=TODAY,
        allowed_company_ids=[1, 2],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Estados por defecto y efectivos
# ---------------------------------------------------------------------------


def test_default_states_are_pending_and_overdue_only():
    gateway = make_gateway(
        [
            obligation(1, 100, state="pending", due_date=TOMORROW),
            obligation(2, 100, state="overdue", due_date=YESTERDAY, type_id=2),
            obligation(3, 200, state="pending", due_date=TOMORROW),
            obligation(4, 200, state="paid", due_date=YESTERDAY),
            obligation(5, 200, state="cancelled", due_date=TOMORROW),
        ]
    )

    result = run(gateway)

    assert result.total == 3
    assert result.returned == 3
    assert result.truncated is False
    assert {item.id for item in result.items} == {1, 2, 3}
    assert all(
        item.effective_state in (ObligationState.pending, ObligationState.overdue)
        for item in result.items
    )


def test_effective_states_adjust_and_warn():
    gateway = make_gateway(
        [
            obligation(1, 100, state="overdue", due_date=TOMORROW),
            obligation(2, 100, state="pending", due_date=YESTERDAY),
            obligation(3, 100, state="pending", due_date=None),
        ]
    )

    result = run(gateway)
    by_id = {item.id: item for item in result.items}

    assert by_id[1].effective_state is ObligationState.pending
    assert WarningCode.effective_state_adjusted in {
        warning.code for warning in by_id[1].warnings
    }

    assert by_id[2].effective_state is ObligationState.overdue
    assert WarningCode.effective_state_adjusted in {
        warning.code for warning in by_id[2].warnings
    }

    assert by_id[3].effective_state is ObligationState.pending
    assert by_id[3].due_date is None
    assert WarningCode.no_due_date in {
        warning.code for warning in by_id[3].warnings
    }


def test_due_date_false_is_included_and_keeps_state():
    gateway = make_gateway(
        [obligation(1, 100, state="overdue", due_date=None)]
    )

    result = run(gateway)

    assert result.total == 1
    assert result.items[0].effective_state is ObligationState.overdue
    assert WarningCode.no_due_date in {
        warning.code for warning in result.items[0].warnings
    }


def test_explicit_paid_state_is_included_when_requested():
    gateway = make_gateway(
        [
            obligation(1, 100, state="paid", due_date=YESTERDAY),
            obligation(2, 100, state="pending", due_date=TOMORROW),
        ]
    )

    result = run(gateway, states=[ObligationState.paid])

    assert result.total == 1
    assert result.items[0].id == 1
    assert result.items[0].effective_state is ObligationState.paid


# ---------------------------------------------------------------------------
# Impuestos y cubetas
# ---------------------------------------------------------------------------


def test_tax_filter_uses_tax_type_and_reports_null_bucket_separately():
    gateway = make_gateway(
        [
            obligation(1, 100, type_id=1, due_date=TOMORROW),
            obligation(2, 100, type_id=1, state="overdue", due_date=YESTERDAY),
            obligation(3, 100, type_id=2, due_date=TOMORROW),
            obligation(4, 200, type_id=None, due_date=TOMORROW),
        ],
        null_obligations=[
            obligation(4, 200, type_id=None, due_date=TOMORROW),
        ],
    )

    result = run(gateway, tax_type="IVA")

    assert result.total == 2
    assert result.returned == 2
    assert all(item.tax_type == "IVA" for item in result.items)

    null_warnings = [
        warning
        for warning in result.warnings
        if warning.code is WarningCode.null_obligation_type
    ]
    assert len(null_warnings) == 1
    assert null_warnings[0].context.get("count") == 1

    obligation_calls = [
        call for call in gateway.calls if call.model == "l10n.ve.obligation"
    ]
    assert any(
        ("obligation_type_id.tax_type", "=", "IVA") in (call.domain or [])
        for call in obligation_calls
    )


def test_null_obligation_type_is_not_double_counted_without_tax_filter():
    gateway = make_gateway(
        [
            obligation(1, 100, type_id=None, due_date=TOMORROW),
            obligation(2, 100, type_id=None, due_date=TOMORROW),
            obligation(3, 100, type_id=1, due_date=TOMORROW),
            obligation(4, 200, type_id=2, due_date=TOMORROW),
        ]
    )

    result = run(gateway)

    assert result.total == 4
    assert result.total == sum(company.total for company in result.breakdown)

    null_warnings = [
        warning
        for warning in result.warnings
        if warning.code is WarningCode.null_obligation_type
    ]
    assert len(null_warnings) == 1
    assert null_warnings[0].context.get("count") == 2


# ---------------------------------------------------------------------------
# Breakdown, orden y truncado
# ---------------------------------------------------------------------------


def test_breakdown_is_grouped_by_company_in_python():
    gateway = make_gateway(
        [
            obligation(1, 100, type_id=1, due_date=TOMORROW),
            obligation(2, 100, type_id=2, due_date=TOMORROW),
            obligation(3, 100, type_id=1, state="overdue", due_date=YESTERDAY),
            obligation(4, 200, type_id=1, due_date=TOMORROW),
        ]
    )

    result = run(gateway)
    breakdown = {entry.company_id: entry for entry in result.breakdown}

    assert breakdown[1].total == 3
    assert breakdown[1].counts == {"pending": 2, "overdue": 1}
    assert breakdown[2].total == 1
    assert breakdown[2].counts == {"pending": 1}


def test_sixty_obligations_return_fifty_items_and_keep_total():
    rows = [
        obligation(
            index,
            100 if index % 2 else 200,
            due_date=TODAY + timedelta(days=index),
            type_id=1 if index % 3 else 2,
        )
        for index in range(1, 61)
    ]
    gateway = make_gateway(rows)

    result = run(gateway)

    assert DEFAULT_DETAIL_LIMIT == 50
    assert result.total == 60
    assert result.returned == 50
    assert len(result.items) == 50
    assert result.truncated is True
    assert WarningCode.truncated in {warning.code for warning in result.warnings}

    due_dates = [item.due_date for item in result.items]
    assert due_dates == sorted(due_dates)
    assert [item.id for item in result.items] == list(range(1, 51))


def test_order_is_due_date_then_id_with_nulls_last():
    gateway = make_gateway(
        [
            obligation(3, 100, due_date=None),
            obligation(2, 100, due_date=TOMORROW),
            obligation(1, 100, due_date=TOMORROW),
            obligation(4, 100, due_date=YESTERDAY),
        ]
    )

    result = run(gateway)

    assert [item.id for item in result.items] == [4, 1, 2, 3]


# ---------------------------------------------------------------------------
# Contrato de consulta
# ---------------------------------------------------------------------------


def test_every_obligation_rpc_has_explicit_company_filter():
    gateway = make_gateway([obligation(1, 100)])

    run(gateway)

    obligation_calls = [
        call for call in gateway.calls if call.model == "l10n.ve.obligation"
    ]
    assert obligation_calls
    for call in obligation_calls:
        assert ("client_id.company_id", "in", [1, 2]) in (call.domain or [])


def test_no_computed_fields_or_read_group_are_used():
    gateway = make_gateway([obligation(1, 100)])

    run(gateway)

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


def test_returned_always_matches_items_and_total_is_not_below_returned():
    gateway = make_gateway(
        [
            obligation(1, 100),
            obligation(2, 100, state="overdue", due_date=YESTERDAY),
        ]
    )

    result = run(gateway)

    assert result.returned == len(result.items)
    assert result.total >= result.returned
    assert result.source == "odoo"
