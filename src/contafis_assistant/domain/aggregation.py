"""Agregación, compliance, orden determinista y truncado sin I/O."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.contracts import CompanyBreakdown
from contafis_assistant.domain.effective_state import (
    effective_document_state,
    effective_obligation_state,
)
from contafis_assistant.domain.enums import (
    ComplianceStatus,
    DocumentState,
    ObligationState,
)

T = TypeVar("T")


class ObligationRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    client_id: int
    company_id: int
    company_name: str = ""
    state: str
    due_date: date | None = None
    obligation_type_id: int | None = None
    tax_type: str | None = None


class DocumentRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    client_id: int
    document_type_id: int
    state: str | bool | None = None
    expiry_date: date | None = None
    is_snapshot: bool = False
    company_id: int | None = None
    company_name: str = ""


class ObligationCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 0
    pending: int = 0
    overdue: int = 0
    paid: int = 0
    cancelled: int = 0
    null_obligation_type: int = 0
    breakdown: list[CompanyBreakdown] = Field(default_factory=list)


class DocumentCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 0
    valid: int = 0
    pending: int = 0
    expired: int = 0
    rejected: int = 0
    missing: int = 0
    pending_count: int = 0


def _field(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _enum_value(value: Any) -> str:
    return value.value if isinstance(value, Enum) else str(value)


def _add_unique(items: list[Any], value: Any) -> None:
    if value not in items:
        items.append(value)


def aggregate_obligations(
    rows: Iterable[ObligationRow],
    today: date,
    *,
    tax_type: str | None = None,
) -> ObligationCounts:
    """Cuenta obligaciones con estados efectivos y cubetas disjuntas.

    Sin filtro de impuesto, las obligaciones sin tipo se incluyen en el total
    activo y se informan en `null_obligation_type`; no se suman dos veces.
    Con filtro de impuesto, las obligaciones sin tipo se excluyen del total
    filtrado y solo se informan en la cubeta separada.
    """
    counts = ObligationCounts()
    buckets: dict[int, dict[str, Any]] = {}

    for row in rows:
        if row.obligation_type_id is None:
            counts.null_obligation_type += 1
            if tax_type is not None:
                continue
        elif tax_type is not None and row.tax_type != tax_type:
            continue

        effective = effective_obligation_state(row.state, row.due_date, today)
        state_value = effective.state

        if state_value == ObligationState.pending.value:
            counts.pending += 1
        elif state_value == ObligationState.overdue.value:
            counts.overdue += 1
        elif state_value == ObligationState.paid.value:
            counts.paid += 1
        elif state_value == ObligationState.cancelled.value:
            counts.cancelled += 1

        is_active = state_value in (
            ObligationState.pending.value,
            ObligationState.overdue.value,
        )

        bucket = buckets.setdefault(
            row.company_id,
            {
                "company_name": row.company_name,
                "total": 0,
                "counts": {},
            },
        )
        if is_active:
            bucket["total"] += 1
        bucket["counts"][state_value] = bucket["counts"].get(state_value, 0) + 1
        if row.obligation_type_id is None:
            bucket["counts"]["null_type"] = bucket["counts"].get("null_type", 0) + 1

    counts.total = counts.pending + counts.overdue
    counts.breakdown = [
        CompanyBreakdown(
            company_id=company_id,
            company_name=bucket["company_name"],
            total=bucket["total"],
            counts=bucket["counts"],
        )
        for company_id, bucket in sorted(buckets.items())
    ]
    return counts


def count_documents(rows: Iterable[DocumentRow], today: date) -> DocumentCounts:
    counts = DocumentCounts()

    for row in rows:
        effective = effective_document_state(
            row.state,
            row.expiry_date,
            today,
            is_snapshot=row.is_snapshot,
        )
        state_value = effective.state
        counts.total += 1

        if state_value == DocumentState.valid.value:
            counts.valid += 1
        elif state_value == DocumentState.pending.value:
            counts.pending += 1
        elif state_value == DocumentState.expired.value:
            counts.expired += 1
        elif state_value == DocumentState.rejected.value:
            counts.rejected += 1
        elif state_value == DocumentState.missing.value:
            counts.missing += 1

    counts.pending_count = counts.total - counts.valid
    return counts


def compliance_status(effective_states: Iterable[str | Enum]) -> ComplianceStatus:
    """Calcula compliance en Python: vencido > pendiente > al_dia."""
    states = {_enum_value(state) for state in effective_states}
    if ObligationState.overdue.value in states:
        return ComplianceStatus.vencido
    if ObligationState.pending.value in states:
        return ComplianceStatus.pendiente
    return ComplianceStatus.al_dia


def order_obligations(rows: Iterable[T]) -> list[T]:
    """Orden determinista `due_date, id`, con nulos al final."""
    return sorted(
        rows,
        key=lambda row: (
            _field(row, "due_date") is None,
            _field(row, "due_date") or date.min,
            _field(row, "id"),
        ),
    )


def order_documents(rows: Iterable[T]) -> list[T]:
    """Orden determinista `document_type_id, id`."""
    return sorted(
        rows,
        key=lambda row: (
            _field(row, "document_type_id"),
            _field(row, "id"),
        ),
    )


def order_alerts(rows: Iterable[T]) -> list[T]:
    """Orden determinista `date desc nulls last, id`."""
    def key(row: T) -> tuple[int, float, int]:
        value = _field(row, "date")
        row_id = _field(row, "id")
        if not value:
            return (1, 0.0, row_id)
        assert isinstance(value, datetime)
        return (0, -value.timestamp(), row_id)

    return sorted(rows, key=key)


def truncate(items: Sequence[T], limit: int) -> tuple[list[T], bool]:
    """Devuelve el detalle limitado y si hubo truncamiento."""
    if limit < 0:
        raise ValueError("limit no puede ser negativo")
    return list(items[:limit]), len(items) > limit
