"""Consulta de obligaciones con estados efectivos, impuestos y truncado."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

from contafis_assistant.domain.contracts import (
    CompanyBreakdown,
    ObligationItem,
    ObligationsResult,
)
from contafis_assistant.domain.effective_state import effective_obligation_state
from contafis_assistant.domain.enums import ObligationState, WarningCode
from contafis_assistant.odoo.client import ToolBudget
from contafis_assistant.odoo.tools.common import (
    CLIENT_READ_FIELDS,
    COMPANY_READ_FIELDS,
    OBLIGATION_READ_FIELDS,
    OBLIGATION_TYPE_READ_FIELDS,
    OdooGateway,
    client_rows_by_id,
    company_rows_by_id,
    make_warning,
    many2one_id,
    normalize_ids,
    search_read,
)

DETAIL_LIMIT = 50
DEFAULT_DETAIL_LIMIT = DETAIL_LIMIT
DEFAULT_STATES = (ObligationState.pending, ObligationState.overdue)


def obligation_domain(
    *,
    allowed_company_ids: Sequence[int],
    company_id: int | None = None,
    client_id: int | None = None,
    tax_type: str | None = None,
) -> list[Any]:
    """Dominio exacto con filtro de compañía explícito (RF-061)."""
    domain: list[Any] = [
        ("client_id.company_id", "in", [int(value) for value in allowed_company_ids])
    ]
    if company_id is not None:
        domain.append(("client_id.company_id", "=", int(company_id)))
    if client_id is not None:
        domain.append(("client_id", "=", int(client_id)))
    if tax_type is not None:
        domain.append(("obligation_type_id.tax_type", "=", tax_type))
    return domain


def query_obligations(
    gateway: OdooGateway,
    *,
    today: date,
    allowed_company_ids: Sequence[int],
    client_id: int | None = None,
    company_id: int | None = None,
    tax_type: str | None = None,
    states: Iterable[ObligationState] | None = None,
    detail_limit: int = DEFAULT_DETAIL_LIMIT,
    budget: ToolBudget | None = None,
) -> ObligationsResult:
    """Devuelve detalle, total y breakdown en una lectura mínima en lote.

    Los estados por defecto son ``pending`` y ``overdue``; ``paid`` y
    ``cancelled`` solo se incluyen si el llamador los pide. El total se calcula
    con todas las filas leídas, nunca a partir del detalle truncado.
    """
    allowed = normalize_ids(allowed_company_ids)
    if not allowed:
        return ObligationsResult(
            total=0,
            returned=0,
            items=[],
            breakdown=[],
            truncated=False,
            warnings=[make_warning(WarningCode.scope_partial)],
        )

    requested = tuple(states) if states is not None else DEFAULT_STATES
    requested_values = {_state_value(state) for state in requested}
    domain = obligation_domain(
        allowed_company_ids=allowed,
        company_id=company_id,
        client_id=client_id,
        tax_type=tax_type,
    )

    rows = search_read(
        gateway,
        "l10n.ve.obligation",
        domain,
        OBLIGATION_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
    )

    null_rows: list[dict[str, Any]] = []
    if tax_type is not None:
        null_domain = obligation_domain(
            allowed_company_ids=allowed,
            company_id=company_id,
            client_id=client_id,
            tax_type=None,
        )
        null_domain.append(("obligation_type_id", "=", False))
        null_rows = search_read(
            gateway,
            "l10n.ve.obligation",
            null_domain,
            OBLIGATION_READ_FIELDS,
            budget=budget,
            allowed_company_ids=allowed,
        )

    obligation_types = search_read(
        gateway,
        "l10n.ve.obligation.type",
        [],
        OBLIGATION_TYPE_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
    )
    type_map = {
        type_id: row
        for row in obligation_types
        if (type_id := many2one_id(row.get("id"))) is not None
    }

    selected: list[dict[str, Any]] = []
    for row in rows:
        effective = _effective_state(row, today)
        if effective is None:
            continue
        if effective.state not in requested_values:
            continue
        if row.get("client_id") is None:
            continue
        if tax_type is not None and _tax_type(row, type_map) != tax_type:
            continue
        selected.append(row)

    null_count = (
        len(null_rows)
        if tax_type is not None
        else sum(
            1 for row in rows if many2one_id(row.get("obligation_type_id")) is None
        )
    )

    client_ids = sorted(
        {
            row_client_id
            for row in selected
            if (row_client_id := many2one_id(row.get("client_id"))) is not None
        }
    )
    clients = (
        search_read(
            gateway,
            "l10n.ve.compliance.client",
            [
                ("id", "in", client_ids),
                ("company_id", "in", allowed),
            ],
            CLIENT_READ_FIELDS,
            budget=budget,
            allowed_company_ids=allowed,
        )
        if client_ids
        else []
    )
    client_map = client_rows_by_id(clients)

    company_ids = sorted(
        {
            client_company_id
            for client in client_map.values()
            if (client_company_id := many2one_id(client.get("company_id")))
            is not None
        }
    )
    companies = (
        search_read(
            gateway,
            "res.company",
            [("id", "in", company_ids)],
            COMPANY_READ_FIELDS,
            budget=budget,
            allowed_company_ids=allowed,
            active_test=False,
        )
        if company_ids
        else []
    )
    company_map = company_rows_by_id(companies)

    enriched: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for row in selected:
        client_identifier = many2one_id(row.get("client_id"))
        client = client_map.get(client_identifier)
        if client is None:
            continue
        company_identifier = many2one_id(client.get("company_id"))
        company = company_map.get(company_identifier)
        if company is None:
            continue
        enriched.append((row, client, company))

    ordered = sorted(
        enriched,
        key=lambda entry: (
            _coerce_date(entry[0].get("due_date")) is None,
            _coerce_date(entry[0].get("due_date")) or date.min,
            int(entry[0]["id"]),
        ),
    )

    detail = ordered[:detail_limit]
    items = [
        _build_item(row, client, company, type_map, today)
        for row, client, company in detail
    ]

    warnings = []
    if null_count > 0:
        warnings.append(
            make_warning(
                WarningCode.null_obligation_type,
                context={"count": null_count},
            )
        )
    total = len(enriched)
    truncated = total > len(items)
    if truncated:
        warnings.append(
            make_warning(
                WarningCode.truncated,
                context={"returned": len(items), "total": total},
            )
        )

    return ObligationsResult(
        total=total,
        returned=len(items),
        items=items,
        breakdown=_build_breakdown(enriched, today),
        truncated=truncated,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Internos
# ---------------------------------------------------------------------------


def _state_value(state: ObligationState | str) -> str:
    return state.value if isinstance(state, ObligationState) else str(state)


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


def _effective_state(row: dict[str, Any], today: date):
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


def _tax_type(
    row: dict[str, Any],
    type_map: dict[int, dict[str, Any]],
) -> str | None:
    type_id = many2one_id(row.get("obligation_type_id"))
    if type_id is None:
        return None
    return type_map.get(type_id, {}).get("tax_type")


def _build_item(
    row: dict[str, Any],
    client: dict[str, Any],
    company: dict[str, Any],
    type_map: dict[int, dict[str, Any]],
    today: date,
) -> ObligationItem:
    raw_state = str(row.get("state"))
    effective = effective_obligation_state(
        raw_state,
        _coerce_date(row.get("due_date")),
        today,
    )
    type_id = many2one_id(row.get("obligation_type_id"))
    type_row = type_map.get(type_id) if type_id is not None else None

    return ObligationItem(
        id=int(row["id"]),
        client_id=many2one_id(row.get("client_id")) or 0,
        client_name=str(client.get("name") or ""),
        company_id=many2one_id(company.get("id")) or 0,
        company_name=str(company.get("name") or ""),
        obligation_type_id=type_id,
        obligation_type_name=(
            str(type_row.get("name") or "") if type_row is not None else None
        ),
        tax_type=(type_row.get("tax_type") if type_row is not None else None),
        period=row.get("period"),
        due_date=_coerce_date(row.get("due_date")),
        state=ObligationState(raw_state),
        effective_state=ObligationState(effective.state),
        warnings=[make_warning(code) for code in effective.warnings],
    )


def _build_breakdown(
    enriched: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
    today: date,
) -> list[CompanyBreakdown]:
    buckets: dict[int, dict[str, Any]] = {}
    for row, _client, company in enriched:
        company_id = many2one_id(company.get("id"))
        if company_id is None:
            continue
        effective = effective_obligation_state(
            str(row.get("state")),
            _coerce_date(row.get("due_date")),
            today,
        )
        bucket = buckets.setdefault(
            company_id,
            {
                "company_name": str(company.get("name") or ""),
                "total": 0,
                "counts": {},
            },
        )
        bucket["total"] += 1
        bucket["counts"][effective.state] = (
            bucket["counts"].get(effective.state, 0) + 1
        )
        if many2one_id(row.get("obligation_type_id")) is None:
            bucket["counts"]["null_type"] = (
                bucket["counts"].get("null_type", 0) + 1
            )

    return [
        CompanyBreakdown(
            company_id=company_id,
            company_name=bucket["company_name"],
            total=bucket["total"],
            counts=bucket["counts"],
        )
        for company_id, bucket in sorted(buckets.items())
    ]
