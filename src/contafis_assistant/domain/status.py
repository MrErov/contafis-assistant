"""Reducer puro de precedencia de estados (§8.3)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.enums import Status, WarningCode


class StatusSignals(BaseModel):
    """Señales tipadas que alimentan la precedencia de §8.3."""

    model_config = ConfigDict(extra="forbid")

    request_valid: bool = True
    out_of_scope_all: bool = False
    out_of_scope_component: bool = False
    needs_clarification: bool = False
    entity_not_found: bool = False
    scope_complete: bool = True
    iteration_limit: bool = False
    upstream_error: bool = False
    source_failed: bool = False
    erp_attempted: bool = False
    rag_attempted: bool = False
    manifest_mismatch: bool = False
    corpus_unavailable: bool = False
    rag_low_score: bool = False
    has_erp_data: bool = False
    has_usable_data: bool = False
    insufficient_context: bool = False
    zero_results_existing_entity: bool = False


class StatusDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status
    warnings: list[WarningCode] = Field(default_factory=list)


def _decision(status: Status, warnings: list[WarningCode] | None = None) -> StatusDecision:
    return StatusDecision(status=status, warnings=warnings or [])


def _append_unique(warnings: list[WarningCode], code: WarningCode) -> None:
    if code not in warnings:
        warnings.append(code)


def reduce_status(signals: StatusSignals) -> StatusDecision:
    """Aplica la precedencia completa de la matriz de estados.

    `invalid_request` > `out_of_scope` completo > `needs_clarification` >
    `not_found` (solo alcance completo) > `iteration_limit` > `upstream_error`
    > `partial` > `insufficient_context` > `ok`.
    """
    if not signals.request_valid:
        return _decision(Status.invalid_request)

    if signals.out_of_scope_all:
        return _decision(Status.out_of_scope)

    if signals.needs_clarification:
        return _decision(Status.needs_clarification)

    if signals.entity_not_found and signals.scope_complete:
        return _decision(Status.not_found)

    if signals.iteration_limit:
        return _decision(Status.iteration_limit)

    if signals.upstream_error and not signals.has_usable_data:
        warnings: list[WarningCode] = []
        if signals.erp_attempted and signals.rag_attempted:
            _append_unique(warnings, WarningCode.source_unavailable)
        if signals.corpus_unavailable:
            _append_unique(warnings, WarningCode.corpus_unavailable)
        if signals.manifest_mismatch:
            _append_unique(warnings, WarningCode.manifest_mismatch)
        return _decision(Status.upstream_error, warnings)

    partial_warnings: list[WarningCode] = []
    if not signals.scope_complete:
        _append_unique(partial_warnings, WarningCode.scope_partial)
    if signals.entity_not_found and not signals.scope_complete:
        _append_unique(partial_warnings, WarningCode.scope_partial)
    if signals.out_of_scope_component:
        _append_unique(partial_warnings, WarningCode.out_of_scope_component)
    if signals.source_failed and (signals.has_usable_data or signals.has_erp_data):
        _append_unique(partial_warnings, WarningCode.source_unavailable)
        if signals.manifest_mismatch:
            _append_unique(partial_warnings, WarningCode.manifest_mismatch)
    if signals.rag_low_score and (signals.has_erp_data or partial_warnings):
        _append_unique(partial_warnings, WarningCode.retrieval_low_score)

    if partial_warnings:
        return _decision(Status.partial, partial_warnings)

    if signals.rag_low_score:
        return _decision(
            Status.insufficient_context,
            [WarningCode.retrieval_low_score],
        )

    if signals.insufficient_context:
        return _decision(Status.insufficient_context)

    return _decision(Status.ok)
