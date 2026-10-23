from __future__ import annotations

from contafis_assistant.domain.enums import Intent
from contafis_assistant.graph.planner import PlanContext, plan_next
from contafis_assistant.graph.state import Slots, ToolArgs, ToolName


def _context(**overrides) -> PlanContext:
    base = {
        "intent": Intent.erp,
        "slots": Slots(topic="obligations"),
        "message": "obligaciones",
        "remaining_budget": 3,
    }
    base.update(overrides)
    return PlanContext(**base)


def test_planner_is_source_aware_for_each_intent():
    erp = plan_next(_context(intent=Intent.erp))
    rag = plan_next(_context(intent=Intent.normativa))
    mixed = plan_next(_context(intent=Intent.mixta))
    conversational = plan_next(_context(intent=Intent.conversacional))

    assert erp.action is not None
    assert erp.action.source == "erp"
    assert erp.action.tool is ToolName.query_obligations

    assert rag.action is not None
    assert rag.action.source == "rag"
    assert rag.action.tool is ToolName.search_normativa

    assert mixed.action is not None
    assert mixed.action.source == "erp"

    assert conversational.action is None


def test_planner_does_not_repeat_a_resolved_source():
    next_rag = plan_next(
        _context(intent=Intent.mixta, sources_used=["erp"])
    )
    nothing = plan_next(
        _context(intent=Intent.mixta, sources_used=["erp", "rag"])
    )
    erp_done = plan_next(
        _context(intent=Intent.erp, sources_used=["erp"])
    )

    assert next_rag.action is not None
    assert next_rag.action.source == "rag"
    assert nothing.action is None
    assert erp_done.action is None


def test_planner_prioritizes_erp_before_rag_for_mixed_intent():
    first = plan_next(_context(intent=Intent.mixta))
    second = plan_next(
        _context(intent=Intent.mixta, sources_used=["erp"])
    )

    assert first.action is not None and first.action.source == "erp"
    assert second.action is not None and second.action.source == "rag"


def test_planner_respects_exact_budget():
    enough = plan_next(_context(remaining_budget=1))
    exhausted = plan_next(_context(remaining_budget=0))

    assert enough.action is not None
    assert exhausted.action is None
    assert exhausted.iteration_limit is False


def test_retry_consumes_budget_and_is_blocked_without_budget():
    retry = plan_next(
        _context(
            sources_used=["erp"],
            sources_attempted=["erp"],
            retryable_source="erp",
            remaining_budget=1,
        )
    )
    blocked = plan_next(
        _context(
            sources_used=["erp"],
            sources_attempted=["erp"],
            retryable_source="erp",
            remaining_budget=0,
        )
    )
    not_retryable = plan_next(
        _context(
            sources_attempted=["erp"],
            retryable_source=None,
            remaining_budget=2,
        )
    )

    assert retry.action is not None
    assert retry.action.source == "erp"
    assert retry.action.is_retry is True
    assert retry.action.requires_budget is True

    assert blocked.action is None
    assert blocked.iteration_limit is True

    assert not_retryable.action is None


def test_truncation_requires_new_pagination_arguments():
    previous = ToolArgs(offset=0, limit=50)
    decision = plan_next(
        _context(
            sources_used=["erp"],
            sources_attempted=["erp"],
            truncated_tool=ToolName.query_obligations,
            last_args=previous,
            remaining_budget=1,
        )
    )

    assert decision.action is not None
    assert decision.action.tool is ToolName.query_obligations
    assert decision.action.args.offset == 50
    assert decision.action.args.limit == 50
    assert decision.action.args != previous
    assert decision.action.is_retry is True


def test_erp_tool_follows_classified_topic_and_entity():
    decision = plan_next(
        _context(
            intent=Intent.erp,
            slots=Slots(topic="clients", tax_type="iva", states=["overdue"]),
            entity_id=7,
            entity_type="client",
            entity_company_id=3,
        )
    )

    assert decision.action is not None
    assert decision.action.tool is ToolName.query_clients
    assert decision.action.args.client_id == 7
    assert decision.action.args.company_id == 3
    assert decision.action.args.tax_type == "iva"
    assert decision.action.args.states == ["overdue"]
