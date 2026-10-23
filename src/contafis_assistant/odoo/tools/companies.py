"""Agregado por compañía con lectura en lote y exclusión de archivadas."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from contafis_assistant.domain.contracts import (
    CompanyBreakdown,
    CompanyItem,
    CompaniesResult,
)
from contafis_assistant.domain.effective_state import effective_obligation_state
from contafis_assistant.domain.enums import ObligationState, WarningCode
from contafis_assistant.odoo.client import ToolBudget
from contafis_assistant.odoo.tools.common import (
    ALERT_READ_FIELDS,
    CLIENT_READ_FIELDS,
    COMPANY_READ_FIELDS,
    OBLIGATION_READ_FIELDS,
    OdooGateway,
    client_rows_by_id,
    make_warning,
    many2one_id,
    normalize_ids,
    search_read,
)

DETAIL_LIMIT = 50
DEFAULT_DETAIL_LIMIT = DETAIL_LIMIT


def query_companies(
    gateway: OdooGateway,
    *,
    today: date,
    allowed_company_ids: Sequence[int] | None = None,
    detail_limit: int = DEFAULT_DETAIL_LIMIT,
    budget: ToolBudget | None = None,
) -> CompaniesResult:
    """Agrega todas las compañías activas en el alcance permitido.

    Las compañías archivadas se excluyen del agregado. Los contadores salen de
    una lectura mínima en lote de clientes, obligaciones y alertas; no hay
    consultas por compañía ni ``read_group``.
    """
    allowed = (
        normalize_ids(allowed_company_ids)
        if allowed_company_ids is not None
        else []
    )
    if allowed_company_ids is not None and not allowed:
        return _empty_result()

    domain: list[Any] = [("active", "=", True)]
    if allowed_company_ids is not None:
        domain.append(("id", "in", allowed))

    companies = search_read(
        gateway,
        "res.company",
        domain,
        COMPANY_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
        active_test=False,
    )
    active_companies = [
        row
        for row in companies
        if bool(row.get("active", True))
        and many2one_id(row.get("id")) is not None
    ]
    active_companies.sort(key=lambda row: many2one_id(row.get("id")) or 0)
    if not active_companies:
        return _empty_result()

    active_ids = [many2one_id(row.get("id")) for row in active_companies]
    active_ids = [identifier for identifier in active_ids if identifier is not None]

    clients = search_read(
        gateway,
        "l10n.ve.compliance.client",
        [("company_id", "in", active_ids)],
        CLIENT_READ_FIELDS,
        budget=budget,
        allowed_company_ids=active_ids,
    )
    obligations = search_read(
        gateway,
        "l10n.ve.obligation",
        [("client_id.company_id", "in", active_ids)],
        OBLIGATION_READ_FIELDS,
        budget=budget,
        allowed_company_ids=active_ids,
    )
    alerts = search_read(
        gateway,
        "l10n.ve.alert",
        [
            ("client_id.company_id", "in", active_ids),
            ("state", "=", "pending"),
        ],
        ALERT_READ_FIELDS,
        budget=budget,
        allowed_company_ids=active_ids,
    )

    client_map = client_rows_by_id(clients)
    client_company = {
        client_identifier: company_identifier
        for client_identifier, row in client_map.items()
        if (company_identifier := many2one_id(row.get("company_id"))) is not None
        and company_identifier in set(active_ids)
    }

    clients_total: dict[int, int] = defaultdict(int)
    for company_identifier in client_company.values():
        clients_total[company_identifier] += 1

    obligations_by_company: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in obligations:
        row_client_id = many2one_id(row.get("client_id"))
        row_company_id = client_company.get(row_client_id)
        if row_company_id is not None:
            obligations_by_company[row_company_id].append(row)

    alerts_by_company: dict[int, int] = defaultdict(int)
    for row in alerts:
        if row.get("state") != "pending":
            continue
        row_client_id = many2one_id(row.get("client_id"))
        row_company_id = client_company.get(row_client_id)
        if row_company_id is not None:
            alerts_by_company[row_company_id] += 1

    items: list[CompanyItem] = []
    for row in active_companies:
        company_id = many2one_id(row.get("id"))
        if company_id is None:
            continue
        effective_states = [
            effective.state
            for row_obligation in obligations_by_company.get(company_id, [])
            if (effective := _effective(row_obligation, today)) is not None
        ]
        obligations_total = sum(
            1
            for state in effective_states
            if state in (ObligationState.pending.value, ObligationState.overdue.value)
        )
        obligations_overdue = sum(
            1 for state in effective_states if state == ObligationState.overdue.value
        )
        items.append(
            CompanyItem(
                id=company_id,
                name=str(row.get("name") or ""),
                company_archived=False,
                clients_total=clients_total.get(company_id, 0),
                obligations_total=obligations_total,
                obligations_overdue=obligations_overdue,
                alerts_pending=alerts_by_company.get(company_id, 0),
            )
        )

    total = len(items)
    detail = items[:detail_limit]
    truncated = total > len(detail)
    warnings = (
        [
            make_warning(
                WarningCode.truncated,
                context={"returned": len(detail), "total": total},
            )
        ]
        if truncated
        else []
    )

    return CompaniesResult(
        total=total,
        returned=len(detail),
        items=detail,
        breakdown=_build_breakdown(active_companies, obligations_by_company, today),
        truncated=truncated,
        warnings=warnings,
    )


def _effective(row: dict[str, Any], today: date):
    raw_state = row.get("state")
    if not isinstance(raw_state, str):
        return None
    try:
        return effective_obligation_state(
            raw_state,
            _coerce_date(row.get("due_date")),
            today,
        )
    except ValueError:
        return None


def _coerce_date(value: Any) -> date | None:
    if value is None or value is False:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _build_breakdown(
    companies: list[dict[str, Any]],
    obligations_by_company: dict[int, list[dict[str, Any]]],
    today: date,
) -> list[CompanyBreakdown]:
    breakdown: list[CompanyBreakdown] = []
    for row in companies:
        company_id = many2one_id(row.get("id"))
        if company_id is None:
            continue
        counts: dict[str, int] = {}
        total = 0
        for obligation in obligations_by_company.get(company_id, []):
            effective = _effective(obligation, today)
            if effective is None:
                continue
            if effective.state not in (
                ObligationState.pending.value,
                ObligationState.overdue.value,
            ):
                continue
            total += 1
            counts[effective.state] = counts.get(effective.state, 0) + 1
            if many2one_id(obligation.get("obligation_type_id")) is None:
                counts["null_type"] = counts.get("null_type", 0) + 1
        breakdown.append(
            CompanyBreakdown(
                company_id=company_id,
                company_name=str(row.get("name") or ""),
                total=total,
                counts=counts,
            )
        )
    return breakdown


def _empty_result() -> CompaniesResult:
    return CompaniesResult(
        total=0,
        returned=0,
        items=[],
        breakdown=[],
        truncated=False,
        warnings=[make_warning(WarningCode.no_active_companies)],
    )
