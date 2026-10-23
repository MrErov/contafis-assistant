from __future__ import annotations

import re
from datetime import date

import pytest

from contafis_assistant.domain.answer import (
    AnswerContext,
    compose_answer,
    render_answer,
    validate_grounding,
)
from contafis_assistant.domain.contracts import (
    EntityRef,
    ObligationItem,
    ObligationsResult,
    Source,
)
from contafis_assistant.domain.enums import (
    EntityType,
    Intent,
    ObligationState,
    Status,
    WarningCode,
    WarningSource,
)
from contafis_assistant.domain.contracts import Warning

MONETARY_PATTERN = re.compile(
    r"(?i)(?:\$|€|£|¥|\bUSD\b|\bVES\b|\bEUR\b|\bBs\.?\b|\bbol[ií]var|\bmonto\b)"
)

SOURCE = Source(
    document_id="doc-1",
    document="Ley de IVA",
    page=4,
    section="Artículo 10",
    locator="p.4",
    chunk_id="c1",
    score=1 / 61,
    collection="contafis_normativa_v1",
)

OBLIGATION = ObligationItem(
    id=7,
    client_id=3,
    client_name="Acme",
    company_id=1,
    company_name="Comercial Uno",
    obligation_type_id=2,
    obligation_type_name="IVA mensual",
    tax_type="IVA",
    period="2026-01",
    due_date=date(2026, 1, 5),
    state=ObligationState.pending,
    effective_state=ObligationState.overdue,
)

DATA = ObligationsResult(total=2, returned=1, items=[OBLIGATION], truncated=False)


def _entity(name: str, identifier: int) -> EntityRef:
    return EntityRef(id=identifier, name=name, type=EntityType.company)


def _context(
    status: Status,
    *,
    intent: Intent | None = Intent.erp,
    data=None,
    sources=None,
    entities=None,
    options=None,
    warnings=None,
    truncated: bool = False,
) -> AnswerContext:
    return AnswerContext(
        status=status,
        intent=intent,
        data=list(data or []),
        sources=list(sources or []),
        entities=list(entities or []),
        options=list(options or []),
        warnings=list(warnings or []),
        truncated=truncated,
    )


def _warning(code: WarningCode) -> Warning:
    return Warning(code=code, message=code.value, source=WarningSource.system)


def test_ok_render_uses_only_data_and_sources():
    context = _context(Status.ok, data=[DATA], sources=[SOURCE])

    answer = render_answer(context)

    assert "Acme" in answer
    assert "05/01/2026" in answer
    assert "Ley de IVA" in answer
    assert "Fantasma" not in answer


def test_partial_render_does_not_assert_nonexistence_without_data():
    context = _context(
        Status.partial,
        warnings=[_warning(WarningCode.scope_partial)],
    )

    answer = render_answer(context).lower()

    assert "no encontr" not in answer
    assert "no existe" not in answer
    assert "0" not in answer


def test_insufficient_context_starts_with_canonical_phrase():
    answer = render_answer(_context(Status.insufficient_context, intent=Intent.normativa))

    assert answer.startswith("No tengo información sobre eso")


def test_needs_clarification_asks_a_concrete_question_and_uses_only_options():
    context = _context(
        Status.needs_clarification,
        intent=Intent.erp,
        options=[_entity("Alfa", 1), _entity("Beta", 2)],
    )

    answer = render_answer(context)

    assert "Alfa" in answer
    assert "Beta" in answer
    assert "?" in answer or "¿" in answer
    assert "total" not in answer.lower()
    assert "Artículo" not in answer


def test_not_found_suggests_at_most_five_options():
    options = [_entity(f"Opción {index}", index) for index in range(1, 8)]

    answer = render_answer(_context(Status.not_found, options=options))

    assert "Opción 1" in answer
    assert "Opción 5" in answer
    assert "Opción 6" not in answer


def test_out_of_scope_offers_an_in_mvp_alternative():
    answer = render_answer(_context(Status.out_of_scope, intent=Intent.fuera_de_alcance))

    lowered = answer.lower()
    assert "fuera del alcance" in lowered
    assert "obligaciones" in lowered
    assert "documentos" in lowered


def test_dates_use_dd_mm_yyyy_format():
    answer = render_answer(_context(Status.ok, data=[DATA]))

    assert "05/01/2026" in answer
    assert "2026-01-05" not in answer


@pytest.mark.parametrize("status", list(Status))
def test_answer_never_contains_amounts_or_currency(status):
    options = [_entity("Alfa", 1)]
    context = _context(
        status,
        data=[DATA],
        sources=[SOURCE],
        options=options,
    )

    answer = render_answer(context)

    assert MONETARY_PATTERN.search(answer) is None


def test_invented_total_triggers_deterministic_fallback():
    context = _context(Status.ok, data=[DATA])

    answer = compose_answer(context, composer=lambda _: "El total es 999.")

    assert answer == render_answer(context)
    assert "999" not in answer


def test_invented_id_triggers_deterministic_fallback():
    context = _context(Status.ok, data=[DATA])

    answer = compose_answer(
        context,
        composer=lambda _: "La obligación 8 está vencida.",
    )

    assert answer == render_answer(context)
    assert "obligación 8" not in answer.lower()


def test_invented_date_triggers_deterministic_fallback():
    context = _context(Status.ok, data=[DATA])

    answer = compose_answer(
        context,
        composer=lambda _: "El vencimiento es 01/01/2030.",
    )

    assert answer == render_answer(context)
    assert "01/01/2030" not in answer


def test_invented_citation_triggers_deterministic_fallback():
    context = _context(Status.ok, intent=Intent.normativa, sources=[SOURCE])

    answer = compose_answer(
        context,
        composer=lambda _: "Según el Artículo 99 de la Ley de IVA.",
    )

    assert answer == render_answer(context)
    assert "Artículo 99" not in answer


def test_valid_composer_answer_is_kept():
    context = _context(Status.ok, data=[DATA])

    answer = compose_answer(context, composer=lambda _: "Total 2.")

    assert answer == "Total 2."


def test_valid_citation_from_sources_is_kept():
    context = _context(Status.ok, intent=Intent.normativa, sources=[SOURCE])

    candidate = "Según el Artículo 10 de la Ley de IVA."
    answer = compose_answer(context, composer=lambda _: candidate)

    assert answer == candidate
    assert validate_grounding(answer, context) is True


def test_composer_exception_triggers_deterministic_fallback():
    context = _context(Status.ok, data=[DATA])

    def broken(_context):
        raise RuntimeError("falló el compositor")

    assert compose_answer(context, composer=broken) == render_answer(context)


def test_invalid_composer_answer_with_money_falls_back():
    context = _context(Status.ok, data=[DATA])

    answer = compose_answer(
        context,
        composer=lambda _: "El total es USD 500.",
    )

    assert answer == render_answer(context)
    assert MONETARY_PATTERN.search(answer) is None


@pytest.mark.parametrize(
    "status",
    [
        Status.invalid_request,
        Status.needs_clarification,
        Status.not_found,
        Status.insufficient_context,
        Status.out_of_scope,
        Status.upstream_error,
        Status.iteration_limit,
    ],
)
def test_deterministic_states_never_call_the_composer(status):
    calls: list[AnswerContext] = []

    def composer(context):
        calls.append(context)
        return "texto del compositor"

    compose_answer(_context(status), composer=composer)

    assert calls == []


def test_grounding_rejects_invented_item_name():
    context = _context(Status.ok, data=[DATA])

    assert validate_grounding("El cliente Fantasma está vencido.", context) is False
