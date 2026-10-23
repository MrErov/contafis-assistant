"""Estado efectivo documental: nulos, vencimiento y precedencias (T04)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from contafis_assistant.domain.effective_state import effective_document_state
from contafis_assistant.domain.enums import DocumentState, WarningCode

TODAY = date(2026, 1, 15)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


def test_state_false_is_pending_with_warning():
    result = effective_document_state(False, TOMORROW, TODAY)

    assert result.state == DocumentState.pending
    assert result.warnings == [WarningCode.null_document_state]


def test_expiry_date_false_preserves_the_stored_state():
    result = effective_document_state(DocumentState.valid, None, TODAY)

    assert result.state == DocumentState.valid
    assert result.warnings == [WarningCode.no_expiry_date]


@pytest.mark.parametrize("expiry", [YESTERDAY, TODAY, TOMORROW, None])
def test_rejected_prevails_over_expiry_date(expiry):
    result = effective_document_state(
        DocumentState.rejected,
        expiry,
        TODAY,
        is_snapshot=False,
    )

    assert result.state == DocumentState.rejected
    assert result.warnings == []


@pytest.mark.parametrize("expiry", [YESTERDAY, TODAY, TOMORROW, None])
def test_missing_prevails_over_expiry_date(expiry):
    result = effective_document_state(
        DocumentState.missing,
        expiry,
        TODAY,
        is_snapshot=True,
    )

    assert result.state == DocumentState.missing
    assert result.warnings == []


def test_expired_with_future_expiry_becomes_pending_with_adjustment():
    result = effective_document_state(DocumentState.expired, TOMORROW, TODAY)

    assert result.state == DocumentState.pending
    assert result.warnings == [WarningCode.effective_state_adjusted]


def test_valid_with_past_expiry_becomes_expired_with_adjustment():
    result = effective_document_state(DocumentState.valid, YESTERDAY, TODAY)

    assert result.state == DocumentState.expired
    assert result.warnings == [WarningCode.effective_state_adjusted]


def test_regular_state_inside_validity_has_no_warning():
    result = effective_document_state(DocumentState.pending, TODAY, TODAY)

    assert result.state == DocumentState.pending
    assert result.warnings == []
