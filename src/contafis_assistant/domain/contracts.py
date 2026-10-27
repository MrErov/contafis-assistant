"""Contratos Pydantic de la frontera HTTP y de las tools."""

from __future__ import annotations

import uuid
from datetime import date as Date
from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from contafis_assistant.domain.enums import (
    AlertState,
    ComplianceStatus,
    DocumentSource,
    DocumentState,
    EntityType,
    ObligationState,
    Status,
    WarningCode,
    WarningSource,
)

THREAD_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


# ---------------------------------------------------------------------------
# Frontera de entrada
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Request permisivo: la validación real es manual y pura."""

    model_config = ConfigDict(extra="allow", strict=False)

    message: Any = None
    thread_id: Any = None


# ---------------------------------------------------------------------------
# Estructuras compartidas
# ---------------------------------------------------------------------------


class Warning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: WarningCode
    message: str
    source: WarningSource | None = None
    entity_id: int | None = None
    context: dict[str, str | int | bool] = Field(default_factory=dict)


class EntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    type: EntityType
    company_id: int | None = None
    company_name: str | None = None
    rif: str | None = None
    company_archived: bool = False

    @model_validator(mode="after")
    def _client_requires_company(self) -> "EntityRef":
        if self.type == EntityType.client:
            if self.company_id is None:
                raise ValueError("company_id es obligatorio para un cliente")
            if not self.company_name:
                raise ValueError("company_name es obligatorio para un cliente")
        return self


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    document: str
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    locator: str | None = None
    chunk_id: str
    score: float = Field(ge=0)
    collection: str


class CompanyBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: int
    company_name: str
    total: int = Field(ge=0)
    counts: dict[str, int] = Field(default_factory=dict)


class CompanyRef(BaseModel):
    """Compañía visible para el usuario (endpoint /scope)."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1)
    name: str = Field(min_length=1)


class ScopeInfo(BaseModel):
    """Respuesta de GET /scope con alcance del usuario autenticado."""

    model_config = ConfigDict(extra="forbid")

    uid: int
    companies: list[CompanyRef]
    scope_state: str
    scope_complete: bool
    tools_enabled: bool


# ---------------------------------------------------------------------------
# Ítems de tools
# ---------------------------------------------------------------------------


class ObligationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    client_id: int
    client_name: str
    company_id: int
    company_name: str
    obligation_type_id: int | None = None
    obligation_type_name: str | None = None
    tax_type: str | None = None
    period: str | None = None
    due_date: Date | None = None
    state: ObligationState
    effective_state: ObligationState
    warnings: list[Warning] = Field(default_factory=list)


class ClientItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    rif: str | None = None
    company_id: int
    company_name: str
    company_archived: bool = False
    compliance_status: ComplianceStatus
    obligations_total: int = Field(ge=0)
    obligations_overdue: int = Field(ge=0)
    alerts_pending: int = Field(ge=0)
    warnings: list[Warning] = Field(default_factory=list)


class DocumentGapItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    document_id: int | None = None
    client_id: int
    client_name: str
    company_id: int
    company_name: str
    document_type_id: int
    document_type_code: str
    document_type_name: str
    state: DocumentState
    stored_state: DocumentState | None = None
    expiry_date: Date | None = None
    source: DocumentSource
    as_of: Date
    warnings: list[Warning] = Field(default_factory=list)


class AlertItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    alert_type: str
    state: AlertState
    date: datetime | None = None
    client_id: int
    client_name: str
    company_id: int
    company_name: str
    obligation_id: int | None = None
    document_id: int | None = None
    due_date: Date | None = None
    channel: str | None = None
    message_clean: str | None = None
    warnings: list[Warning] = Field(default_factory=list)


class CompanyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    company_archived: bool = False
    clients_total: int = Field(ge=0)
    obligations_total: int = Field(ge=0)
    obligations_overdue: int = Field(ge=0)
    alerts_pending: int = Field(ge=0)


# ---------------------------------------------------------------------------
# ToolResult: unión discriminada por `kind`
# ---------------------------------------------------------------------------


class ToolResultBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int | None = None
    returned: int = Field(ge=0)
    items: list[Any] = Field(default_factory=list)
    breakdown: list[CompanyBreakdown] = Field(default_factory=list)
    truncated: bool
    source: Literal["odoo"] = "odoo"
    warnings: list[Warning] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_common_contract(self) -> "ToolResultBase":
        if self.returned != len(self.items):
            raise ValueError("returned debe ser igual a len(items)")
        if self.total is not None and self.total < self.returned:
            raise ValueError("total debe ser mayor o igual que returned")
        if self.total is None:
            if not self.truncated:
                raise ValueError("total=None exige truncated=true")
            codes = {warning.code for warning in self.warnings}
            if WarningCode.count_unavailable not in codes:
                raise ValueError("total=None exige warning count_unavailable")
        return self


class ObligationsResult(ToolResultBase):
    kind: Literal["obligations"] = "obligations"
    items: list[ObligationItem] = Field(default_factory=list)


class ClientsResult(ToolResultBase):
    kind: Literal["clients"] = "clients"
    items: list[ClientItem] = Field(default_factory=list)


class DocumentsResult(ToolResultBase):
    kind: Literal["documents"] = "documents"
    items: list[DocumentGapItem] = Field(default_factory=list)
    pending_count: int | None = None

    @model_validator(mode="after")
    def _validate_pending_count(self) -> "DocumentsResult":
        if self.pending_count is not None:
            if self.pending_count < 0:
                raise ValueError("pending_count no puede ser negativo")
            if self.total is not None and self.pending_count > self.total:
                raise ValueError("pending_count no puede superar total")
        return self


class AlertsResult(ToolResultBase):
    kind: Literal["alerts"] = "alerts"
    items: list[AlertItem] = Field(default_factory=list)


class CompaniesResult(ToolResultBase):
    kind: Literal["companies"] = "companies"
    items: list[CompanyItem] = Field(default_factory=list)


ToolResult = Annotated[
    Union[
        ObligationsResult,
        ClientsResult,
        DocumentsResult,
        AlertsResult,
        CompaniesResult,
    ],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Frontera de salida
# ---------------------------------------------------------------------------


def ensure_status_scope(status: Status, scope_complete: bool) -> Status:
    """`not_found` solo es coherente con alcance completo."""
    if status == Status.not_found and not scope_complete:
        raise ValueError("not_found requiere scope_complete=true")
    return status


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    thread_id: str = Field(pattern=THREAD_ID_PATTERN)
    status: Status
    answer: str
    data: list[ToolResult] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    tool_call_count: int = Field(ge=0)
    entities: list[EntityRef] = Field(default_factory=list)
    options: list[EntityRef] = Field(default_factory=list)
    truncated: bool
    warnings: list[Warning] = Field(default_factory=list)

    @field_validator("request_id")
    @classmethod
    def _request_id_is_uuid4(cls, value: str) -> str:
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError) as exc:
            raise ValueError("request_id debe ser un UUID4") from exc
        if parsed.version != 4:
            raise ValueError("request_id debe ser un UUID4")
        return str(parsed)

    @model_validator(mode="after")
    def _validate_options(self) -> "ChatResponse":
        if self.options and self.status not in (
            Status.needs_clarification,
            Status.not_found,
        ):
            raise ValueError(
                "options solo se permite en needs_clarification o not_found"
            )
        return self

    def validate_scope(self, scope_complete: bool) -> None:
        ensure_status_scope(self.status, scope_complete)
