"""Reducer de precedencia de estados de la sección 8.3 (T01)."""

from __future__ import annotations

from contafis_assistant.domain.enums import Status, WarningCode
from contafis_assistant.domain.status import StatusSignals, reduce_status


def test_invalid_request_has_top_precedence():
    decision = reduce_status(
        StatusSignals(
            request_valid=False,
            out_of_scope_all=True,
            needs_clarification=True,
            entity_not_found=True,
            iteration_limit=True,
            upstream_error=True,
        )
    )

    assert decision.status == Status.invalid_request


def test_fully_out_of_scope_is_before_clarification():
    decision = reduce_status(
        StatusSignals(out_of_scope_all=True, needs_clarification=True)
    )

    assert decision.status == Status.out_of_scope


def test_needs_clarification_is_before_not_found_and_partial():
    decision = reduce_status(
        StatusSignals(
            needs_clarification=True,
            entity_not_found=True,
            out_of_scope_component=True,
        )
    )

    assert decision.status == Status.needs_clarification


def test_not_found_requires_complete_scope():
    decision = reduce_status(StatusSignals(entity_not_found=True, scope_complete=True))

    assert decision.status == Status.not_found


def test_not_found_with_incomplete_scope_becomes_partial():
    decision = reduce_status(StatusSignals(entity_not_found=True, scope_complete=False))

    assert decision.status == Status.partial
    assert WarningCode.scope_partial in decision.warnings


def test_iteration_limit_prevails_over_source_failure():
    decision = reduce_status(
        StatusSignals(
            iteration_limit=True,
            upstream_error=True,
            source_failed=True,
            has_usable_data=False,
        )
    )

    assert decision.status == Status.iteration_limit


def test_iteration_limit_prevails_over_not_found_downgraded_to_partial():
    decision = reduce_status(
        StatusSignals(
            iteration_limit=True,
            entity_not_found=True,
            scope_complete=False,
        )
    )

    assert decision.status == Status.iteration_limit


def test_upstream_error_without_usable_data():
    decision = reduce_status(
        StatusSignals(upstream_error=True, source_failed=True, has_usable_data=False)
    )

    assert decision.status == Status.upstream_error


def test_source_failure_with_usable_data_is_partial():
    decision = reduce_status(
        StatusSignals(source_failed=True, has_usable_data=True, has_erp_data=True)
    )

    assert decision.status == Status.partial
    assert WarningCode.source_unavailable in decision.warnings


def test_rag_low_score_with_erp_is_partial_and_keeps_erp():
    decision = reduce_status(
        StatusSignals(
            rag_low_score=True,
            has_erp_data=True,
            has_usable_data=True,
        )
    )

    assert decision.status == Status.partial
    assert WarningCode.retrieval_low_score in decision.warnings


def test_rag_low_score_without_erp_is_insufficient_context():
    decision = reduce_status(
        StatusSignals(rag_low_score=True, has_erp_data=False, has_usable_data=False)
    )

    assert decision.status == Status.insufficient_context
    assert WarningCode.retrieval_low_score in decision.warnings


def test_partial_scope_without_data_is_partial():
    decision = reduce_status(StatusSignals(scope_complete=False, has_usable_data=False))

    assert decision.status == Status.partial
    assert WarningCode.scope_partial in decision.warnings


def test_out_of_scope_component_with_usable_data_is_partial():
    decision = reduce_status(
        StatusSignals(out_of_scope_component=True, has_usable_data=True)
    )

    assert decision.status == Status.partial
    assert WarningCode.out_of_scope_component in decision.warnings


def test_insufficient_context_is_after_partial():
    decision = reduce_status(
        StatusSignals(
            insufficient_context=True,
            scope_complete=False,
            has_usable_data=False,
            has_erp_data=False,
        )
    )

    assert decision.status == Status.partial
    assert WarningCode.scope_partial in decision.warnings


def test_insufficient_context_without_other_signals():
    decision = reduce_status(StatusSignals(insufficient_context=True))

    assert decision.status == Status.insufficient_context


def test_zero_results_of_existing_entity_is_ok():
    decision = reduce_status(
        StatusSignals(
            has_usable_data=True,
            has_erp_data=True,
            zero_results_existing_entity=True,
        )
    )

    assert decision.status == Status.ok


def test_clean_response_is_ok():
    decision = reduce_status(StatusSignals(has_usable_data=True, has_erp_data=True))

    assert decision.status == Status.ok
    assert decision.warnings == []


def test_manifest_mismatch_is_reported_with_partial_source_failure():
    decision = reduce_status(
        StatusSignals(
            source_failed=True,
            manifest_mismatch=True,
            has_usable_data=True,
        )
    )

    assert decision.status == Status.partial
    assert WarningCode.source_unavailable in decision.warnings
    assert WarningCode.manifest_mismatch in decision.warnings
