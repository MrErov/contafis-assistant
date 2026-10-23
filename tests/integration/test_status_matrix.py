from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from contafis_assistant.domain.contracts import (
    EntityRef,
    ObligationItem,
    ObligationsResult,
    Source,
    Warning,
)
from contafis_assistant.domain.enums import (
    EntityType,
    Intent,
    ObligationState,
    ScopeState,
    Status,
    WarningCode,
    WarningSource,
)
from contafis_assistant.graph.build import GraphDependencies, run_turn
from contafis_assistant.graph.classify import ClassificationDraft, ClassificationResult
from contafis_assistant.graph.executor import ToolOutcome
from contafis_assistant.graph.nodes import finalize_node
from contafis_assistant.graph.planner import PlanDecision, plan_next
from contafis_assistant.graph.response import to_chat_response
from contafis_assistant.graph.state import (
    PlanAction,
    Slots,
    ToolArgs,
    ToolName,
    TurnState,
)
from contafis_assistant.odoo.scope import ScopeReason, ScopeSnapshot
from contafis_assistant.odoo.tools.entities import EntityResolution

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

DATA = ObligationsResult(total=1, returned=1, items=[OBLIGATION], truncated=False)
ZERO_DATA = ObligationsResult(total=0, returned=0, truncated=False)


def _warning(code: WarningCode) -> Warning:
    return Warning(code=code, message=code.value, source=WarningSource.system)


class FakeTool:
    def __init__(self, outcome: ToolOutcome | None = None):
        self.outcome = outcome or ToolOutcome(status=Status.ok, data=[DATA])
        self.calls: list[ToolArgs] = []

    def __call__(self, args: ToolArgs) -> ToolOutcome:
        self.calls.append(args)
        return self.outcome


class FakeClassifier:
    def __init__(
        self,
        intent: Intent,
        mentions: list[str] | None = None,
        *,
        components: list[str] | None = None,
        warnings: list[Warning] | None = None,
        out_of_scope_all: bool | None = None,
    ):
        self.intent = intent
        self.mentions = list(mentions or [])
        self.components = list(components or [])
        self.warnings = list(warnings or [])
        self.out_of_scope_all = (
            intent is Intent.fuera_de_alcance
            if out_of_scope_all is None
            else out_of_scope_all
        )

    def __call__(self, message: str) -> ClassificationResult:
        return ClassificationResult(
            classification=ClassificationDraft(
                intent=self.intent,
                slots=Slots(topic="obligations"),
                entity_mentions=list(self.mentions),
                out_of_scope_components=list(self.components),
            ),
            warnings=list(self.warnings),
            out_of_scope_all=self.out_of_scope_all,
        )


class FakeResolver:
    def __init__(self, resolution: EntityResolution):
        self.resolution = resolution
        self.mentions: list[str] = []

    def __call__(self, mention: str) -> EntityResolution:
        self.mentions.append(mention)
        return self.resolution


def _entity(name: str, identifier: int) -> EntityRef:
    return EntityRef(id=identifier, name=name, type=EntityType.company)


def _tools(erp: FakeTool | None = None, rag: FakeTool | None = None):
    erp = erp or FakeTool()
    rag = rag or FakeTool(ToolOutcome(status=Status.ok, sources=[SOURCE]))
    return {
        ToolName.query_obligations: erp,
        ToolName.query_clients: erp,
        ToolName.query_documents: erp,
        ToolName.query_alerts: erp,
        ToolName.query_companies: erp,
        ToolName.search_normativa: rag,
    }


def _deps(
    classifier,
    *,
    tools=None,
    resolver: FakeResolver | None = None,
    planner=plan_next,
    max_tool_calls: int = 3,
    scope_snapshot: ScopeSnapshot | None = None,
    answer_composer=None,
) -> GraphDependencies:
    return GraphDependencies(
        classifier=classifier,
        tools=tools or _tools(),
        planner=planner,
        entity_resolver=resolver,
        max_tool_calls=max_tool_calls,
        scope_snapshot=scope_snapshot,
        answer_composer=answer_composer,
    )


def _always_plan(_context) -> PlanDecision:
    return PlanDecision(
        action=PlanAction(
            source="erp",
            tool=ToolName.query_obligations,
            args=ToolArgs(limit=50),
        )
    )


def _partial_scope() -> ScopeSnapshot:
    return ScopeSnapshot(
        state=ScopeState.scope_partial,
        reason=ScopeReason.incomplete_coverage,
        user_company_ids=[1],
        active_company_ids=[1, 2],
        attested=True,
        checked_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
    )


