"""Reloj Caracas, ventanas semiabiertas y Tabla 9.3-A (T01)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from contafis_assistant.domain.effective_state import (
    effective_document_state,
    effective_obligation_state,
)
from contafis_assistant.domain.time import (
    CARACAS,
    Clock,
    day_window,
    format_date,
    to_caracas,
    today_caracas,
    week_window,
)
from contafis_assistant.domain.enums import (
    DocumentState,
    ObligationState,
    WarningCode,
)

TODAY = date(2026, 1, 15)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


# ---------------------------------------------------------------------------
# Reloj y ventanas
# ---------------------------------------------------------------------------


def test_today_caracas_does_not_advance_on_2330_last_day():
    # 23:30 del 31/01/2026 en Caracas == 03:30 UTC del 01/02/2026.
    utc_now = datetime(2026, 2, 1, 3, 30, tzinfo=timezone.utc)

    assert today_caracas(utc_now) == date(2026, 1, 31)
    assert Clock(fixed=utc_now).today_caracas() == date(2026, 1, 31)


def test_today_caracas_advances_on_0030_day_one():
    # 00:30 del 01/02/2026 en Caracas == 04:30 UTC del 01/02/2026.
    utc_now = datetime(2026, 2, 1, 4, 30, tzinfo=timezone.utc)

    assert today_caracas(utc_now) == date(2026, 2, 1)
    assert Clock(fixed=utc_now).today_caracas() == date(2026, 2, 1)


def test_to_caracas_converts_utc():
    utc_now = datetime(2026, 1, 15, 4, 0, tzinfo=timezone.utc)
    local = to_caracas(utc_now)

    assert local.tzinfo == CARACAS
    assert local.hour == 0


def test_day_window_is_semi_open():
    start, end = day_window(TODAY)

    assert start == datetime(2026, 1, 15, 0, 0, tzinfo=CARACAS)
    assert end == datetime(2026, 1, 16, 0, 0, tzinfo=CARACAS)
    assert end - start == timedelta(days=1)
    assert start <= datetime(2026, 1, 15, 23, 59, tzinfo=CARACAS) < end


def test_week_window_is_semi_open_from_monday():
    start, end = week_window(date(2026, 1, 14))  # miércoles

    assert start == datetime(2026, 1, 12, 0, 0, tzinfo=CARACAS)
    assert end == datetime(2026, 1, 19, 0, 0, tzinfo=CARACAS)
    assert end - start == timedelta(days=7)


def test_format_date_is_dd_mm_yyyy():
    assert format_date(date(2026, 1, 5)) == "05/01/2026"
    assert format_date(None) is None


# ---------------------------------------------------------------------------
# Tabla 9.3-A — obligaciones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "due_date", "expected", "warnings"),
    [
        (ObligationState.paid, None, ObligationState.paid, set()),
        (ObligationState.cancelled, YESTERDAY, ObligationState.cancelled, set()),
        (ObligationState.pending, None, ObligationState.pending, {WarningCode.no_due_date}),
        (ObligationState.overdue, None, ObligationState.overdue, {WarningCode.no_due_date}),
        (
            ObligationState.overdue,
            TOMORROW,
            ObligationState.pending,
            {WarningCode.effective_state_adjusted},
        ),
        (
            ObligationState.overdue,
            TODAY,
            ObligationState.pending,
            {WarningCode.effective_state_adjusted},
        ),
        (
            ObligationState.pending,
            YESTERDAY,
            ObligationState.overdue,
            {WarningCode.effective_state_adjusted},
        ),
        (ObligationState.pending, TODAY, ObligationState.pending, set()),
        (ObligationState.pending, TOMORROW, ObligationState.pending, set()),
        (ObligationState.overdue, YESTERDAY, ObligationState.overdue, set()),
    ],
)
def test_effective_obligation_state_table(stored, due_date, expected, warnings):
    result = effective_obligation_state(stored, due_date, TODAY)

    assert result.state == expected
    assert set(result.warnings) == warnings


# ---------------------------------------------------------------------------
# Tabla 9.3-A — documentos y snapshots
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expiry_date", "expected", "warnings", "is_snapshot"),
    [
        (DocumentState.rejected, YESTERDAY, DocumentState.rejected, set(), False),
        (DocumentState.rejected, TOMORROW, DocumentState.rejected, set(), False),
        (DocumentState.rejected, None, DocumentState.rejected, set(), False),
        (DocumentState.missing, YESTERDAY, DocumentState.missing, set(), True),
        (DocumentState.missing, None, DocumentState.missing, set(), True),
        (False, YESTERDAY, DocumentState.pending, {WarningCode.null_document_state}, False),
        (None, TOMORROW, DocumentState.pending, {WarningCode.null_document_state}, True),
        (DocumentState.valid, None, DocumentState.valid, {WarningCode.no_expiry_date}, False),
        (DocumentState.pending, None, DocumentState.pending, {WarningCode.no_expiry_date}, False),
        (DocumentState.expired, None, DocumentState.expired, {WarningCode.no_expiry_date}, True),
        (
            DocumentState.valid,
            YESTERDAY,
            DocumentState.expired,
            {WarningCode.effective_state_adjusted},
            False,
        ),
        (
            DocumentState.pending,
            YESTERDAY,
            DocumentState.expired,
            {WarningCode.effective_state_adjusted},
            True,
        ),
        (DocumentState.expired, YESTERDAY, DocumentState.expired, set(), False),
        (
            DocumentState.expired,
            TODAY,
            DocumentState.pending,
            {WarningCode.effective_state_adjusted},
            True,
        ),
        (
            DocumentState.expired,
            TOMORROW,
            DocumentState.pending,
            {WarningCode.effective_state_adjusted},
            False,
        ),
        (DocumentState.valid, TODAY, DocumentState.valid, set(), False),
        (DocumentState.valid, TOMORROW, DocumentState.valid, set(), False),
        (DocumentState.pending, TOMORROW, DocumentState.pending, set(), True),
    ],
)
def test_effective_document_state_table(
    stored, expiry_date, expected, warnings, is_snapshot
):
    result = effective_document_state(
        stored, expiry_date, TODAY, is_snapshot=is_snapshot
    )

    assert result.state == expected
    assert set(result.warnings) == warnings
