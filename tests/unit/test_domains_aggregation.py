"""Dominios exactos, agregación, compliance y orden/truncado (T01)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from contafis_assistant.domain.aggregation import (
    DocumentRow,
    ObligationRow,
    aggregate_obligations,
    compliance_status,
    count_documents,
    order_alerts,
    order_documents,
    order_obligations,
    truncate,
)
from contafis_assistant.domain.domains import (
    d_cancelled,
    d_doc_expired,
    d_doc_missing,
    d_doc_pending,
    d_doc_rejected,
    d_doc_valid,
    d_overdue,
    d_paid,
    d_pending,
)
from contafis_assistant.domain.enums import ComplianceStatus

TODAY = date(2026, 1, 15)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


# ---------------------------------------------------------------------------
# Evaluador mínimo de dominios Odoo (solo para los tests)
# ---------------------------------------------------------------------------


def _leaf(record: dict[str, Any], condition: Sequence[Any]) -> bool:
    field, operator, value = condition
    actual = record.get(field)

    # Odoo representa el nulo como False; los registros de test usan None.
    null_match = actual is None and value is False
    if operator == "=":
        return null_match or actual == value
    if operator == "!=":
        return not (null_match or actual == value)
    if operator == "in":
        return actual in value
    if operator == "not in":
        return actual not in value
    if operator == ">":
        return actual is not None and actual > value
    if operator == ">=":
        return actual is not None and actual >= value
    if operator == "<":
        return actual is not None and actual < value
    if operator == "<=":
        return actual is not None and actual <= value
    raise AssertionError(f"Operador no soportado en test: {operator}")


def _eval_domain(domain: list[Any], record: dict[str, Any]) -> bool:
    pos = 0

    def parse() -> bool:
        nonlocal pos
        token = domain[pos]
        pos += 1
        if token == "&":
            left = parse()
            right = parse()
            result = left and right
            # Algunos dominios del spec expresan A & B & C como '&', A, B, C.
            while pos < len(domain) and isinstance(domain[pos], tuple):
                rhs = parse()
                result = result and rhs
            return result
        if token == "|":
            left = parse()
            right = parse()
            return left or right
        if token == "!":
            operand = parse()
            return not operand
        assert isinstance(token, tuple)
        return _leaf(record, token)

    result = parse()
    assert pos == len(domain), f"Tokens sobrantes: {domain[pos:]}"
    return result


# ---------------------------------------------------------------------------
# Dominios exactos
# ---------------------------------------------------------------------------


def test_obligation_domains_are_exact():
    assert d_pending(TODAY) == [
        "|",
        "&",
        ("state", "=", "pending"),
        "|",
        ("due_date", "=", False),
        ("due_date", ">=", TODAY),
        "&",
        ("state", "=", "overdue"),
        ("due_date", ">=", TODAY),
    ]
    assert d_overdue(TODAY) == [
        "|",
        "&",
        ("state", "=", "overdue"),
        "|",
        ("due_date", "=", False),
        ("due_date", "<", TODAY),
        "&",
        ("state", "=", "pending"),
        ("due_date", "<", TODAY),
    ]
    assert d_paid() == [("state", "=", "paid")]
    assert d_cancelled() == [("state", "=", "cancelled")]


def test_document_domains_are_exact():
    assert d_doc_expired(TODAY) == [
        "|",
        "&",
        ("state", "in", ["valid", "pending", "expired"]),
        ("expiry_date", "!=", False),
        ("expiry_date", "<", TODAY),
        "&",
        ("state", "=", "expired"),
        ("expiry_date", "=", False),
    ]
    assert d_doc_pending(TODAY) == [
        "|",
        ("state", "=", False),
        "|",
        "&",
        ("state", "=", "pending"),
        "|",
        ("expiry_date", "=", False),
        ("expiry_date", ">=", TODAY),
        "&",
        ("state", "=", "expired"),
        ("expiry_date", "!=", False),
        ("expiry_date", ">=", TODAY),
    ]
    assert d_doc_valid(TODAY) == [
        "|",
        "&",
        ("state", "=", "valid"),
        ("expiry_date", "=", False),
        "&",
        ("state", "=", "valid"),
        ("expiry_date", ">=", TODAY),
    ]
    assert d_doc_rejected() == [("state", "=", "rejected")]
    assert d_doc_missing() == [("state", "=", "missing")]


@pytest.mark.parametrize(
    ("state", "due_date", "expected_pending", "expected_overdue"),
    [
        ("pending", None, True, False),
        ("pending", TODAY, True, False),
        ("pending", TOMORROW, True, False),
        ("pending", YESTERDAY, False, True),
        ("overdue", None, False, True),
        ("overdue", TODAY, True, False),
        ("overdue", TOMORROW, True, False),
        ("overdue", YESTERDAY, False, True),
        ("paid", TODAY, False, False),
        ("cancelled", YESTERDAY, False, False),
    ],
)
def test_obligation_domains_are_disjoint_and_semantically_correct(
    state, due_date, expected_pending, expected_overdue
):
    record = {"state": state, "due_date": due_date}
    pending = _eval_domain(d_pending(TODAY), record)
    overdue = _eval_domain(d_overdue(TODAY), record)

    assert not (pending and overdue)
    assert pending is expected_pending
    assert overdue is expected_overdue


@pytest.mark.parametrize(
    ("state", "expiry_date", "expected"),
    [
        (False, YESTERDAY, "pending"),
        ("pending", None, "pending"),
        ("expired", TOMORROW, "pending"),
        ("valid", None, "valid"),
        ("valid", TODAY, "valid"),
        ("valid", TOMORROW, "valid"),
        ("valid", YESTERDAY, "expired"),
        ("pending", YESTERDAY, "expired"),
        ("expired", None, "expired"),
        ("expired", YESTERDAY, "expired"),
        ("rejected", YESTERDAY, "rejected"),
        ("rejected", TOMORROW, "rejected"),
        ("missing", None, "missing"),
    ],
)
def test_document_domains_are_disjoint_and_semantically_correct(
    state, expiry_date, expected
):
    record = {"state": state, "expiry_date": expiry_date}
    matches = {
        name: _eval_domain(domain, record)
        for name, domain in {
            "expired": d_doc_expired(TODAY),
            "pending": d_doc_pending(TODAY),
            "valid": d_doc_valid(TODAY),
            "rejected": d_doc_rejected(),
            "missing": d_doc_missing(),
        }.items()
    }

    assert list(matches.values()).count(True) == 1
    assert [name for name, matched in matches.items() if matched] == [expected]


# ---------------------------------------------------------------------------
# Agregación de obligaciones
# ---------------------------------------------------------------------------


def _obligation_row(
    row_id: int,
    *,
    state: str = "pending",
    due_date: date | None = TOMORROW,
    obligation_type_id: int | None = 1,
    tax_type: str | None = "IVA",
    company_id: int = 10,
    company_name: str = "Compañía Uno",
) -> ObligationRow:
    return ObligationRow(
        id=row_id,
        client_id=100 + row_id,
        company_id=company_id,
        company_name=company_name,
        state=state,
        due_date=due_date,
        obligation_type_id=obligation_type_id,
        tax_type=tax_type,
    )


def test_null_obligation_type_is_not_double_counted():
    rows = [
        _obligation_row(1, obligation_type_id=None, tax_type=None),
        _obligation_row(2),
        _obligation_row(3, state="overdue", due_date=YESTERDAY, tax_type="ISLR"),
        _obligation_row(4, obligation_type_id=None, tax_type=None, company_id=11),
    ]

    counts = aggregate_obligations(rows, TODAY)

    assert counts.total == 4
    assert counts.pending == 3
    assert counts.overdue == 1
    assert counts.null_obligation_type == 2
    # El total activo es la suma de las cubetas disjuntas; null_type no se suma otra vez.
    assert counts.total == counts.pending + counts.overdue
    assert sum(company.total for company in counts.breakdown) == counts.total


def test_tax_filter_excludes_null_type_and_reports_separately():
    rows = [
        _obligation_row(1, obligation_type_id=None, tax_type=None),
        _obligation_row(2, tax_type="IVA"),
        _obligation_row(3, tax_type="ISLR"),
        _obligation_row(4, obligation_type_id=None, tax_type=None),
    ]

    counts = aggregate_obligations(rows, TODAY, tax_type="IVA")

    assert counts.total == 1
    assert counts.pending == 1
    assert counts.overdue == 0
    assert counts.null_obligation_type == 2


def test_compliance_status_uses_effective_precedence():
    assert compliance_status([]) == ComplianceStatus.al_dia
    assert compliance_status(["paid", "cancelled"]) == ComplianceStatus.al_dia
    assert compliance_status(["pending", "paid"]) == ComplianceStatus.pendiente
    assert compliance_status(["pending", "overdue"]) == ComplianceStatus.vencido
    assert compliance_status(["overdue", "pending", "paid"]) == ComplianceStatus.vencido


def test_obligation_order_is_due_date_then_id_with_nulls_last():
    rows = [
        _obligation_row(3, due_date=None),
        _obligation_row(2, due_date=TOMORROW),
        _obligation_row(1, due_date=TOMORROW),
        _obligation_row(4, due_date=YESTERDAY),
    ]

    ordered = order_obligations(rows)

    assert [row.id for row in ordered] == [4, 1, 2, 3]


def test_truncation_keeps_total_independent_from_returned():
    rows = [_obligation_row(index, due_date=TOMORROW + timedelta(days=index)) for index in range(1, 61)]

    counts = aggregate_obligations(rows, TODAY)
    returned, truncated = truncate(rows, 50)

    assert counts.total == 60
    assert len(returned) == 50
    assert truncated is True

    returned_all, not_truncated = truncate(rows[:10], 50)
    assert len(returned_all) == 10
    assert not_truncated is False


# ---------------------------------------------------------------------------
# Documentos, orden y truncado genérico
# ---------------------------------------------------------------------------


def test_document_counts_use_effective_state_and_pending_count():
    rows = [
        DocumentRow(id=1, client_id=1, document_type_id=1, state="valid", expiry_date=None),
        DocumentRow(id=2, client_id=1, document_type_id=2, state="rejected", expiry_date=YESTERDAY),
        DocumentRow(id=3, client_id=1, document_type_id=3, state="missing", expiry_date=None, is_snapshot=True),
        DocumentRow(id=4, client_id=1, document_type_id=4, state=False, expiry_date=None),
        DocumentRow(id=5, client_id=1, document_type_id=5, state="expired", expiry_date=YESTERDAY),
    ]

    counts = count_documents(rows, TODAY)

    assert counts.total == 5
    assert counts.valid == 1
    assert counts.rejected == 1
    assert counts.missing == 1
    assert counts.pending == 1
    assert counts.expired == 1
    assert counts.pending_count == 4


def test_document_order_is_type_then_id():
    rows = [
        DocumentRow(id=9, client_id=1, document_type_id=2, state="valid"),
        DocumentRow(id=3, client_id=1, document_type_id=1, state="valid"),
        DocumentRow(id=1, client_id=1, document_type_id=1, state="valid"),
    ]

    assert [row.id for row in order_documents(rows)] == [1, 3, 9]


def test_alert_order_is_date_desc_nulls_last_then_id():
    early = datetime(2026, 1, 14, 9, 0, tzinfo=timezone.utc)
    late = datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc)
    rows = [
        {"id": 1, "date": early},
        {"id": 2, "date": None},
        {"id": 3, "date": late},
        {"id": 4, "date": late},
    ]

    assert [row["id"] for row in order_alerts(rows)] == [3, 4, 1, 2]


def test_truncate_is_generic_and_rejects_negative_limit():
    assert truncate(range(10), 3) == ([0, 1, 2], True)
    assert truncate([1, 2], 2) == ([1, 2], False)
    with pytest.raises(ValueError):
        truncate([1, 2], -1)
