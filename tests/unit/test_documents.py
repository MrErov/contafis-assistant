"""Cartelera y documentos: pares, snapshots, fallbacks y pending_count (T04)."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone

from contafis_assistant.domain.enums import (
    DocumentSource,
    DocumentState,
    WarningCode,
)
from contafis_assistant.domain.time import today_caracas
from contafis_assistant.odoo.errors import OdooTransientError
from contafis_assistant.odoo.tools.documents import query_documents

TODAY = date(2026, 1, 15)
TOMORROW = date(2026, 1, 16)


class FakeGateway:
    """Doble del gateway: filtra snapshots por dominio y registra cada RPC."""

    def __init__(
        self,
        *,
        companies,
        clients,
        types,
        snapshots=None,
        documents=None,
        documents_error=None,
    ):
        self.companies = companies
        self.clients = clients
        self.types = types
        self.snapshots = snapshots or []
        self.documents = documents or []
        self.documents_error = documents_error
        self.calls = []
        self.contexts = []

    def execute(self, call, **kwargs):
        self.calls.append(call)
        self.contexts.append(list(kwargs.get("allowed_company_ids") or []))
        if call.model == "res.company":
            return deepcopy(self.companies)
        if call.model == "l10n.ve.compliance.client":
            return deepcopy(self.clients)
        if call.model == "l10n.ve.document.type":
            return deepcopy(self.types)
        if call.model == "l10n.ve.document":
            if self.documents_error is not None:
                raise self.documents_error
            return deepcopy(self.documents)
        if call.model == "l10n.ve.cartelera.status":
            return deepcopy(_filter_snapshots(self.snapshots, call.domain))
        raise AssertionError(f"modelo inesperado: {call.model}")


def _filter_snapshots(rows, domain):
    result = list(rows)
    for token in domain or []:
        if not isinstance(token, tuple):
            continue
        field, operator, value = token
        if field == "year" and operator == "=":
            result = [row for row in result if row.get("year") == value]
        elif field == "year" and operator == "<=":
            result = [row for row in result if (row.get("year") or 0) <= value]
        elif field == "month" and operator == "=":
            result = [row for row in result if str(row.get("month")) == value]
    return result


def company(record_id, name, *, active=True):
    return {"id": record_id, "name": name, "active": active}


def client(record_id, name, company_id=1):
    return {"id": record_id, "name": name, "rif": "J-12345678-9", "company_id": company_id}


def doc_type(record_id, code, name, *, required_for="company"):
    return {
        "id": record_id,
        "code": code,
        "name": name,
        "required_for": required_for,
    }


def snapshot(
    record_id,
    client_id,
    document_type_id,
    *,
    year=2026,
    month="1",
    state="valid",
    document_id=None,
    expiry_date=TOMORROW,
    date_generated=None,
):
    return {
        "id": record_id,
        "client_id": client_id,
        "document_type_id": document_type_id,
        "year": year,
        "month": month,
        "state": state,
        "document_id": document_id,
        "expiry_date": expiry_date,
        "date_generated": date_generated,
    }


def document(record_id, client_id, document_type_id, *, state="valid", expiry_date=TOMORROW):
    return {
        "id": record_id,
        "client_id": client_id,
        "document_type_id": document_type_id,
        "state": state,
        "expiry_date": expiry_date,
    }


def make_gateway(**kwargs):
    defaults = {
        "companies": [company(1, "Compañía Uno"), company(2, "Compañía Dos")],
        "clients": [client(100, "Cliente Uno", 1), client(200, "Cliente Dos", 2)],
        "types": [
            doc_type(10, "C01", "RIF"),
            doc_type(11, "C02", "Certificado"),
            doc_type(99, "P01", "Partner", required_for="partner"),
        ],
    }
    defaults.update(kwargs)
    return FakeGateway(**defaults)


def run(gateway, **kwargs):
    return query_documents(
        gateway,
        today=TODAY,
        allowed_company_ids=[1, 2],
        **kwargs,
    )


def codes(result):
    return {warning.code for warning in result.warnings}


def test_universe_is_active_clients_times_company_document_types():
    gateway = make_gateway(
        snapshots=[
            snapshot(1, 100, 10, document_id=501, date_generated=datetime(2026, 1, 10, 12)),
            snapshot(2, 100, 11, document_id=502, date_generated=datetime(2026, 1, 10, 12)),
            snapshot(3, 200, 10, document_id=503, date_generated=datetime(2026, 1, 10, 12)),
            snapshot(4, 200, 11, document_id=504, date_generated=datetime(2026, 1, 10, 12)),
        ]
    )

    result = run(gateway)

    assert result.kind == "documents"
    assert result.total == 4
    assert result.returned == 4
    assert result.pending_count == 0
    assert result.truncated is False
    assert result.source == "odoo"
    assert {item.source for item in result.items} == {DocumentSource.current_snapshot}
    assert {item.document_type_id for item in result.items} == {10, 11}
    assert {item.document_id for item in result.items} == {501, 502, 503, 504}
    assert result.items[0].as_of == date(2026, 1, 10)

    type_calls = [
        call for call in gateway.calls if call.model == "l10n.ve.document.type"
    ]
    assert type_calls
    assert ("required_for", "=", "company") in (type_calls[0].domain or [])


def test_incomplete_snapshot_is_completed_with_live_documents():
    gateway = make_gateway(
        clients=[client(100, "Cliente Uno", 1)],
        snapshots=[snapshot(1, 100, 10, document_id=501)],
        documents=[document(601, 100, 11, state="pending", expiry_date=TOMORROW)],
    )

    result = run(gateway)
    by_type = {item.document_type_id: item for item in result.items}

    assert result.total == 2
    assert result.pending_count == 1
    assert by_type[10].source is DocumentSource.current_snapshot
    assert by_type[11].source is DocumentSource.live_document
    assert by_type[11].id == 601
    assert by_type[11].document_id == 601
    assert by_type[11].state is DocumentState.pending
    assert by_type[11].as_of == TODAY
    assert WarningCode.cartelera_snapshot_incomplete in codes(result)
    assert WarningCode.cartelera_fallback_live_documents in codes(result)


def test_absent_snapshot_uses_live_documents_and_materializes_missing_pairs():
    gateway = make_gateway(
        clients=[client(100, "Cliente Uno", 1)],
        snapshots=[],
        documents=[document(601, 100, 10, state="valid", expiry_date=TOMORROW)],
    )

    result = run(gateway)
    by_type = {item.document_type_id: item for item in result.items}

    assert result.total == 2
    assert result.pending_count == 1
    assert by_type[10].source is DocumentSource.live_document
    assert by_type[11].source is DocumentSource.live_document
    assert by_type[11].id == 0
    assert by_type[11].document_id is None
    assert by_type[11].state is DocumentState.missing
    assert by_type[11].stored_state is None
    assert by_type[11].as_of == TODAY
    assert WarningCode.cartelera_fallback_live_documents in codes(result)


def test_live_failure_uses_previous_snapshot():
    gateway = make_gateway(
        clients=[client(100, "Cliente Uno", 1)],
        types=[doc_type(10, "C01", "RIF")],
        snapshots=[
            snapshot(
                7,
                100,
                10,
                year=2025,
                month="12",
                document_id=501,
                date_generated=datetime(2025, 12, 31, 20),
            )
        ],
        documents_error=OdooTransientError("timeout"),
    )

    result = run(gateway)

    assert result.total == 1
    assert result.returned == 1
    assert result.pending_count == 0
    assert result.items[0].source is DocumentSource.previous_snapshot
    assert result.items[0].id == 7
    assert result.items[0].document_id == 501
    assert result.items[0].as_of == date(2025, 12, 31)
    assert WarningCode.cartelera_fallback_previous_snapshot in codes(result)


def test_live_failure_without_previous_snapshot_never_reports_zero():
    gateway = make_gateway(
        clients=[client(100, "Cliente Uno", 1)],
        types=[doc_type(10, "C01", "RIF")],
        snapshots=[],
        documents_error=OdooTransientError("timeout"),
    )

    result = run(gateway)

    assert result.total is None
    assert result.truncated is True
    assert result.pending_count is None
    assert result.items == []
    assert WarningCode.count_unavailable in codes(result)


def test_rejected_and_missing_count_as_gaps():
    gateway = make_gateway(
        clients=[client(100, "Cliente Uno", 1)],
        types=[
            doc_type(10, "C01", "RIF"),
            doc_type(11, "C02", "Certificado"),
            doc_type(12, "C03", "Solvencia"),
        ],
        snapshots=[
            snapshot(1, 100, 10, state="valid", document_id=501),
            snapshot(2, 100, 11, state="rejected", expiry_date=TOMORROW),
            snapshot(3, 100, 12, state="missing", document_id=None, expiry_date=None),
        ],
    )

    result = run(gateway)
    by_type = {item.document_type_id: item for item in result.items}

    assert result.total == 3
    assert result.pending_count == 2
    assert by_type[10].state is DocumentState.valid
    assert by_type[11].state is DocumentState.rejected
    assert by_type[12].state is DocumentState.missing


def test_pending_count_is_recalculated_for_all_pairs_not_the_truncated_detail():
    clients = [client(index, f"Cliente {index}", 1) for index in range(1, 61)]
    snapshots = [
        snapshot(
            index,
            index,
            10,
            state="missing" if index <= 10 else "valid",
            document_id=None if index <= 10 else 500 + index,
        )
        for index in range(1, 61)
    ]
    gateway = make_gateway(
        clients=clients,
        types=[doc_type(10, "C01", "RIF")],
        snapshots=snapshots,
    )

    result = run(gateway)

    assert result.total == 60
    assert result.returned == 50
    assert len(result.items) == 50
    assert result.pending_count == 10
    assert result.truncated is True
    assert WarningCode.truncated in codes(result)


def test_month_filter_uses_string_and_caracas_calendar():
    gateway = make_gateway(
        clients=[client(100, "Cliente Uno", 1)],
        types=[doc_type(10, "C01", "RIF")],
        snapshots=[
            snapshot(1, 100, 10, year=2026, month="1", document_id=501),
            snapshot(2, 100, 10, year=2026, month="2", document_id=502),
        ],
    )

    late_last_day = today_caracas(datetime(2026, 2, 1, 3, 30, tzinfo=timezone.utc))
    early_first_day = today_caracas(datetime(2026, 2, 1, 4, 30, tzinfo=timezone.utc))

    january = query_documents(
        gateway,
        today=late_last_day,
        allowed_company_ids=[1, 2],
    )
    february = query_documents(
        gateway,
        today=early_first_day,
        allowed_company_ids=[1, 2],
    )

    assert late_last_day == date(2026, 1, 31)
    assert early_first_day == date(2026, 2, 1)
    assert january.items[0].document_id == 501
    assert february.items[0].document_id == 502

    for call in gateway.calls:
        if call.model == "l10n.ve.cartelera.status":
            months = [
                token[2]
                for token in (call.domain or [])
                if isinstance(token, tuple) and token[0] == "month"
            ]
            assert all(isinstance(value, str) for value in months)
            assert ("month", "=", "1") in (call.domain or []) or (
                "month",
                "=",
                "2",
            ) in (call.domain or [])


def test_every_business_rpc_filters_by_company_and_reuses_allowed_context():
    gateway = make_gateway(
        clients=[client(100, "Cliente Uno", 1)],
        snapshots=[snapshot(1, 100, 10, document_id=501)],
        documents=[document(601, 100, 11)],
    )

    run(gateway)

    for call in gateway.calls:
        if call.model in {
            "l10n.ve.compliance.client",
            "l10n.ve.document",
            "l10n.ve.cartelera.status",
        }:
            assert ("client_id.company_id", "in", [1, 2]) in (call.domain or []) or (
                "company_id",
                "in",
                [1, 2],
            ) in (call.domain or [])
        assert call.method != "read_group"

    for context in gateway.contexts:
        assert context == [1, 2]


def test_archived_company_is_excluded_from_the_cartelera_universe():
    gateway = make_gateway(
        companies=[
            company(1, "Compañía Uno"),
            company(2, "Compañía Dos", active=False),
        ],
        clients=[client(100, "Cliente Uno", 1), client(200, "Cliente Dos", 2)],
        types=[doc_type(10, "C01", "RIF")],
        snapshots=[snapshot(1, 100, 10, document_id=501)],
    )

    result = run(gateway)

    assert result.total == 1
    assert [item.client_id for item in result.items] == [100]