def _warning_codes(state: TurnState) -> set[WarningCode]:
    return {warning.code for warning in state.warnings}


def test_invalid_request_is_a_valid_final_state():
    state = finalize_node(
        TurnState(
            request_id=str(uuid.uuid4()),
            thread_id="invalido",
            message="",
            status=Status.invalid_request,
        )
    )

    assert state.status is Status.invalid_request
    assert state.is_final is True
    assert "No pude procesar" in state.answer

    response = to_chat_response(state)
    assert response.status is Status.invalid_request
    assert response.data == []


def test_out_of_scope_total_does_not_invoke_tools():
    tool = FakeTool()
    deps = _deps(FakeClassifier(Intent.fuera_de_alcance), tools=_tools(erp=tool))

    state = run_turn("hacé una factura", deps=deps, thread_id="fuera-total")

    assert state.status is Status.out_of_scope
    assert tool.calls == []
    assert "fuera del alcance" in state.answer.lower()
    assert to_chat_response(state).options == []


def test_out_of_scope_component_with_data_is_partial():
    deps = _deps(
        FakeClassifier(
            Intent.erp,
            components=["montos"],
            warnings=[_warning(WarningCode.out_of_scope_component)],
        )
    )

    state = run_turn("obligaciones y montos", deps=deps, thread_id="fuera-componente")

    assert state.status is Status.partial
    assert WarningCode.out_of_scope_component in _warning_codes(state)
    assert state.data


def test_needs_clarification_returns_only_entity_options():
    resolver = FakeResolver(
        EntityResolution(
            status=Status.needs_clarification,
            options=[_entity("Alfa", 1), _entity("Beta", 2)],
        )
    )
    deps = _deps(
        FakeClassifier(Intent.erp, mentions=["Acme"]),
        resolver=resolver,
    )

    state = run_turn("obligaciones de Acme", deps=deps, thread_id="aclaracion")

    assert state.status is Status.needs_clarification
    assert state.data == []
    assert len(state.options) == 2
    assert "?" in state.answer or "¿" in state.answer
    assert "Alfa" in state.answer and "Beta" in state.answer

    response = to_chat_response(state)
    assert response.options == state.options


def test_not_found_with_complete_scope_keeps_suggestions():
    resolver = FakeResolver(
        EntityResolution(
            status=Status.not_found,
            options=[_entity("Alfa", 1), _entity("Beta", 2)],
            scope_complete=True,
        )
    )
    deps = _deps(
        FakeClassifier(Intent.erp, mentions=["Acme"]),
        resolver=resolver,
    )

    state = run_turn("obligaciones de Acme", deps=deps, thread_id="no-encontrada")

    assert state.status is Status.not_found
    response = to_chat_response(state)
    assert response.status is Status.not_found
    assert len(response.options) == 2
    assert "Alfa" in response.answer


def test_not_found_with_incomplete_scope_becomes_partial_and_does_not_assert_absence():
    resolver = FakeResolver(
        EntityResolution(
            status=Status.not_found,
            options=[_entity("Alfa", 1)],
            scope_complete=False,
        )
    )
    deps = _deps(
        FakeClassifier(Intent.erp, mentions=["Acme"]),
        resolver=resolver,
        scope_snapshot=_partial_scope(),
    )

    state = run_turn("obligaciones de Acme", deps=deps, thread_id="scope-parcial")

    assert state.status is Status.partial
    assert WarningCode.scope_partial in _warning_codes(state)
    lowered = state.answer.lower()
    assert "no encontr" not in lowered
    assert "no existe" not in lowered

    response = to_chat_response(state)
    assert response.status is Status.partial
    assert response.options == []


def test_zero_results_of_existing_entity_is_ok():
    resolver = FakeResolver(
        EntityResolution(status=Status.ok, entity=_entity("Acme", 1))
    )
    deps = _deps(
        FakeClassifier(Intent.erp, mentions=["Acme"]),
        tools=_tools(erp=FakeTool(ToolOutcome(status=Status.ok, data=[ZERO_DATA]))),
        resolver=resolver,
    )

    state = run_turn("obligaciones de Acme", deps=deps, thread_id="cero")

    assert state.status is Status.ok
    assert state.data[0].total == 0
    assert "total 0" in state.answer.lower()


def test_erp_down_with_rag_available_is_partial():
    erp = FakeTool(ToolOutcome(status=Status.upstream_error))
    rag = FakeTool(ToolOutcome(status=Status.ok, sources=[SOURCE]))
    deps = _deps(
        FakeClassifier(Intent.mixta),
        tools=_tools(erp=erp, rag=rag),
    )

    state = run_turn("obligaciones y normativa", deps=deps, thread_id="erp-caido")

    assert state.status is Status.partial
    assert WarningCode.source_unavailable in _warning_codes(state)
    assert state.sources


