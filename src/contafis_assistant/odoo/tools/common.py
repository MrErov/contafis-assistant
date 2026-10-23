"""Utilidades compartidas por las tools ERP: gateway, normalización y warnings."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from contafis_assistant.domain.contracts import Warning
from contafis_assistant.domain.enums import WarningCode, WarningSource
from contafis_assistant.odoo.allowlist import RpcCall
from contafis_assistant.odoo.client import ToolBudget

CLIENT_READ_FIELDS = ["id", "name", "rif", "company_id"]
COMPANY_READ_FIELDS = ["id", "name", "active"]
OBLIGATION_READ_FIELDS = [
    "id",
    "client_id",
    "obligation_type_id",
    "period",
    "due_date",
    "state",
]
OBLIGATION_TYPE_READ_FIELDS = ["id", "name", "tax_type"]
ALERT_READ_FIELDS = ["id", "client_id", "state"]

WARNING_MESSAGES: dict[WarningCode, str] = {
    WarningCode.company_archived: (
        "La compañía está archivada; no se consultan sus datos."
    ),
    WarningCode.no_active_companies: "No hay compañías activas en el alcance.",
    WarningCode.null_obligation_type: (
        "Hay obligaciones sin tipo; se informan en una cubeta separada."
    ),
    WarningCode.no_due_date: "La obligación no tiene vencimiento calculado.",
    WarningCode.effective_state_adjusted: (
        "El estado efectivo se ajustó según el vencimiento."
    ),
    WarningCode.fuzzy_match: "La entidad se resolvió por coincidencia aproximada.",
    WarningCode.scope_partial: "El alcance de compañías es incompleto.",
    WarningCode.truncated: "El detalle se truncó al límite configurado.",
    WarningCode.count_unavailable: "No se pudo confirmar el total.",
}


class OdooGateway(Protocol):
    """Contrato mínimo que cumplen el cliente real y los dobles de test."""

    def execute(
        self,
        call: RpcCall,
        *,
        budget: ToolBudget | None = None,
        previous_truncated: RpcCall | None = None,
        allowed_company_ids: Sequence[int] | None = None,
    ) -> Any: ...


def normalize_ids(values: Iterable[int] | None) -> list[int]:
    """Normaliza IDs positivos y elimina duplicados, preservando orden."""
    normalized: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed <= 0 or parsed in seen:
            continue
        seen.add(parsed)
        normalized.append(parsed)
    return normalized


def many2one_id(value: Any) -> int | None:
    """Extrae el ID de un many2one leído en formato crudo o [id, nombre]."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple)) and value:
        first = value[0]
        return first if isinstance(first, int) and not isinstance(first, bool) else None
    if isinstance(value, dict):
        identifier = value.get("id")
        return identifier if isinstance(identifier, int) else None
    return None


def make_warning(
    code: WarningCode,
    *,
    message: str | None = None,
    entity_id: int | None = None,
    context: dict[str, str | int | bool] | None = None,
    source: WarningSource = WarningSource.odoo,
) -> Warning:
    return Warning(
        code=code,
        message=message or WARNING_MESSAGES.get(code, code.value),
        source=source,
        entity_id=entity_id,
        context=context or {},
    )


def search_read(
    gateway: OdooGateway,
    model: str,
    domain: list[Any],
    fields: list[str],
    *,
    budget: ToolBudget | None,
    allowed_company_ids: Sequence[int],
    limit: int | None = None,
    active_test: bool | None = None,
) -> list[dict[str, Any]]:
    """Ejecuta un ``search_read`` ya construido por código, nunca por el LLM."""
    call = RpcCall(
        model=model,
        method="search_read",
        domain=domain,
        fields=fields,
        limit=limit,
        active_test=active_test,
    )
    result = gateway.execute(
        call,
        budget=budget,
        allowed_company_ids=list(allowed_company_ids),
    )
    if result is None:
        return []
    if not isinstance(result, list):
        raise TypeError(f"{model}.search_read devolvió un valor no válido")
    return [row for row in result if isinstance(row, dict)]


def company_rows_by_id(rows: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    mapping: dict[int, dict[str, Any]] = {}
    for row in rows:
        company_id = many2one_id(row.get("id"))
        if company_id is not None:
            mapping[company_id] = row
    return mapping


def client_rows_by_id(rows: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    mapping: dict[int, dict[str, Any]] = {}
    for row in rows:
        client_id = many2one_id(row.get("id"))
        if client_id is not None:
            mapping[client_id] = row
    return mapping
