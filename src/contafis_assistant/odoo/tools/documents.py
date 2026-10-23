"""Cartelera y documentos: universo de pares, fallbacks y brechas (T04)."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from contafis_assistant.domain.contracts import (
    DocumentGapItem,
    DocumentsResult,
    Warning,
)
from contafis_assistant.domain.effective_state import effective_document_state
from contafis_assistant.domain.enums import (
    DocumentSource,
    DocumentState,
    WarningCode,
)
from contafis_assistant.odoo.client import ToolBudget
from contafis_assistant.odoo.errors import OdooError
from contafis_assistant.odoo.tools.common import (
    CARTELERA_READ_FIELDS,
    CLIENT_READ_FIELDS,
    COMPANY_READ_FIELDS,
    DOCUMENT_READ_FIELDS,
    DOCUMENT_TYPE_READ_FIELDS,
    OdooGateway,
    company_rows_by_id,
    make_warning,
    many2one_id,
    normalize_ids,
    search_read,
)

DETAIL_LIMIT = 50
DEFAULT_DETAIL_LIMIT = DETAIL_LIMIT
COMPANY_REQUIRED_FOR = "company"


def query_documents(
    gateway: OdooGateway,
    *,
    today: date,
    allowed_company_ids: Sequence[int],
    client_id: int | None = None,
    company_id: int | None = None,
    detail_limit: int = DEFAULT_DETAIL_LIMIT,
    budget: ToolBudget | None = None,
) -> DocumentsResult:
    """Recorre los pares cliente/tipo y resuelve snapshot, vivos o anterior.

    ``total`` es el número de pares del universo, nunca el detalle devuelto.
    ``pending_count`` se recalcula en Python como ``total - valid``.
    """
    allowed = normalize_ids(allowed_company_ids)
    if not allowed:
        return _empty_result(WarningCode.scope_partial)

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

    scope_company_ids = (
        [int(company_id)] if company_id is not None else list(active_ids)
    )
    client_domain: list[Any] = [("company_id", "in", scope_company_ids)]
    if client_id is not None:
        client_domain.append(("id", "=", int(client_id)))

    client_rows = search_read(
        gateway,
        "l10n.ve.compliance.client",
        client_domain,
        CLIENT_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
    )
    clients: dict[int, dict[str, Any]] = {}
    for row in client_rows:
        row_client_id = many2one_id(row.get("id"))
        row_company_id = many2one_id(row.get("company_id"))
        if row_client_id is None or row_company_id not in scope_company_ids:
            continue
        if client_id is not None and row_client_id != int(client_id):
            continue
        clients[row_client_id] = row

    type_rows = search_read(
        gateway,
        "l10n.ve.document.type",
        [("required_for", "=", COMPANY_REQUIRED_FOR)],
        DOCUMENT_TYPE_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
    )
    document_types = {
        type_id: row
        for row in type_rows
        if (type_id := many2one_id(row.get("id"))) is not None
        and row.get("required_for") == COMPANY_REQUIRED_FOR
    }
    document_types = dict(sorted(document_types.items()))

    if not clients or not document_types:
        return DocumentsResult(
            total=0,
            returned=0,
            items=[],
            breakdown=[],
            pending_count=0,
            truncated=False,
        )

    pairs = [
        (row_client_id, type_id)
        for row_client_id in sorted(clients)
        for type_id in document_types
    ]
    pair_client_ids = sorted({entry[0] for entry in pairs})
    type_ids = list(document_types)

    snapshot_domain: list[Any] = [
        ("client_id.company_id", "in", scope_company_ids),
        ("year", "=", today.year),
        ("month", "=", str(today.month)),
        ("client_id", "in", pair_client_ids),
        ("document_type_id", "in", type_ids),
    ]
    snapshot_rows = search_read(
        gateway,
        "l10n.ve.cartelera.status",
        snapshot_domain,
        CARTELERA_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
    )
    current: dict[tuple[int, int], dict[str, Any]] = {}
    for row in snapshot_rows:
        row_client_id = many2one_id(row.get("client_id"))
        type_id = many2one_id(row.get("document_type_id"))
        if row_client_id in clients and type_id in document_types:
            key = (row_client_id, type_id)
            previous = current.get(key)
            if previous is None or int(row.get("id") or 0) >= int(
                previous.get("id") or 0
            ):
                current[key] = row

    current_as_of = {
        row_client_id: _snapshot_as_of(row, today)
        for (row_client_id, _type_id), row in current.items()
    }
    snapshot_clients = {entry[0] for entry in current}
    missing = [pair for pair in pairs if pair not in current]
    warnings: list[Warning] = []

    if missing and snapshot_clients:
        if any(pair[0] in snapshot_clients for pair in missing):
            warnings.append(make_warning(WarningCode.cartelera_snapshot_incomplete))

    live_failed = False
    live_map: dict[tuple[int, int], dict[str, Any]] = {}
    missing_set = set(missing)
    if missing:
        live_domain: list[Any] = [
            ("client_id.company_id", "in", scope_company_ids),
            ("client_id", "in", sorted({pair[0] for pair in missing})),
            ("document_type_id", "in", sorted({pair[1] for pair in missing})),
        ]
        try:
            live_rows = search_read(
                gateway,
                "l10n.ve.document",
                live_domain,
                DOCUMENT_READ_FIELDS,
                budget=budget,
                allowed_company_ids=allowed,
            )
        except OdooError:
            live_failed = True
            live_rows = []

        for row in live_rows:
            key = (
                many2one_id(row.get("client_id")),
                many2one_id(row.get("document_type_id")),
            )
            if key in missing_set and key not in current:
                previous = live_map.get(key)
                if previous is None or _live_sort_key(row) >= _live_sort_key(
                    previous
                ):
                    live_map[key] = row

        if not live_failed:
            warnings.append(make_warning(WarningCode.cartelera_fallback_live_documents))

    previous_map: dict[tuple[int, int], dict[str, Any]] = {}
    if live_failed and missing:
        previous_rows = _previous_snapshots(
            gateway,
            today=today,
            scope_company_ids=scope_company_ids,
            client_ids=sorted({pair[0] for pair in missing}),
            type_ids=sorted({pair[1] for pair in missing}),
            budget=budget,
            allowed_company_ids=allowed,
        )
        previous_map = _select_previous(previous_rows, today, missing)
        if previous_map:
            warnings.append(
                make_warning(WarningCode.cartelera_fallback_previous_snapshot)
            )

    items: list[DocumentGapItem] = []
    unresolved = 0
    for pair in pairs:
        row_client_id, type_id = pair
        client = clients[row_client_id]
        company = company_map.get(many2one_id(client.get("company_id")) or 0)
        if company is None:
            continue
        type_row = document_types[type_id]

        current_row = current.get(pair)
        if current_row is not None:
            items.append(
                _snapshot_item(
                    current_row,
                    client=client,
                    company=company,
                    type_row=type_row,
                    today=today,
                    source=DocumentSource.current_snapshot,
                )
            )
            continue

        if not live_failed:
            live_row = live_map.get(pair)
            if live_row is not None:
                items.append(
                    _live_item(
                        live_row,
                        client=client,
                        company=company,
                        type_row=type_row,
                        today=today,
                    )
                )
            else:
                source = (
                    DocumentSource.current_snapshot
                    if row_client_id in snapshot_clients
                    else DocumentSource.live_document
                )
                as_of = current_as_of.get(row_client_id, today)
                items.append(
                    _missing_item(
                        client=client,
                        company=company,
                        type_row=type_row,
                        today=today,
                        source=source,
                        as_of=as_of,
                    )
                )
            continue

        previous_row = previous_map.get(pair)
        if previous_row is not None:
            items.append(
                _snapshot_item(
                    previous_row,
                    client=client,
                    company=company,
                    type_row=type_row,
                    today=today,
                    source=DocumentSource.previous_snapshot,
                )
            )
        else:
            unresolved += 1

    items.sort(key=lambda item: (item.document_type_id, item.id, item.client_id))
    total = len(pairs) if unresolved == 0 else None
    pending_count = (
        total - sum(1 for item in items if item.state is DocumentState.valid)
        if total is not None
        else None
    )

    if unresolved:
        warnings.append(
            make_warning(
                WarningCode.count_unavailable,
                context={"pairs": unresolved},
            )
        )

    detail = items[:detail_limit]
    truncated = unresolved > 0 or len(items) > len(detail)
    if len(items) > len(detail):
        warnings.append(
            make_warning(
                WarningCode.truncated,
                context={"returned": len(detail), "total": len(items)},
            )
        )

    return DocumentsResult(
        total=total,
        returned=len(detail),
        items=detail,
        breakdown=[],
        pending_count=pending_count,
        truncated=truncated,
        warnings=warnings,
    )


def _previous_snapshots(
    gateway: OdooGateway,
    *,
    today: date,
    scope_company_ids: list[int],
    client_ids: list[int],
    type_ids: list[int],
    budget: ToolBudget | None,
    allowed_company_ids: list[int],
) -> list[dict[str, Any]]:
    if not client_ids or not type_ids:
        return []
    domain: list[Any] = [
        ("client_id.company_id", "in", scope_company_ids),
        ("year", "<=", today.year),
        ("client_id", "in", client_ids),
        ("document_type_id", "in", type_ids),
    ]
    try:
        return search_read(
            gateway,
            "l10n.ve.cartelera.status",
            domain,
            CARTELERA_READ_FIELDS,
            budget=budget,
            allowed_company_ids=allowed_company_ids,
        )
    except OdooError:
        return []


def _select_previous(
    rows: list[dict[str, Any]],
    today: date,
    missing: list[tuple[int, int]],
) -> dict[tuple[int, int], dict[str, Any]]:
    missing_keys = set(missing)
    selected: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        key = (
            many2one_id(row.get("client_id")),
            many2one_id(row.get("document_type_id")),
        )
        if key not in missing_keys:
            continue
        year = _as_int(row.get("year"))
        month = _as_int(row.get("month"))
        if year is None or month is None:
            continue
        if (year, month) >= (today.year, today.month):
            continue
        previous = selected.get(key)
        if previous is None or _previous_sort_key(row) >= _previous_sort_key(
            previous
        ):
            selected[key] = row
    return selected


def _previous_sort_key(row: dict[str, Any]) -> tuple[int, int, int]:
    return (
        _as_int(row.get("year")) or 0,
        _as_int(row.get("month")) or 0,
        int(row.get("id") or 0),
    )


def _live_sort_key(row: dict[str, Any]) -> tuple[bool, date, int]:
    expiry = _coerce_date(row.get("expiry_date"))
    return (
        expiry is not None,
        expiry or date.min,
        int(row.get("id") or 0),
    )


def _snapshot_as_of(row: dict[str, Any], today: date) -> date:
    generated = _coerce_date(row.get("date_generated"))
    if generated is not None:
        return generated
    year = _as_int(row.get("year"))
    month = _as_int(row.get("month"))
    if year is None or month is None:
        return today
    if year == today.year and month == today.month:
        return today
    return date(year, month, monthrange(year, month)[1])


def _snapshot_item(
    row: dict[str, Any],
    *,
    client: dict[str, Any],
    company: dict[str, Any],
    type_row: dict[str, Any],
    today: date,
    source: DocumentSource,
) -> DocumentGapItem:
    raw_state = row.get("state")
    stored_state = _stored_state(raw_state)
    expiry_date = _coerce_date(row.get("expiry_date"))
    effective = effective_document_state(
        raw_state,
        expiry_date,
        today,
        is_snapshot=True,
    )
    return DocumentGapItem(
        id=int(row.get("id") or 0),
        document_id=many2one_id(row.get("document_id")),
        client_id=many2one_id(client.get("id")) or 0,
        client_name=str(client.get("name") or ""),
        company_id=many2one_id(company.get("id")) or 0,
        company_name=str(company.get("name") or ""),
        document_type_id=many2one_id(type_row.get("id")) or 0,
        document_type_code=str(type_row.get("code") or ""),
        document_type_name=str(type_row.get("name") or ""),
        state=DocumentState(effective.state),
        stored_state=stored_state,
        expiry_date=expiry_date,
        source=source,
        as_of=_snapshot_as_of(row, today),
        warnings=[make_warning(code) for code in effective.warnings],
    )


def _live_item(
    row: dict[str, Any],
    *,
    client: dict[str, Any],
    company: dict[str, Any],
    type_row: dict[str, Any],
    today: date,
) -> DocumentGapItem:
    raw_state = row.get("state")
    stored_state = _stored_state(raw_state)
    expiry_date = _coerce_date(row.get("expiry_date"))
    effective = effective_document_state(
        raw_state,
        expiry_date,
        today,
        is_snapshot=False,
    )
    document_identifier = many2one_id(row.get("id"))
    return DocumentGapItem(
        id=document_identifier or 0,
        document_id=document_identifier,
        client_id=many2one_id(client.get("id")) or 0,
        client_name=str(client.get("name") or ""),
        company_id=many2one_id(company.get("id")) or 0,
        company_name=str(company.get("name") or ""),
        document_type_id=many2one_id(type_row.get("id")) or 0,
        document_type_code=str(type_row.get("code") or ""),
        document_type_name=str(type_row.get("name") or ""),
        state=DocumentState(effective.state),
        stored_state=stored_state,
        expiry_date=expiry_date,
        source=DocumentSource.live_document,
        as_of=today,
        warnings=[make_warning(code) for code in effective.warnings],
    )


def _missing_item(
    *,
    client: dict[str, Any],
    company: dict[str, Any],
    type_row: dict[str, Any],
    today: date,
    source: DocumentSource,
    as_of: date,
) -> DocumentGapItem:
    effective = effective_document_state(
        DocumentState.missing.value,
        None,
        today,
        is_snapshot=True,
    )
    return DocumentGapItem(
        id=0,
        document_id=None,
        client_id=many2one_id(client.get("id")) or 0,
        client_name=str(client.get("name") or ""),
        company_id=many2one_id(company.get("id")) or 0,
        company_name=str(company.get("name") or ""),
        document_type_id=many2one_id(type_row.get("id")) or 0,
        document_type_code=str(type_row.get("code") or ""),
        document_type_name=str(type_row.get("name") or ""),
        state=DocumentState(effective.state),
        stored_state=None,
        expiry_date=None,
        source=source,
        as_of=as_of,
        warnings=[make_warning(code) for code in effective.warnings],
    )


def _stored_state(value: Any) -> DocumentState | None:
    if value is False or value is None:
        return None
    return DocumentState(value)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
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


def _empty_result(code: WarningCode) -> DocumentsResult:
    return DocumentsResult(
        total=0,
        returned=0,
        items=[],
        breakdown=[],
        pending_count=0,
        truncated=False,
        warnings=[make_warning(code)],
    )
