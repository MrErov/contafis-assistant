from __future__ import annotations

from contafis_assistant.domain.contracts import EntityRef, ObligationsResult, Source
from contafis_assistant.domain.enums import EntityType, Intent, Status
from contafis_assistant.graph.build import GraphDependencies, run_turn
from contafis_assistant.graph.classify import ClassificationDraft, ClassificationResult
from contafis_assistant.graph.executor import ToolOutcome
from contafis_assistant.graph.planner import PlanDecision, plan_next
from contafis_assistant.graph.state import PlanAction, Slots, ToolArgs, ToolName
from contafis_assistant.memory.threads import ThreadStore, ThreadTurn
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

ERP_RESULT = ObligationsResult(total=0, returned=0, truncated=False)


class FakeTool:
    def __init__(self, outcome: ToolOutcome | None = None):
        self.outcome = outcome or ToolOutcome(
            status=Status.ok,
            data=[ERP_RESULT],
        )
        self.calls: list[ToolArgs] = []

    def __call__(self, args: ToolArgs) -> ToolOutcome:
        self.calls.append(args)
        return self.outcome


class FakeClassifier:
    def __init__(self, intent: Intent, mentions: list[str] | None = None):
        self.intent = intent
        self.mentions = list(mentions or [])

    def __call__(self, message: str) -> ClassificationResult:
        return ClassificationResult(
            classification=ClassificationDraft(
                intent=self.intent,
                slots=Slots(topic="obligations"),
                entity_mentions=list(self.mentions),
            )
        )


class MessageClassifier:
    def __init__(self, mapping: dict[str, list[str]]):
        self.mapping = mapping

    def __call__(self, message: str) -> ClassificationResult:
        mentions: list[str] = []
        for token, values in self.mapping.items():
            if token in message:
                mentions.extend(values)
        return ClassificationResult(
            classification=ClassificationDraft(
                intent=Intent.erp,
                slots=Slots(topic="obligations"),
                entity_mentions=mentions,
            )
        )


class FakeResolver:
    def __init__(self, mapping: dict[str, EntityRef]):
        self.mapping = mapping
        self.mentions: list[str] = []

    def __call__(self, mention: str) -> EntityResolution:
        self.mentions.append(mention)
        return EntityResolution(status=Status.ok, entity=self.mapping[mention])


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
    store: ThreadStore | None = None,
    max_tool_calls: int = 3,
) -> GraphDependencies:
    return GraphDependencies(
        classifier=classifier,
        tools=tools or _tools(),
        planner=planner,
        entity_resolver=resolver,
        store=store,
        max_tool_calls=max_tool_calls,
    )


def _always_plan(_context) -> PlanDecision:
    return PlanDecision(
        action=PlanAction(
            source="erp",
            tool=ToolName.query_obligations,
            args=ToolArgs(limit=50),
        )
    )


def test_fourth_tool_is_never_executed_and_sets_iteration_limit():
    tool = FakeTool()
    deps = _deps(
        FakeClassifier(Intent.erp),
        tools=_tools(erp=tool),
        planner=_always_plan,
        max_tool_calls=3,
    )

    state = run_turn("consulta", deps=deps, thread_id="cuarta")

    assert len(tool.calls) == 3
    assert state.tool_call_count == 3
    assert state.iteration_limit_reason == "tool_budget"
    assert state.status is Status.iteration_limit
    assert state.is_final is True


def test_retry_without_budget_sets_iteration_limit_before_second_call():
    tool = FakeTool(
        ToolOutcome(
            status=Status.upstream_error,
            retryable=True,
        )
    )
    deps = _deps(
        FakeClassifier(Intent.erp),
        tools=_tools(erp=tool),
        max_tool_calls=1,
    )

    state = run_turn("consulta", deps=deps, thread_id="reintento")

    assert len(tool.calls) == 1
    assert state.tool_call_count == 1
    assert state.retryable_source == "erp"
    assert state.iteration_limit_reason == "tool_budget"
    assert state.status is Status.iteration_limit


def test_exactly_three_tools_with_resolved_sources_is_not_iteration_limit():
    erp = FakeTool()
    rag = FakeTool(ToolOutcome(status=Status.ok, sources=[SOURCE]))
    resolver = FakeResolver({"Acme": _entity("Acme", 1)})
    deps = _deps(
        FakeClassifier(Intent.mixta, mentions=["Acme"]),
        tools=_tools(erp=erp, rag=rag),
        resolver=resolver,
    )

    state = run_turn("consulta", deps=deps, thread_id="tres")

    assert resolver.mentions == ["Acme"]
    assert len(erp.calls) == 1
    assert len(rag.calls) == 1
    assert state.tool_call_count == 3
    assert state.status in {Status.ok, Status.partial}
    assert state.status is not Status.iteration_limit
    assert state.iteration_limit_reason is None
    assert set(state.sources_used) == {"erp", "rag"}
    assert set(state.sources_attempted) == {"erp", "rag"}
    assert state.tools_used == [
        "resolve_entity",
        "query_obligations",
        "search_normativa",
    ]


