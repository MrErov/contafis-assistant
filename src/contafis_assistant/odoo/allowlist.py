"""Allowlist cerrada de Odoo 19 (Tabla 9.3-B) y validador recursivo.

La validación es pura y ocurre antes de construir la URL o ejecutar HTTP.
Las tools no pueden aportar modelos, métodos, campos, operadores ni XML IDs
libres: todo se contrasta contra esta tabla.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.odoo.errors import InvalidToolArgs

ALLOWED_GROUP_EXT_IDS = frozenset(
    {"base.group_system", "base.group_erp_manager", "base.group_user"}
)

LOGICAL_OPERATORS = frozenset({"&", "|", "!"})
LEAF_OPERATORS = frozenset(
    {"=", "!=", "in", "not in", ">", ">=", "<", "<=", "ilike", "=ilike"}
)

MAX_IDS = 200
MAX_IN_VALUES = 200


@dataclass(frozen=True)
class ModelPolicy:
    """Métodos, campos de lectura y campos de dominio admitidos para un modelo."""

    methods: frozenset[str]
    read_fields: frozenset[str]
    domain_fields: frozenset[str]


MODEL_POLICIES: dict[str, ModelPolicy] = {
    "res.company": ModelPolicy(
        methods=frozenset({"search_read", "read", "search_count"}),
        read_fields=frozenset({"id", "name", "active"}),
        domain_fields=frozenset({"id", "name", "active"}),
    ),
    "l10n.ve.compliance.client": ModelPolicy(
        methods=frozenset({"search_read", "read", "search_count"}),
        read_fields=frozenset(
            {"id", "name", "rif", "company_id", "accountant_id", "partner_id"}
        ),
        domain_fields=frozenset(
            {"id", "name", "rif", "company_id", "accountant_id"}
        ),
    ),
    "l10n.ve.obligation": ModelPolicy(
        methods=frozenset({"search_read", "read", "search_count"}),
        read_fields=frozenset(
            {
                "id",
                "name",
                "client_id",
                "obligation_type_id",
                "period",
                "due_date",
                "state",
            }
        ),
        domain_fields=frozenset(
            {
                "id",
                "client_id",
                "client_id.company_id",
                "obligation_type_id",
                "obligation_type_id.tax_type",
                "period",
                "due_date",
                "state",
            }
        ),
    ),
    "l10n.ve.document": ModelPolicy(
        methods=frozenset({"search_read", "read", "search_count"}),
        read_fields=frozenset(
            {"id", "client_id", "document_type_id", "state", "expiry_date"}
        ),
        domain_fields=frozenset(
            {
                "id",
                "client_id",
                "client_id.company_id",
                "document_type_id",
                "state",
                "expiry_date",
            }
        ),
    ),
    "l10n.ve.cartelera.status": ModelPolicy(
        methods=frozenset({"search_read", "read", "search_count"}),
        read_fields=frozenset(
            {
                "id",
                "client_id",
                "year",
                "month",
                "document_type_id",
                "state",
                "expiry_date",
                "document_id",
                "date_generated",
            }
        ),
        domain_fields=frozenset(
            {
                "id",
                "client_id",
                "client_id.company_id",
                "year",
                "month",
                "document_type_id",
                "state",
            }
        ),
    ),
    "l10n.ve.alert": ModelPolicy(
        methods=frozenset({"search_read", "read", "search_count"}),
        read_fields=frozenset(
            {
                "id",
                "client_id",
                "obligation_id",
                "document_id",
                "alert_type",
                "date",
                "state",
                "channel",
                "message",
                "sent_date",
            }
        ),
        domain_fields=frozenset(
            {
                "id",
                "client_id",
                "client_id.company_id",
                "obligation_id",
                "document_id",
                "alert_type",
                "date",
                "state",
                "channel",
            }
        ),
    ),
    "l10n.ve.document.type": ModelPolicy(
        methods=frozenset({"search_read", "read", "search_count"}),
        read_fields=frozenset({"id", "name", "code", "required_for"}),
        domain_fields=frozenset({"id", "name", "code", "required_for"}),
    ),
    "l10n.ve.obligation.type": ModelPolicy(
        methods=frozenset({"search_read", "read", "search_count"}),
        read_fields=frozenset({"id", "name", "tax_type", "periodicity"}),
        domain_fields=frozenset({"id", "name", "tax_type", "periodicity"}),
    ),
    "res.users": ModelPolicy(
        methods=frozenset({"context_get", "read", "has_group"}),
        read_fields=frozenset({"id", "company_ids", "group_ids"}),
        domain_fields=frozenset({"id"}),
    ),
    "res.groups": ModelPolicy(
        methods=frozenset({"read"}),
        read_fields=frozenset({"id", "name"}),
        domain_fields=frozenset(),
    ),
    "ir.rule": ModelPolicy(
        methods=frozenset({"search_read"}),
        read_fields=frozenset(
            {
                "id",
                "name",
                "model_id",
                "groups",
                "domain_force",
                "perm_read",
                "perm_write",
                "perm_create",
                "perm_unlink",
            }
        ),
        domain_fields=frozenset({"model_id", "groups"}),
    ),
}

# Filtro explícito de compañía que exige cada modelo de negocio (RF-061).
COMPANY_FILTER_FIELDS: dict[str, str] = {
    "l10n.ve.compliance.client": "company_id",
    "l10n.ve.obligation": "client_id.company_id",
    "l10n.ve.document": "client_id.company_id",
    "l10n.ve.cartelera.status": "client_id.company_id",
    "l10n.ve.alert": "client_id.company_id",
}


class RpcCall(BaseModel):
    """RPC tipado construido por código; nunca un diccionario libre."""

    model_config = ConfigDict(extra="forbid")

    model: str
    method: str
    ids: list[int] | None = None
    domain: list[Any] | None = None
    fields: list[str] | None = None
    offset: int | None = None
    limit: int | None = None
    group_ext_id: str | None = None
    active_test: bool | None = Field(default=None)


def validate_rpc(
    call: RpcCall,
    *,
    uid: int | None = None,
    allowed_company_ids: Sequence[int] | None = None,
) -> RpcCall:
    """Valida modelo, método, campos, dominio y argumentos antes del HTTP."""

    if not isinstance(call, RpcCall):
        raise InvalidToolArgs("se esperaba un RpcCall tipado")

    policy = MODEL_POLICIES.get(call.model)
    if policy is None:
        raise InvalidToolArgs(f"modelo no permitido: {call.model}")

    if call.method.startswith("action_"):
        raise InvalidToolArgs(f"método de acción no permitido: {call.method}")
    if call.method not in policy.methods:
        raise InvalidToolArgs(
            f"método no permitido para {call.model}: {call.method}"
        )

    _validate_common_arguments(call)

    if call.method == "search_read":
        _require_no_ids(call)
        _require_fields(call, policy)
        _require_no_group_ext_id(call)
        _validate_domain(call.domain or [], policy, call.model, uid)
        _require_company_filter(call, allowed_company_ids)
    elif call.method == "search_count":
        _require_no_ids(call)
        _require_no_fields(call)
        _require_no_pagination(call)
        _require_no_group_ext_id(call)
        _validate_domain(call.domain or [], policy, call.model, uid)
        _require_company_filter(call, allowed_company_ids)
    elif call.method == "read":
        _validate_read_ids(call, uid)
        _require_fields(call, policy)
        if call.domain is not None:
            raise InvalidToolArgs("read no admite domain")
        _require_no_pagination(call)
        _require_no_group_ext_id(call)
    elif call.method == "context_get":
        _require_no_ids(call)
        _require_no_fields(call)
        if call.domain is not None:
            raise InvalidToolArgs("context_get no admite domain")
        _require_no_pagination(call)
        _require_no_group_ext_id(call)
        if call.active_test is not None:
            raise InvalidToolArgs("context_get no admite active_test")
    elif call.method == "has_group":
        _validate_current_uid_ids(call, uid)
        _require_no_fields(call)
        if call.domain is not None:
            raise InvalidToolArgs("has_group no admite domain")
        _require_no_pagination(call)
        if call.group_ext_id not in ALLOWED_GROUP_EXT_IDS:
            raise InvalidToolArgs(
                f"XML ID de grupo no permitido: {call.group_ext_id!r}"
            )
        if call.active_test is not None:
            raise InvalidToolArgs("has_group no admite active_test")

    if call.method not in {"search_read", "read"} and call.active_test is not None:
        raise InvalidToolArgs("active_test solo se admite en search_read/read")

    return call


def has_company_filter(
    call: RpcCall,
    allowed_company_ids: Sequence[int] | None = None,
) -> bool:
    """Indica si el dominio filtra por la compañía exigida y permitida."""

    field = COMPANY_FILTER_FIELDS.get(call.model)
    if field is None:
        return True
    return _domain_has_company_filter(
        call.domain or [],
        field,
        _normalize_company_ids(allowed_company_ids),
    )


# ---------------------------------------------------------------------------
# Validaciones internas
# ---------------------------------------------------------------------------


def _validate_common_arguments(call: RpcCall) -> None:
    if call.ids is not None:
        if not isinstance(call.ids, list) or len(call.ids) > MAX_IDS:
            raise InvalidToolArgs("ids debe ser una lista acotada")
        for value in call.ids:
            if not _is_positive_int(value):
                raise InvalidToolArgs("ids solo admite enteros positivos")

    if call.fields is not None:
        if not isinstance(call.fields, list):
            raise InvalidToolArgs("fields debe ser una lista")
        for value in call.fields:
            if not isinstance(value, str) or not value:
                raise InvalidToolArgs("fields solo admite nombres de campo")
            if value.split(".")[-1] in {"create", "write", "unlink"}:
                raise InvalidToolArgs(f"campo no permitido: {value}")

    if call.domain is not None and not isinstance(call.domain, list):
        raise InvalidToolArgs("domain debe ser una lista")

    if call.offset is not None and not _is_non_negative_int(call.offset):
        raise InvalidToolArgs("offset debe ser un entero no negativo")
    if call.limit is not None and not _is_non_negative_int(call.limit):
        raise InvalidToolArgs("limit debe ser un entero no negativo")

    if call.group_ext_id is not None and not isinstance(call.group_ext_id, str):
        raise InvalidToolArgs("group_ext_id debe ser un XML ID textual")

    if call.active_test is not None and not isinstance(call.active_test, bool):
        raise InvalidToolArgs("active_test debe ser booleano")


def _require_fields(call: RpcCall, policy: ModelPolicy) -> None:
    if not call.fields:
        raise InvalidToolArgs(f"{call.method} exige fields explícitos")
    for field in call.fields:
        if field not in policy.read_fields:
            raise InvalidToolArgs(
                f"campo no permitido para {call.model}: {field}"
            )


def _require_no_fields(call: RpcCall) -> None:
    if call.fields is not None:
        raise InvalidToolArgs(f"{call.method} no admite fields")


def _require_no_ids(call: RpcCall) -> None:
    if call.ids is not None:
        raise InvalidToolArgs(f"{call.method} no admite ids")


def _require_no_pagination(call: RpcCall) -> None:
    if call.offset is not None or call.limit is not None:
        raise InvalidToolArgs(f"{call.method} no admite offset/limit")


def _require_no_group_ext_id(call: RpcCall) -> None:
    if call.group_ext_id is not None:
        raise InvalidToolArgs(f"{call.method} no admite group_ext_id")


def _require_ids(call: RpcCall) -> list[int]:
    if not call.ids:
        raise InvalidToolArgs(f"{call.method} exige ids")
    return call.ids


def _validate_read_ids(call: RpcCall, uid: int | None) -> None:
    ids = _require_ids(call)
    if call.model == "res.users":
        if uid is None or ids != [uid]:
            raise InvalidToolArgs("res.users.read solo admite el uid actual")


def _validate_current_uid_ids(call: RpcCall, uid: int | None) -> None:
    if uid is None:
        raise InvalidToolArgs("has_group requiere el uid actual")
    if call.ids != [uid]:
        raise InvalidToolArgs("has_group solo admite ids=[uid]")


def _validate_domain(
    domain: list[Any],
    policy: ModelPolicy,
    model: str,
    uid: int | None,
) -> None:
    if not isinstance(domain, list):
        raise InvalidToolArgs("domain debe ser una lista")
    index = 0
    while index < len(domain):
        index = _parse_domain_expression(domain, index, policy, model, uid)


def _parse_domain_expression(
    domain: list[Any],
    index: int,
    policy: ModelPolicy,
    model: str,
    uid: int | None,
) -> int:
    if index >= len(domain):
        raise InvalidToolArgs("domain incompleto")

    token = domain[index]

    if isinstance(token, str):
        if token in {"&", "|"}:
            index = _parse_domain_expression(domain, index + 1, policy, model, uid)
            return _parse_domain_expression(domain, index, policy, model, uid)
        if token == "!":
            return _parse_domain_expression(domain, index + 1, policy, model, uid)
        raise InvalidToolArgs(f"operador lógico no permitido: {token!r}")

    if isinstance(token, (list, tuple)):
        _validate_leaf(token, policy, model, uid)
        return index + 1

    raise InvalidToolArgs("token de dominio no válido")


def _validate_leaf(
    leaf: Sequence[Any],
    policy: ModelPolicy,
    model: str,
    uid: int | None,
) -> None:
    if len(leaf) != 3:
        raise InvalidToolArgs("cada hoja del dominio debe tener 3 elementos")

    field, operator, value = leaf

    if not isinstance(field, str):
        raise InvalidToolArgs("el campo del dominio debe ser textual")
    if field not in policy.domain_fields:
        raise InvalidToolArgs(f"campo de dominio no permitido: {field}")
    if operator not in LEAF_OPERATORS:
        raise InvalidToolArgs(f"operador de hoja no permitido: {operator}")

    _validate_value(operator, value)

    if model == "res.users":
        if uid is None:
            raise InvalidToolArgs("res.users exige el uid actual en el dominio")
        if operator not in {"=", "in"}:
            raise InvalidToolArgs("res.users solo admite id = uid o id in [uid]")
        values = value if operator == "in" else [value]
        if not values or any(item != uid for item in values):
            raise InvalidToolArgs("res.users solo admite el uid actual")


def _validate_value(operator: str, value: Any) -> None:
    if operator in {"in", "not in"}:
        if not isinstance(value, (list, tuple)):
            raise InvalidToolArgs(f"{operator} exige una lista de valores")
        if len(value) > MAX_IN_VALUES:
            raise InvalidToolArgs(f"{operator} admite como máximo {MAX_IN_VALUES} valores")
        for item in value:
            if not _is_scalar(item):
                raise InvalidToolArgs(f"{operator} solo admite valores simples")
        return

    if operator in {"ilike", "=ilike"}:
        if not isinstance(value, str):
            raise InvalidToolArgs(f"{operator} exige un valor textual")
        return

    if operator in {">", ">=", "<", "<="}:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise InvalidToolArgs(f"{operator} exige un número o texto")
        return

    if not _is_scalar(value):
        raise InvalidToolArgs(f"{operator} exige un valor simple")


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _iter_leaves(domain: Iterable[Any]) -> Iterable[Sequence[Any]]:
    for token in domain:
        if isinstance(token, (list, tuple)) and len(token) == 3:
            yield token


def _normalize_company_ids(
    company_ids: Sequence[int] | None,
) -> set[int] | None:
    if company_ids is None:
        return None
    return {int(value) for value in company_ids}


def _domain_has_company_filter(
    domain: Iterable[Any],
    field: str,
    allowed_company_ids: set[int] | None,
) -> bool:
    for leaf_field, operator, value in _iter_leaves(domain):
        if leaf_field != field:
            continue
        if operator == "=" and _is_positive_int(value):
            if allowed_company_ids is None or value in allowed_company_ids:
                return True
        elif operator == "in" and isinstance(value, (list, tuple)) and value:
            if not all(_is_positive_int(item) for item in value):
                continue
            if allowed_company_ids is None or set(value).issubset(allowed_company_ids):
                return True
    return False


def _require_company_filter(
    call: RpcCall,
    allowed_company_ids: Sequence[int] | None,
) -> None:
    field = COMPANY_FILTER_FIELDS.get(call.model)
    if field is None:
        return
    if not has_company_filter(call, allowed_company_ids):
        raise InvalidToolArgs(
            f"{call.model} exige un filtro explícito por {field} "
            "acotado a las compañías permitidas"
        )