def test_rag_down_with_erp_available_is_partial():
    erp = FakeTool(ToolOutcome(status=Status.ok, data=[DATA]))
    rag = FakeTool(
        ToolOutcome(
            status=Status.upstream_error,
            warnings=[
                _warning(WarningCode.corpus_unavailable),
                _warning(WarningCode.manifest_mismatch),
            ],
        )
    )
    deps = _deps(
        FakeClassifier(Intent.mixta),
        tools=_tools(erp=erp, rag=rag),
    )

    state = run_turn("obligaciones y normativa", deps=deps, thread_id="rag-caido")

    assert state.status is Status.partial
    assert state.data
    codes = _warning_codes(state)
    assert WarningCode.source_unavailable in codes
    assert WarningCode.manifest_mismatch in codes
    assert WarningCode.corpus_unavailable in codes


def test_both_sources_down_is_upstream_error():
    erp = FakeTool(ToolOutcome(status=Status.upstream_error))
    rag = FakeTool(ToolOutcome(status=Status.upstream_error))
    deps = _deps(
        FakeClassifier(Intent.mixta),
        tools=_tools(erp=erp, rag=rag),
    )

    state = run_turn("obligaciones y normativa", deps=deps, thread_id="ambos-caidos")

    assert state.status is Status.upstream_error
    assert state.data == []
    assert state.sources == []
    assert WarningCode.source_unavailable in _warning_codes(state)
    assert "No pude completar" in state.answer


def test_rag_low_score_without_erp_is_insufficient_context():
    rag = FakeTool(
        ToolOutcome(
            status=Status.insufficient_context,
            warnings=[_warning(WarningCode.retrieval_low_score)],
        )
    )
    deps = _deps(FakeClassifier(Intent.normativa), tools=_tools(rag=rag))

    state = run_turn("¿qué dice la norma?", deps=deps, thread_id="rag-bajo")

    assert state.status is Status.insufficient_context
    assert state.answer.startswith("No tengo información sobre eso")
    assert WarningCode.retrieval_low_score in _warning_codes(state)


def test_rag_low_score_with_erp_is_partial_and_keeps_erp_data():
    erp = FakeTool(ToolOutcome(status=Status.ok, data=[DATA]))
    rag = FakeTool(
        ToolOutcome(
            status=Status.insufficient_context,
            warnings=[_warning(WarningCode.retrieval_low_score)],
        )
    )
    deps = _deps(
        FakeClassifier(Intent.mixta),
        tools=_tools(erp=erp, rag=rag),
    )

    state = run_turn("obligaciones y normativa", deps=deps, thread_id="mixta-baja")

    assert state.status is Status.partial
    assert state.data == [DATA]
    assert WarningCode.retrieval_low_score in _warning_codes(state)


def test_iteration_limit_preserves_partial_data():
    tool = FakeTool(ToolOutcome(status=Status.ok, data=[DATA]))
    deps = _deps(
        FakeClassifier(Intent.erp),
        tools=_tools(erp=tool),
        planner=_always_plan,
        max_tool_calls=3,
    )

    state = run_turn("consulta", deps=deps, thread_id="limite")

    assert state.status is Status.iteration_limit
    assert state.data
    assert all(result.total == 1 for result in state.data)
    assert state.tool_call_count == 3
    assert "límite" in state.answer.lower()


def test_rag_only_corpus_unavailable_is_upstream_error():
    rag = FakeTool(
        ToolOutcome(
            status=Status.upstream_error,
            warnings=[_warning(WarningCode.corpus_unavailable)],
        )
    )
    deps = _deps(FakeClassifier(Intent.normativa), tools=_tools(rag=rag))

    state = run_turn("¿qué dice la norma?", deps=deps, thread_id="corpus-caido")

    assert state.status is Status.upstream_error
    assert WarningCode.corpus_unavailable in _warning_codes(state)


def test_chat_response_is_coherent_for_a_final_state():
    deps = _deps(FakeClassifier(Intent.erp))

    state = run_turn("obligaciones", deps=deps, thread_id="coherente")
    response = to_chat_response(state)

    assert uuid.UUID(response.request_id).version == 4
    assert response.thread_id == "coherente"
    assert response.status is Status.ok
    assert response.tool_call_count == 1
    assert response.tools_used == ["query_obligations"]
    assert response.truncated is False
    assert response.options == []
    for result in response.data:
        assert result.returned == len(result.items)