def test_entity_from_history_does_not_spend_budget():
    store = ThreadStore()
    store.resolve("historial")
    store.append_turn("historial", ThreadTurn(entity=_entity("Acme", 1)))
    tool = FakeTool()
    resolver = FakeResolver({"Acme": _entity("Acme", 1)})
    deps = _deps(
        FakeClassifier(Intent.erp),
        tools=_tools(erp=tool),
        resolver=resolver,
        store=store,
    )

    state = run_turn("obligaciones", deps=deps, thread_id="historial")

    assert resolver.mentions == []
    assert len(tool.calls) == 1
    assert state.tool_call_count == 1
    assert state.tools_used == ["query_obligations"]
    assert state.entities == [_entity("Acme", 1)]


def test_new_entity_mention_consumes_budget_when_it_queries_odoo():
    store = ThreadStore()
    store.resolve("historial-odoo")
    store.append_turn("historial-odoo", ThreadTurn(entity=_entity("Acme", 1)))
    tool = FakeTool()
    resolver = FakeResolver({"Beta": _entity("Beta", 2)})
    deps = _deps(
        FakeClassifier(Intent.erp, mentions=["Beta"]),
        tools=_tools(erp=tool),
        resolver=resolver,
        store=store,
    )

    state = run_turn("obligaciones de Beta", deps=deps, thread_id="historial-odoo")

    assert resolver.mentions == ["Beta"]
    assert len(tool.calls) == 1
    assert state.tool_call_count == 2
    assert state.tools_used == ["resolve_entity", "query_obligations"]


def test_recursion_limit_is_iteration_limit_with_partial_state():
    tool = FakeTool()
    deps = _deps(
        FakeClassifier(Intent.erp),
        tools=_tools(erp=tool),
        planner=_always_plan,
        max_tool_calls=3,
    )

    state = run_turn(
        "consulta",
        deps=deps,
        thread_id="recursion",
        recursion_limit=8,
    )

    assert state.status is Status.iteration_limit
    assert state.iteration_limit_reason == "recursion_limit"
    assert state.is_final is True
    assert state.data
    assert 1 <= state.tool_call_count <= 3


def test_conversational_and_out_of_scope_do_not_invoke_tools():
    tool = FakeTool()
    conversational = _deps(
        FakeClassifier(Intent.conversacional),
        tools=_tools(erp=tool),
    )
    out_of_scope = _deps(
        FakeClassifier(Intent.fuera_de_alcance),
        tools=_tools(erp=tool),
    )

    conversational_state = run_turn(
        "hola", deps=conversational, thread_id="charla"
    )
    out_of_scope_state = run_turn(
        "crea una factura", deps=out_of_scope, thread_id="alcance"
    )

    assert tool.calls == []
    assert conversational_state.tool_call_count == 0
    assert conversational_state.tools_used == []
    assert out_of_scope_state.tool_call_count == 0
    assert out_of_scope_state.status is Status.out_of_scope


def test_follow_up_queries_again_and_does_not_reuse_previous_facts():
    store = ThreadStore()
    tool = FakeTool()
    classifier = MessageClassifier({"Acme": ["Acme"]})
    resolver = FakeResolver({"Acme": _entity("Acme", 1)})
    deps = _deps(
        classifier,
        tools=_tools(erp=tool),
        resolver=resolver,
        store=store,
    )

    first = run_turn("obligaciones de Acme", deps=deps, thread_id="seguimiento")
    second = run_turn(
        "¿y cuáles están vencidas?", deps=deps, thread_id="seguimiento"
    )

    assert len(tool.calls) == 2
    assert first.tool_call_count == 2
    assert second.tool_call_count == 1
    assert second.history
    assert second.history[-1].entity == _entity("Acme", 1)
    assert second.data
    persisted = store.resolve("seguimiento").history[-1].model_dump(mode="json")
    assert "data" not in persisted
    assert "sources" not in persisted


def test_new_entity_replaces_previous_history_entity():
    store = ThreadStore()
    tool = FakeTool()
    classifier = MessageClassifier({"Acme": ["Acme"], "Beta": ["Beta"]})
    resolver = FakeResolver(
        {"Acme": _entity("Acme", 1), "Beta": _entity("Beta", 2)}
    )
    deps = _deps(
        classifier,
        tools=_tools(erp=tool),
        resolver=resolver,
        store=store,
    )

    run_turn("obligaciones de Acme", deps=deps, thread_id="reemplazo")
    second = run_turn(
        "ahora las de Beta", deps=deps, thread_id="reemplazo"
    )

    assert resolver.mentions == ["Acme", "Beta"]
    assert second.entities == [_entity("Beta", 2)]
    assert store.resolve("reemplazo").last_entity == _entity("Beta", 2)
    assert len(tool.calls) == 2
