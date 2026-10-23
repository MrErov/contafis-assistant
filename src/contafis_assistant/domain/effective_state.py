"""Tabla 9.3-A: estado efectivo de obligaciones, documentos y snapshots."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.enums import (
    DocumentState,
    ObligationState,
    WarningCode,
)


class EffectiveStateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str
    warnings: list[WarningCode] = Field(default_factory=list)


def _obligation_state(value: object) -> ObligationState:
    if not isinstance(value, str):
        raise ValueError(f"estado de obligación inválido: {value!r}")
    try:
        return ObligationState(value)
    except ValueError as exc:
        raise ValueError(f"estado de obligación inválido: {value!r}") from exc


def _document_state(value: object) -> DocumentState:
    if not isinstance(value, str):
        raise ValueError(f"estado documental inválido: {value!r}")
    try:
        return DocumentState(value)
    except ValueError as exc:
        raise ValueError(f"estado documental inválido: {value!r}") from exc


def effective_obligation_state(
    state: str | bool | None,
    due_date: date | None,
    today: date,
) -> EffectiveStateResult:
    """Aplica la precedencia de obligaciones de la Tabla 9.3-A."""
    stored = _obligation_state(state)

    if stored in (ObligationState.paid, ObligationState.cancelled):
        return EffectiveStateResult(state=stored.value)

    if due_date is None:
        return EffectiveStateResult(
            state=stored.value,
            warnings=[WarningCode.no_due_date],
        )

    if stored == ObligationState.overdue and due_date >= today:
        return EffectiveStateResult(
            state=ObligationState.pending.value,
            warnings=[WarningCode.effective_state_adjusted],
        )

    if stored == ObligationState.pending and due_date < today:
        return EffectiveStateResult(
            state=ObligationState.overdue.value,
            warnings=[WarningCode.effective_state_adjusted],
        )

    return EffectiveStateResult(state=stored.value)


def effective_document_state(
    state: str | bool | None,
    expiry_date: date | None,
    today: date,
    *,
    is_snapshot: bool = False,
) -> EffectiveStateResult:
    """Aplica la precedencia documental de la Tabla 9.3-A.

    `missing` solo existe en snapshots, pero la función lo respeta también
    cuando el llamador lo marca como snapshot.
    """
    if state is False or state is None:
        return EffectiveStateResult(
            state=DocumentState.pending.value,
            warnings=[WarningCode.null_document_state],
        )

    stored = _document_state(state)

    if stored == DocumentState.rejected:
        return EffectiveStateResult(state=DocumentState.rejected.value)

    if stored == DocumentState.missing:
        return EffectiveStateResult(state=DocumentState.missing.value)

    if expiry_date is None:
        return EffectiveStateResult(
            state=stored.value,
            warnings=[WarningCode.no_expiry_date],
        )

    if expiry_date < today:
        if stored == DocumentState.expired:
            return EffectiveStateResult(state=DocumentState.expired.value)
        return EffectiveStateResult(
            state=DocumentState.expired.value,
            warnings=[WarningCode.effective_state_adjusted],
        )

    if stored == DocumentState.expired:
        return EffectiveStateResult(
            state=DocumentState.pending.value,
            warnings=[WarningCode.effective_state_adjusted],
        )

    return EffectiveStateResult(state=stored.value)
