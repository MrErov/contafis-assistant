"""Estado tipado del grafo acotado y modelos de acción."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.contracts import (
    EntityRef,
    Source,
    ToolResult,
    Warning,
)
from contafis_assistant.domain.enums import CorpusStatus, Intent, Status
from contafis_assistant.memory.threads import ThreadTurn
from contafis_assistant.odoo.scope import ScopeSnapshot

SourceKind = Literal["erp", "rag"]


class ToolName(str, Enum):
    resolve_entity = "resolve_entity"
    query_obligations = "query_obligations"
    query_clients = "query_clients"
    query_documents = "query_documents"
    query_alerts = "query_alerts"
    query_companies = "query_companies"
    search_normativa = "search_normativa"


class Slots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: Literal[
        "obligations", "clients", "documents", "alerts", "companies"
    ] | None = None
    tax_type: str | None = None
    states: list[Literal["pending", "overdue", "paid", "cancelled"]] = Field(
        default_factory=list
    )
    period_start: date | None = None
    period_end: date | None = None
    document_type_id: int | None = Field(default=None, ge=1)
    query: str | None = None


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: int | None = Field(default=None, ge=1)
    company_id: int | None = Field(default=None, ge=1)
    tax_type: str | None = None
    states: list[Literal["pending", "overdue", "paid", "cancelled"]] = Field(
        default_factory=list
    )
    offset: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=1)
    query: str | None = None
    document_type_id: int | None = Field(default=None, ge=1)


class PlanAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SourceKind
    tool: ToolName
    args: ToolArgs = Field(default_factory=ToolArgs)
    requires_budget: bool = True
    is_retry: bool = False


class TurnState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    thread_id: str
    message: str
    history: list[ThreadTurn] = Field(default_factory=list)

    intent: Intent | None = None
    slots: Slots = Field(default_factory=Slots)
    entity_mentions: list[str] = Field(default_factory=list)
    out_of_scope_components: list[str] = Field(default_factory=list)

    scope_snapshot: ScopeSnapshot | None = None
    corpus_status: CorpusStatus = CorpusStatus.ok
    thread_warnings: list[Warning] = Field(default_factory=list)

    entities: list[EntityRef] = Field(default_factory=list)
    options: list[EntityRef] = Field(default_factory=list)
    data: list[ToolResult] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    tool_call_count: int = Field(default=0, ge=0)
    sources_used: list[SourceKind] = Field(default_factory=list)
    sources_attempted: list[SourceKind] = Field(default_factory=list)
    retryable_source: SourceKind | None = None
    retried_sources: list[SourceKind] = Field(default_factory=list)

    status: Status | None = None
    answer: str = ""
    truncated: bool = False
    warnings: list[Warning] = Field(default_factory=list)
    latency_ms: int | None = Field(default=None, ge=0)

    remaining_budget: int = Field(default=3, ge=0)
    iteration_limit_reason: str | None = None
    tool_errors: list[str] = Field(default_factory=list)
    is_final: bool = False

    classified: bool = False
    next_action: PlanAction | None = None
    last_tool: ToolName | None = None
    last_args: ToolArgs | None = None
    last_truncated: bool = False
