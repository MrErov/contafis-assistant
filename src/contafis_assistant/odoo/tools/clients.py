"""Consulta de clientes con contadores agregados en una lectura en lote."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from contafis_assistant.domain.aggregation import compliance_status
from contafis_assistant.domain.contracts import ClientItem, ClientsResult
from contafis_assistant.domain.effective_state import effective_obligation_state
from contafis_assistant.domain.enums import ObligationState, WarningCode
from contafis_assistant.odoo.client import ToolBudget
from contafis_assistant.odoo.tools.common import (
    ALERT_READ_FIELDS,
    CLIENT_READ_FIELDS,
    COMPANY_READ_FIELDS,
    OBLIGATION_READ_FIELDS,
    OdooGateway,
    company_rows_by_id,
    make_warning,
    many2one_id,
    normalize_ids,
    search_read,
)

DETAIL_LIMIT = 50
DEFAULT_DETAIL_LIMIT = DETAIL_LIMIT


def query_clients(
    gateway: OdooGateway,
    *,
    today: date,
    allowed_company_ids: Sequence[int],
    client_id: int | None = None,
    company_id: int | None = None,
    detail_limit: int = DEFAULT_DETAIL_LIMIT,
    budget: ToolBudget | None = None,
) -> ClientsResult:
    """Lista clientes activos y calcula contadores sin consultas por cliente.

    Las obligaciones y alertas se leen una sola vez para todo el alcance y se
    agrupan en Python. ``compliance_status`` nunca se pide ni se filtra en Odoo.
    """
    allowed = normalize_ids(allowed_company_ids)
    if not allowed:
        return _empty_result(WarningCode.no_active_companies)

    companies = search_read(
        gateway,
        "res.company",
        [("id", "in", allowed)],
        COMPANY_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
        active_test=False,
    )
    company_map = company_rows_by_id(companies)
    active_ids = sorted(
        company_key
        for company_key, row in company_map.items()
        if bool(row.get("active", True))
    )
    if not active_ids:
        return _empty_result(WarningCode.no_active_companies)

    if company_id is not None and int(company_id) not in active_ids:
        return _empty_result(WarningCode.company_archived)

    client_domain: list[Any] = [("company_id", "in", active_ids)]
    obligation_domain: list[Any] = [("client_id.company_id", "in", active_ids)]
    alert_domain: list[Any] = [
        ("client_id.company_id", "in", active_ids),
        ("state", "=", "pending"),
    ]
    if company_id is not None:
        client_domain.append(("company_id", "=", int(company_id)))
        obligation_domain.append(("client_id.company_id", "=", int(company_id)))
        alert_domain.append(("client_id.company_id", "=", int(company_id)))
    if client_id is not None:
        client_domain.append(("id", "=", int(client_id)))
        obligation_domain.append(("client_id", "=", int(client_id)))
        alert_domain.append(("client_id", "=", int(client_id)))

    clients = search_read(
        gateway,
        "l10n.ve.compliance.client",
        client_domain,
        CLIENT_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
    )
    obligations = search_read(
        gateway,
        "l10n.ve.obligation",
        obligation_domain,
        OBLIGATION_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
    )
    alerts = search_read(
        gateway,
        "l10n.ve.alert",
        alert_domain,
        ALERT_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
    )

    obligations_by_client: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in obligations:
        row_client_id = many2one_id(row.get("client_id"))
        if row_client_id is not None:
            obligations_by_client[row_client_id].append(row)

    alerts_by_client: dict[int, int] = defaultdict(int)
    for row in alerts:
        if row.get("state") != "pending":
            continue
        row_client_id = many2one_id(row.get("client_id"))
        if row_client_id is not None:
            alerts_by_client[row_client_id] += 1

    items: list[ClientItem] = []
    for row in sorted(
        clients,
        key=lambda candidate: many2one_id(candidate.get("id")) or 0,
    ):
        row_client_id = many2one_id(row.get("id"))
        row_company_id = many2one_id(row.get("company_id"))
        if row_client_id is None or row_company_id is None:
            continue
        company = company_map.get(row_company_id)
        if company is None or not bool(company.get("active", True)):
            continue

        client_obligations = obligations_by_client.get(row_client_id, [])
        effective_states = [
            effective.state
            for row_obligation in client_obligations
            if (effective := _effective(row_obligation, today)) is not None
        ]
        active_count = sum(
            1
            for state in effective_states
            if state in (ObligationState.pending.value, ObligationState.overdue.value)
        )
        overdue_count = sum(
            1 for state in effective_states if state == ObligationState.overdue.value
        )

        items.append(
            ClientItem(
                id=row_client_id,
                name=str(row.get("name") or ""),
                rif=row.get("rif"),
                company_id=row_company_id,
                company_name=str(company.get("name") or ""),
                company_archived=False,
                compliance_status=compliance_status(effective_states),
                obligations_total=active_count,
                obligations_overdue=overdue_count,
                alerts_pending=alerts_by_client.get(row_client_id, 0),
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

    return ClientsResult(
        total=total,
        returned=len(detail),
        items=detail,
        breakdown=[],
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


def _empty_result(code: WarningCode) -> ClientsResult:
    return ClientsResult(
        total=0,
        returned=0,
        items=[],
        breakdown=[],
        truncated=False,
        warnings=[make_warning(code)],
    )
