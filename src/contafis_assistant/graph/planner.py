"""Planificador source-aware con presupuesto exacto y paginación segura."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.enums import Intent
from contafis_assistant.graph.state import (
    PlanAction,
    Slots,
    SourceKind,
    ToolArgs,
    ToolName,
)

DEFAULT_PAGE_LIMIT = 50

TOPIC_TOOL: dict[str, ToolName] = {
    "obligations": ToolName.query_obligations,
    "clients": ToolName.query_clients,
    "documents": ToolName.query_documents,
    "alerts": ToolName.query_alerts,
    "companies": ToolName.query_companies,
}

RAG_TOOLS = {ToolName.search_normativa}


class PlanContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    slots: Slots = Field(default_factory=Slots)
    message: str = ""
    sources_used: list[SourceKind] = Field(default_factory=list)
    sources_attempted: list[SourceKind] = Field(default_factory=list)
    retryable_source: SourceKind | None = None
    retried_sources: list[SourceKind] = Field(default_factory=list)
    remaining_budget: int = Field(default=3, ge=0)
    truncated_tool: ToolName | None = None
    last_args: ToolArgs | None = None
    entity_id: int | None = None
    entity_type: Literal["company", "client"] | None = None
    entity_company_id: int | None = None


class PlanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: PlanAction | None = None
    iteration_limit: bool = False
    reason: str | None = None


def _no_action() -> PlanDecision:
    return PlanDecision()


def _budget_exhausted() -> PlanDecision:
    return PlanDecision(iteration_limit=True, reason="tool_budget")


def _source_of(tool: ToolName) -> SourceKind:
    return "rag" if tool in RAG_TOOLS else "erp"


def _entity_args(context: PlanContext) -> dict[str, int]:
    if context.entity_type == "client" and context.entity_id is not None:
        args: dict[str, int] = {"client_id": context.entity_id}
        if context.entity_company_id is not None:
            args["company_id"] = context.entity_company_id
        return args
    if context.entity_type == "company" and context.entity_id is not None:
        return {"company_id": context.entity_id}
    return {}


def _erp_action(
    context: PlanContext,
    *,
    is_retry: bool = False,
) -> PlanAction:
    tool = TOPIC_TOOL.get(context.slots.topic or "obligations", ToolName.query_obligations)
    args = ToolArgs(
        tax_type=context.slots.tax_type,
        states=list(context.slots.states),
        document_type_id=context.slots.document_type_id,
        **_entity_args(context),
    )
    return PlanAction(
        source="erp",
        tool=tool,
        args=args,
        requires_budget=True,
        is_retry=is_retry,
    )


def _rag_action(context: PlanContext, *, is_retry: bool = False) -> PlanAction:
    query = context.slots.query or context.message
    return PlanAction(
        source="rag",
        tool=ToolName.search_normativa,
        args=ToolArgs(query=query),
        requires_budget=True,
        is_retry=is_retry,
    )


def _action_for_source(
    context: PlanContext,
    source: SourceKind,
    *,
    is_retry: bool,
) -> PlanAction:
    if source == "rag":
        return _rag_action(context, is_retry=is_retry)
    return _erp_action(context, is_retry=is_retry)


def _truncated_action(context: PlanContext) -> PlanAction:
    tool = context.truncated_tool
    assert tool is not None
    base = context.last_args or ToolArgs()
    offset = (base.offset or 0) + (base.limit or DEFAULT_PAGE_LIMIT)
    limit = base.limit or DEFAULT_PAGE_LIMIT
    args = base.model_copy(update={"offset": offset, "limit": limit})
    return PlanAction(
        source=_source_of(tool),
        tool=tool,
        args=args,
        requires_budget=True,
        is_retry=True,
    )


def plan_next(context: PlanContext) -> PlanDecision:
    if context.intent in {Intent.conversacional, Intent.fuera_de_alcance}:
        return _no_action()

    if context.truncated_tool is not None:
        if context.remaining_budget < 1:
            return _budget_exhausted()
        return PlanDecision(action=_truncated_action(context))

    if (
        context.retryable_source is not None
        and context.retryable_source not in context.retried_sources
    ):
        if context.remaining_budget < 1:
            return _budget_exhausted()
        return PlanDecision(
            action=_action_for_source(
                context,
                context.retryable_source,
                is_retry=True,
            )
        )

    needs_erp = context.intent in {Intent.erp, Intent.mixta}
    needs_rag = context.intent in {Intent.normativa, Intent.mixta}

    if (
        needs_erp
        and "erp" not in context.sources_used
        and "erp" not in context.sources_attempted
    ):
        if context.remaining_budget < 1:
            return _no_action()
        return PlanDecision(action=_erp_action(context))

    if (
        needs_rag
        and "rag" not in context.sources_used
        and "rag" not in context.sources_attempted
    ):
        if context.remaining_budget < 1:
            return _no_action()
        return PlanDecision(action=_rag_action(context))

    return _no_action()


__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "PlanContext",
    "PlanDecision",
    "plan_next",
]
