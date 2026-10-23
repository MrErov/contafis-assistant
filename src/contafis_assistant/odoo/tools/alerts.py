"""Alertas: estados vigentes, ventana de hoy en Caracas y sanitización (T04)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timezone
from typing import Any

from contafis_assistant.domain.contracts import AlertItem, AlertsResult, Warning
from contafis_assistant.domain.enums import AlertState, WarningCode
from contafis_assistant.domain.time import day_window, to_caracas
from contafis_assistant.odoo.client import ToolBudget
from contafis_assistant.odoo.tools.common import (
    ALERT_DETAIL_READ_FIELDS,
    CLIENT_READ_FIELDS,
    COMPANY_READ_FIELDS,
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
DEFAULT_STATES = (AlertState.pending, AlertState.sent)
FALLBACK_LIMIT = 5

_TEL_URI_RE = re.compile(r"(?i)\b(?:tel|mailto):[^\s]+")
_WA_RE = re.compile(
    r"(?i)(?:\bhttps?://)?\b(?:wa\.me|api\.whatsapp\.com)/[^\s]*"
)
_URL_RE = re.compile(r"(?i)\bhttps?://[^\s]+")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_AMOUNT_PHRASE_RE = re.compile(
    r"(?i)\b(?:monto|importe|total|saldo)\b(?:\s+de)?\s*:?\s*"
    r"(?:us\$|\$|bs\.?|ves|usd|eur)?\s*\d[\d.,]*"
    r"(?:\s*(?:us\$|\$|bs\.?|ves|usd|eur))?"
)
_CURRENCY_NUMBER_RE = re.compile(
    r"(?i)(?:\b(?:us\$|\$|bs\.?|ves|usd|eur)\s*\d[\d.,]*"
    r"|\b\d[\d.,]*\s*(?:us\$|\$|bs\.?|ves|usd|eur)\b)"
)
_CURRENCY_RE = re.compile(r"(?i)(?:US\$|\$|\bUSD\b|\bVES\b|\bEUR\b|\bBs\.?)")
_THOUSANDS_AMOUNT_RE = re.compile(
    r"(?<![\w/])\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?(?![\w/])"
)
_DECIMAL_AMOUNT_RE = re.compile(r"(?<![\w/])\d+[.,]\d{2}(?![\w/])")
_PHONE_RE = re.compile(r"(?<![\w/])(?:\+?\d[\d\s().-]{5,}\d)(?![\w/])")
_SPACE_RE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,.;:])")
_REPEATED_PUNCTUATION_RE = re.compile(r"([,;:])(?:\s*[,;:])+")
_HAS_TEXT_RE = re.compile(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]")


def sanitize_alert_message(message: str | None) -> str | None:
    """Elimina montos, moneda, emails, enlaces y teléfonos del texto."""
    if not isinstance(message, str) or not message.strip():
        return None

    text = _TEL_URI_RE.sub(" ", message)
    text = _WA_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _EMAIL_RE.sub(" ", text)
    text = _AMOUNT_PHRASE_RE.sub(" ", text)
    text = _CURRENCY_NUMBER_RE.sub(" ", text)
    text = _CURRENCY_RE.sub(" ", text)
    text = _THOUSANDS_AMOUNT_RE.sub(" ", text)
    text = _DECIMAL_AMOUNT_RE.sub(" ", text)
    text = _PHONE_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", text)
    text = _REPEATED_PUNCTUATION_RE.sub(r"\1", text)
    text = text.strip(" \t\r\n-–—,.;:()[]")

    if not text or not _HAS_TEXT_RE.search(text):
        return None
    return text


def query_alerts(
    gateway: OdooGateway,
    *,
    today: date,
    allowed_company_ids: Sequence[int],
    client_id: int | None = None,
    company_id: int | None = None,
    states: Iterable[AlertState] | None = None,
    detail_limit: int = DEFAULT_DETAIL_LIMIT,
    budget: ToolBudget | None = None,
) -> AlertsResult:
    """Devuelve alertas de hoy o las últimas cinco, con orden determinista."""
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
    requested = tuple(states) if states is not None else DEFAULT_STATES
    state_values = [_state_value(state) for state in requested]
    state_set = set(state_values)

    alert_domain: list[Any] = [
        ("client_id.company_id", "in", scope_company_ids),
        ("state", "in", state_values),
    ]
    if client_id is not None:
        alert_domain.append(("client_id", "=", int(client_id)))

    alert_rows = search_read(
        gateway,
        "l10n.ve.alert",
        alert_domain,
        ALERT_DETAIL_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
    )
    if not alert_rows:
        return _empty_result(WarningCode.no_alerts_today)

    client_ids = sorted(
        {
            row_client_id
            for row in alert_rows
            if (row_client_id := many2one_id(row.get("client_id"))) is not None
        }
    )
    client_rows = (
        search_read(
            gateway,
            "l10n.ve.compliance.client",
            [
                ("id", "in", client_ids),
                ("company_id", "in", scope_company_ids),
            ],
            CLIENT_READ_FIELDS,
            budget=budget,
            allowed_company_ids=allowed,
        )
        if client_ids
        else []
    )
    client_map = client_rows_by_id(client_rows)

    company_ids = sorted(
        {
            row_company_id
            for client in client_map.values()
            if (row_company_id := many2one_id(client.get("company_id"))) is not None
            and row_company_id in active_ids
        }
    )
    related_companies = (
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
    company_map.update(company_rows_by_id(related_companies))

    obligation_ids = sorted(
        {
            obligation_identifier
            for row in alert_rows
            if (
                obligation_identifier := many2one_id(row.get("obligation_id"))
            )
            is not None
        }
    )
    due_dates: dict[int, date | None] = {}
    if obligation_ids:
        obligation_rows = search_read(
            gateway,
            "l10n.ve.obligation",
            [
                ("id", "in", obligation_ids),
                ("client_id.company_id", "in", scope_company_ids),
            ],
            ["id", "due_date"],
            budget=budget,
            allowed_company_ids=allowed,
        )
        due_dates = {
            obligation_id: _coerce_date(row.get("due_date"))
            for row in obligation_rows
            if (obligation_id := many2one_id(row.get("id"))) is not None
        }

    items: list[AlertItem] = []
    for row in alert_rows:
        if str(row.get("state")) not in state_set:
            continue
        row_client_id = many2one_id(row.get("client_id"))
        if client_id is not None and row_client_id != int(client_id):
            continue
        client = client_map.get(row_client_id)
        if client is None:
            continue
        row_company_id = many2one_id(client.get("company_id"))
        company = company_map.get(row_company_id)
        if company is None or not bool(company.get("active", True)):
            continue
        item = _build_item(row, client, company, due_dates.get(
            many2one_id(row.get("obligation_id"))
        ))
        if item is not None:
            items.append(item)

    items.sort(key=_order_key)
    start, end = day_window(today)
    today_items = [
        item
        for item in items
        if item.date is not None and start <= item.date < end
    ]

    warnings: list[Warning] = []
    if today_items:
        selected = today_items
    else:
        selected = items[:FALLBACK_LIMIT]
        warnings.append(make_warning(WarningCode.no_alerts_today))

    total = len(selected)
    detail = selected[:detail_limit]
    truncated = total > len(detail)
    if truncated:
        warnings.append(
            make_warning(
                WarningCode.truncated,
                context={"returned": len(detail), "total": total},
            )
        )

    return AlertsResult(
        total=total,
        returned=len(detail),
        items=detail,
        breakdown=[],
        truncated=truncated,
        warnings=warnings,
    )


def _build_item(
    row: dict[str, Any],
    client: dict[str, Any],
    company: dict[str, Any],
    due_date: date | None,
) -> AlertItem | None:
    raw_state = row.get("state")
    try:
        state = AlertState(raw_state)
    except ValueError:
        return None
    alert_date = _coerce_datetime(row.get("date"))
    warnings = (
        [make_warning(WarningCode.null_alert_date)]
        if alert_date is None
        else []
    )
    return AlertItem(
        id=int(row.get("id") or 0),
        alert_type=str(row.get("alert_type") or ""),
        state=state,
        date=to_caracas(alert_date) if alert_date is not None else None,
        client_id=many2one_id(client.get("id")) or 0,
        client_name=str(client.get("name") or ""),
        company_id=many2one_id(company.get("id")) or 0,
        company_name=str(company.get("name") or ""),
        obligation_id=many2one_id(row.get("obligation_id")),
        document_id=many2one_id(row.get("document_id")),
        due_date=due_date,
        channel=row.get("channel"),
        message_clean=sanitize_alert_message(row.get("message")),
        warnings=warnings,
    )


def _order_key(item: AlertItem) -> tuple[int, float, int]:
    if item.date is None:
        return (1, 0.0, item.id)
    return (0, -item.date.timestamp(), item.id)


def _state_value(state: AlertState | str) -> str:
    return state.value if isinstance(state, AlertState) else str(state)


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None or value is False:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(
            tzinfo=timezone.utc
        )
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(
            tzinfo=timezone.utc
        )
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


def _empty_result(code: WarningCode) -> AlertsResult:
    return AlertsResult(
        total=0,
        returned=0,
        items=[],
        breakdown=[],
        truncated=False,
        warnings=[make_warning(code)],
    )
