"""Alertas: estados vigentes, hoy Caracas, orden y sanitización (T04)."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone

from contafis_assistant.domain.enums import AlertState, WarningCode
from contafis_assistant.odoo.tools.alerts import query_alerts, sanitize_alert_message

TODAY = date(2026, 1, 15)


class FakeGateway:
    """Doble del gateway; entrega alertas y datos relacionados sin red."""

    def __init__(self, *, companies, clients, alerts, obligations=None):
        self.companies = companies
        self.clients = clients
        self.alerts = alerts
        self.obligations = obligations or []
        self.calls = []
        self.contexts = []

    def execute(self, call, **kwargs):
        self.calls.append(call)
        self.contexts.append(list(kwargs.get("allowed_company_ids") or []))
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


def client(record_id, name, company_id=1):
    return {"id": record_id, "name": name, "rif": "J-12345678-9", "company_id": company_id}


def alert(
    record_id,
    client_id=100,
    *,
    state="pending",
    date=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
    alert_type="due_soon",
    obligation_id=None,
    document_id=None,
    channel="email",
    message=None,
):
    return {
        "id": record_id,
        "client_id": client_id,
        "obligation_id": obligation_id,
        "document_id": document_id,
        "alert_type": alert_type,
        "date": date,
        "state": state,
        "channel": channel,
        "message": message,
    }


def make_gateway(alerts=None, **kwargs):
    defaults = {
        "companies": [company(1, "Compañía Uno")],
        "clients": [client(100, "Cliente Uno", 1)],
        "alerts": alerts or [],
        "obligations": [],
    }
    defaults.update(kwargs)
    return FakeGateway(**defaults)


def run(gateway, **kwargs):
    return query_alerts(
        gateway,
        today=TODAY,
        allowed_company_ids=[1],
        **kwargs,
    )


def codes(result):
    return {warning.code for warning in result.warnings}


def item_codes(item):
    return {warning.code for warning in item.warnings}


def test_default_states_are_pending_and_sent_done_only_when_explicit():
    gateway = make_gateway(
        alerts=[
            alert(1, state="pending"),
            alert(2, state="sent"),
            alert(3, state="done"),
        ]
    )

    default_result = run(gateway)
    done_result = run(gateway, states=[AlertState.done])

    assert [item.id for item in default_result.items] == [1, 2]
    assert default_result.total == 2
    assert [item.id for item in done_result.items] == [3]
    assert done_result.total == 1


def test_today_uses_semi_open_caracas_window():
    gateway = make_gateway(
        alerts=[
            alert(1, date=datetime(2026, 1, 15, 4, 30, tzinfo=timezone.utc)),
            alert(2, date=datetime(2026, 1, 16, 3, 59, tzinfo=timezone.utc)),
            alert(3, date=datetime(2026, 1, 16, 4, 0, tzinfo=timezone.utc)),
            alert(4, date=datetime(2026, 1, 15, 3, 59, tzinfo=timezone.utc)),
        ]
    )

    result = run(gateway)

    assert {item.id for item in result.items} == {1, 2}
    assert WarningCode.no_alerts_today not in codes(result)


def test_no_alerts_today_returns_last_five_and_warning():
    gateway = make_gateway(
        alerts=[
            alert(index, date=datetime(2026, 1, index, 14, 0, tzinfo=timezone.utc))
            for index in range(1, 9)
        ]
    )

    result = run(gateway)

    assert result.total == 5
    assert result.returned == 5
    assert [item.id for item in result.items] == [8, 7, 6, 5, 4]
    assert WarningCode.no_alerts_today in codes(result)


def test_order_is_date_desc_nulls_last_then_id():
    gateway = make_gateway(
        alerts=[
            alert(2, date=datetime(2026, 1, 14, 12, 0, tzinfo=timezone.utc)),
            alert(1, date=datetime(2026, 1, 14, 12, 0, tzinfo=timezone.utc)),
            alert(3, date=datetime(2026, 1, 13, 12, 0, tzinfo=timezone.utc)),
            alert(4, date=False),
        ]
    )

    result = run(gateway)

    assert [item.id for item in result.items] == [1, 2, 3, 4]


def test_null_alert_date_warns_and_goes_last():
    gateway = make_gateway(
        alerts=[
            alert(1, date=False),
            alert(2, date=datetime(2026, 1, 14, 12, 0, tzinfo=timezone.utc)),
        ]
    )

    result = run(gateway)
    by_id = {item.id: item for item in result.items}

    assert result.items[-1].id == 1
    assert by_id[1].date is None
    assert WarningCode.null_alert_date in item_codes(by_id[1])
    assert by_id[1].warnings[0].message


def test_message_clean_removes_amounts_currency_contacts_and_links():
    message = (
        "Cliente debe pagar Monto: 123,45 USD antes del 15/01/2026. "
        "Contacto: 0414-1234567 / +58 414 1234567. "
        "Email: cobranza@example.com. "
        "WhatsApp: https://wa.me/584141234567?text=hola. "
        "Otro: api.whatsapp.com/send?phone=584141234567. "
        "Tel: tel:+584141234567. Mail: mailto:cobranza@example.com. "
        "También Bs. 1.234,56."
    )
    gateway = make_gateway(alerts=[alert(1, message=message)])

    result = run(gateway)
    clean = result.items[0].message_clean

    assert clean is not None
    for forbidden in (
        "123,45",
        "USD",
        "0414",
        "+58",
        "cobranza@example.com",
        "wa.me",
        "api.whatsapp.com",
        "tel:",
        "mailto:",
        "Bs.",
        "1.234,56",
    ):
        assert forbidden not in clean
    assert "15/01/2026" in clean
    assert "Cliente debe pagar" in clean


def test_message_clean_is_empty_when_nothing_useful_remains():
    gateway = make_gateway(
        alerts=[
            alert(
                1,
                message=(
                    "Monto: 123,45 USD https://wa.me/584141234567 "
                    "tel:+584141234567 mailto:cobranza@example.com"
                ),
            )
        ]
    )

    result = run(gateway)

    assert result.items[0].message_clean is None


def test_sanitize_alert_message_handles_none_and_currency_symbols():
    assert sanitize_alert_message(None) is None
    assert sanitize_alert_message("   ") is None
    assert sanitize_alert_message("Vence el 15/01/2026") == "Vence el 15/01/2026"
    assert "$" not in (sanitize_alert_message("Pago $ 100") or "")
    assert "USD" not in (sanitize_alert_message("Total 100 USD") or "")
    assert sanitize_alert_message("Debe 123,45 ahora") == "Debe ahora"
    assert sanitize_alert_message("Queda 1.234,56 pendiente") == "Queda pendiente"


def test_item_carries_structured_fields_and_caracas_offset():
    gateway = make_gateway(
        alerts=[
            alert(
                1,
                alert_type="overdue",
                obligation_id=7,
                document_id=8,
                channel="whatsapp",
                date=datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc),
            )
        ],
        obligations=[{"id": 7, "due_date": date(2026, 1, 10)}],
    )

    result = run(gateway)
    item = result.items[0]

    assert item.alert_type == "overdue"
    assert item.state is AlertState.pending
    assert item.client_name == "Cliente Uno"
    assert item.company_name == "Compañía Uno"
    assert item.obligation_id == 7
    assert item.document_id == 8
    assert item.due_date == date(2026, 1, 10)
    assert item.channel == "whatsapp"
    assert item.date is not None
    assert item.date.utcoffset().total_seconds() == -4 * 3600
    assert item.date.hour == 10


def test_alert_rpcs_filter_by_company_and_reuse_allowed_context():
    gateway = make_gateway(alerts=[alert(1)])

    run(gateway, states=[AlertState.pending, AlertState.sent, AlertState.done])

    alert_calls = [
        call for call in gateway.calls if call.model == "l10n.ve.alert"
    ]
    assert alert_calls
    assert ("client_id.company_id", "in", [1]) in (
        alert_calls[0].domain or []
    )
    for context in gateway.contexts:
        assert context == [1]
    for call in gateway.calls:
        assert call.method != "read_group"
