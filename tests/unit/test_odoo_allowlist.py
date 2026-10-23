"""Allowlist cerrada de Odoo y validador recursivo (T02, Tabla 9.3-B)."""

from __future__ import annotations

import httpx
import pytest

from contafis_assistant.odoo.allowlist import (
    ALLOWED_GROUP_EXT_IDS,
    COMPANY_FILTER_FIELDS,
    MODEL_POLICIES,
    RpcCall,
    has_company_filter,
    validate_rpc,
)
from contafis_assistant.odoo.client import OdooClient
from contafis_assistant.odoo.errors import InvalidToolArgs

UID = 7


def _client(model, method="search_read", **kwargs):
    return RpcCall(model=model, method=method, **kwargs)


def _company_filter(model):
    field = COMPANY_FILTER_FIELDS[model]
    return [(field, "in", [1, 2])]


VALID_CALLS = [
    # res.company: search_read, read, search_count
    _client(
        "res.company",
        domain=[("active", "=", True)],
        fields=["id", "name", "active"],
        limit=50,
    ),
    _client("res.company", method="read", ids=[1, 2], fields=["id", "name"]),
    _client("res.company", method="search_count", domain=[("active", "=", True)]),
    # l10n.ve.compliance.client
    _client(
        "l10n.ve.compliance.client",
        domain=_company_filter("l10n.ve.compliance.client")
        + [("name", "ilike", "%el sol%")],
        fields=["id", "name", "rif", "company_id", "accountant_id", "partner_id"],
    ),
    _client(
        "l10n.ve.compliance.client",
        method="read",
        ids=[10],
        fields=["id", "name", "company_id"],
    ),
    _client(
        "l10n.ve.compliance.client",
        method="search_count",
        domain=[("company_id", "=", 1)],
    ),
    # l10n.ve.obligation
    _client(
        "l10n.ve.obligation",
        domain=_company_filter("l10n.ve.obligation") + [("state", "=", "pending")],
        fields=[
            "id",
            "name",
            "client_id",
            "obligation_type_id",
            "period",
            "due_date",
            "state",
        ],
    ),
    _client(
        "l10n.ve.obligation",
        method="read",
        ids=[20],
        fields=["id", "client_id", "due_date", "state"],
    ),
    _client(
        "l10n.ve.obligation",
        method="search_count",
        domain=_company_filter("l10n.ve.obligation"),
    ),
    # l10n.ve.document
    _client(
        "l10n.ve.document",
        domain=_company_filter("l10n.ve.document") + [("state", "=", "valid")],
        fields=["id", "client_id", "document_type_id", "state", "expiry_date"],
    ),
    _client(
        "l10n.ve.document",
        method="read",
        ids=[30],
        fields=["id", "client_id", "state"],
    ),
    _client(
        "l10n.ve.document",
        method="search_count",
        domain=_company_filter("l10n.ve.document"),
    ),
    # l10n.ve.cartelera.status
    _client(
        "l10n.ve.cartelera.status",
        domain=_company_filter("l10n.ve.cartelera.status")
        + [("year", "=", 2026), ("month", "=", "1")],
        fields=[
            "id",
            "client_id",
            "year",
            "month",
            "document_type_id",
            "state",
            "expiry_date",
            "document_id",
            "date_generated",
        ],
    ),
    _client(
        "l10n.ve.cartelera.status",
        method="read",
        ids=[40],
        fields=["id", "client_id", "year", "month", "state"],
    ),
    _client(
        "l10n.ve.cartelera.status",
        method="search_count",
        domain=_company_filter("l10n.ve.cartelera.status"),
    ),
    # l10n.ve.alert
    _client(
        "l10n.ve.alert",
        domain=_company_filter("l10n.ve.alert") + [("state", "=", "pending")],
        fields=[
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
        ],
    ),
    _client(
        "l10n.ve.alert",
        method="read",
        ids=[50],
        fields=["id", "client_id", "state", "date"],
    ),
    _client(
        "l10n.ve.alert",
        method="search_count",
        domain=_company_filter("l10n.ve.alert"),
    ),
    # l10n.ve.document.type
    _client(
        "l10n.ve.document.type",
        domain=[("required_for", "=", "company")],
        fields=["id", "name", "code", "required_for"],
    ),
    _client(
        "l10n.ve.document.type",
        method="read",
        ids=[60],
        fields=["id", "name", "code"],
    ),
    _client(
        "l10n.ve.document.type",
        method="search_count",
        domain=[("required_for", "=", "company")],
    ),
    # l10n.ve.obligation.type
    _client(
        "l10n.ve.obligation.type",
        domain=[("tax_type", "=", "iva")],
        fields=["id", "name", "tax_type", "periodicity"],
    ),
    _client(
        "l10n.ve.obligation.type",
        method="read",
        ids=[70],
        fields=["id", "name", "tax_type"],
    ),
    _client(
        "l10n.ve.obligation.type",
        method="search_count",
        domain=[("tax_type", "=", "iva")],
    ),
    # res.users
    _client("res.users", method="context_get"),
    _client(
        "res.users",
        method="read",
        ids=[UID],
        fields=["id", "company_ids", "group_ids"],
    ),
    # res.groups
    _client("res.groups", method="read", ids=[11], fields=["id", "name"]),
    # ir.rule (diagnóstico)
    _client(
        "ir.rule",
        domain=[("model_id", "=", 5)],
        fields=[
            "id",
            "name",
            "model_id",
            "groups",
            "domain_force",
            "perm_read",
            "perm_write",
            "perm_create",
            "perm_unlink",
        ],
    ),
]


@pytest.mark.parametrize("call", VALID_CALLS, ids=lambda call: f"{call.model}.{call.method}")
def test_table_9_3_b_valid_calls_are_accepted(call):
    assert validate_rpc(call, uid=UID) is call


@pytest.mark.parametrize("group_ext_id", sorted(ALLOWED_GROUP_EXT_IDS))
def test_has_group_accepts_only_the_three_allowed_xml_ids(group_ext_id):
    call = _client(
        "res.users",
        method="has_group",
        ids=[UID],
        group_ext_id=group_ext_id,
    )
    assert validate_rpc(call, uid=UID) is call


def test_allowed_group_xml_ids_are_exactly_the_spec_set():
    assert ALLOWED_GROUP_EXT_IDS == frozenset(
        {"base.group_system", "base.group_erp_manager", "base.group_user"}
    )


@pytest.mark.parametrize(
    "operator,value",
    [
        ("=", 1),
        ("!=", 1),
        ("in", [1, 2]),
        ("not in", [3]),
        (">", 1),
        (">=", 1),
        ("<", 5),
        ("<=", 5),
    ],
)
def test_allowed_leaf_operators_on_numeric_fields(operator, value):
    call = _client(
        "res.company",
        domain=[("id", operator, value)],
        fields=["id"],
    )
    assert validate_rpc(call, uid=UID) is call


@pytest.mark.parametrize("operator,value", [("ilike", "%sol%"), ("=ilike", "sol")])
def test_allowed_leaf_operators_on_text_fields(operator, value):
    call = _client(
        "res.company",
        domain=[("name", operator, value)],
        fields=["id"],
    )
    assert validate_rpc(call, uid=UID) is call


@pytest.mark.parametrize(
    "domain",
    [
        ["|", ("id", "=", 1), ("id", "=", 2)],
        ["&", ("active", "=", True), ("id", ">", 0)],
        ["!", ("active", "=", False)],
        ["|", "&", ("id", "=", 1), ("name", "ilike", "%a%"), ("id", "=", 2)],
    ],
)
def test_logical_operators_are_validated_recursively(domain):
    call = _client("res.company", domain=domain, fields=["id"])
    assert validate_rpc(call, uid=UID) is call


@pytest.mark.parametrize(
    "method",
    [
        "write",
        "unlink",
        "create",
        "action_confirm",
        "action_post",
        "read_group",
        "fields_get",
        "search",
    ],
)
def test_write_and_unsupported_methods_are_rejected(method):
    call = RpcCall(
        model="res.company",
        method=method,
        ids=[1],
        fields=["id"],
        domain=[],
    )
    with pytest.raises(InvalidToolArgs) as exc:
        validate_rpc(call, uid=UID)
    assert exc.value.code == "invalid_tool_args"


@pytest.mark.parametrize(
    "model,fields",
    [
        ("res.company", ["id", "email"]),
        ("l10n.ve.compliance.client", ["id", "compliance_status"]),
        ("l10n.ve.document.type", ["id", "active"]),
        ("l10n.ve.obligation.type", ["id", "active"]),
    ],
)
def test_fields_outside_the_table_are_rejected(model, fields):
    call = _client(model, fields=fields)
    with pytest.raises(InvalidToolArgs):
        validate_rpc(call, uid=UID)


@pytest.mark.parametrize(
    "field,operator,value",
    [
        ("name", "like", "x"),
        ("name", "not ilike", "x"),
        ("name", "not like", "x"),
        ("id", "child_of", 1),
    ],
)
def test_forbidden_leaf_operators_are_rejected(field, operator, value):
    call = _client("res.company", domain=[(field, operator, value)], fields=["id"])
    with pytest.raises(InvalidToolArgs):
        validate_rpc(call, uid=UID)


@pytest.mark.parametrize(
    "model",
    ["l10n.ve.document.type", "l10n.ve.obligation.type"],
)
def test_active_is_rejected_in_type_models(model):
    call = _client(model, domain=[("active", "=", True)], fields=["id"])
    with pytest.raises(InvalidToolArgs) as exc:
        validate_rpc(call, uid=UID)
    assert "active" in str(exc.value)


def test_active_is_not_part_of_type_policies():
    assert "active" not in MODEL_POLICIES["l10n.ve.document.type"].read_fields
    assert "active" not in MODEL_POLICIES["l10n.ve.obligation.type"].read_fields
    assert "active" not in MODEL_POLICIES["l10n.ve.document.type"].domain_fields
    assert "active" not in MODEL_POLICIES["l10n.ve.obligation.type"].domain_fields


@pytest.mark.parametrize(
    "domain",
    [
        ["&"],
        ["|", ("id", "=", 1)],
        ["!", "!"],
        [("id", "=", 1, 2)],
        [("id", "bogus", 1)],
        [("id", "in", 5)],
        [("name", "ilike", 5)],
        ["xor", ("id", "=", 1)],
        [("unknown", "=", 1)],
        [("id",)],
    ],
)
def test_malformed_domains_are_rejected(domain):
    call = _client("res.company", domain=domain, fields=["id"])
    with pytest.raises(InvalidToolArgs):
        validate_rpc(call, uid=UID)


@pytest.mark.parametrize(
    "call",
    [
        _client("res.company", domain=[]),
        _client("res.company", domain=[], fields=[]),
        _client(
            "res.company",
            method="search_count",
            domain=[],
            fields=["id"],
        ),
        _client("res.company", method="read", fields=["id"]),
        _client("res.company", method="read", ids=[], fields=["id"]),
        _client("res.company", method="read", ids=[0], fields=["id"]),
        _client(
            "res.company",
            method="read",
            ids=[1],
            fields=["id"],
            domain=[("id", "=", 1)],
        ),
        _client("res.users", method="context_get", fields=["id"]),
        _client("res.users", method="context_get", ids=[UID]),
        _client("res.company", domain=[], fields=["id"], ids=[1]),
        _client("res.company", domain=[], fields=["id"], limit=-1),
        _client("res.company", domain=[], fields=["id"], offset=-1),
        _client(
            "res.company",
            domain=[],
            fields=["id"],
            group_ext_id="base.group_user",
        ),
        _client(
            "res.users",
            method="has_group",
            ids=[UID],
            group_ext_id="base.group_user",
            domain=[("id", "=", UID)],
        ),
        _client(
            "res.users",
            method="has_group",
            ids=[UID],
            group_ext_id="base.group_user",
            fields=["id"],
        ),
    ],
)
def test_invalid_argument_shapes_are_rejected(call):
    with pytest.raises(InvalidToolArgs):
        validate_rpc(call, uid=UID)


def test_has_group_rejects_arbitrary_xml_id():
    call = _client(
        "res.users",
        method="has_group",
        ids=[UID],
        group_ext_id="base.group_no_such_group",
    )
    with pytest.raises(InvalidToolArgs):
        validate_rpc(call, uid=UID)


@pytest.mark.parametrize("ids", [[UID + 1], [UID, UID + 1], [], None])
def test_has_group_rejects_ids_other_than_current_uid(ids):
    call = _client(
        "res.users",
        method="has_group",
        ids=ids,
        group_ext_id="base.group_user",
    )
    with pytest.raises(InvalidToolArgs):
        validate_rpc(call, uid=UID)


def test_has_group_requires_known_uid():
    call = _client(
        "res.users",
        method="has_group",
        ids=[UID],
        group_ext_id="base.group_user",
    )
    with pytest.raises(InvalidToolArgs):
        validate_rpc(call, uid=None)


def test_has_group_requires_group_ext_id():
    call = _client("res.users", method="has_group", ids=[UID])
    with pytest.raises(InvalidToolArgs):
        validate_rpc(call, uid=UID)


def test_res_users_read_rejects_other_ids():
    call = _client(
        "res.users",
        method="read",
        ids=[UID + 1],
        fields=["id", "company_ids"],
    )
    with pytest.raises(InvalidToolArgs):
        validate_rpc(call, uid=UID)


def test_res_users_domain_must_be_current_uid():
    call = _client(
        "res.users",
        domain=[("id", "=", UID + 1)],
        fields=["id"],
    )
    with pytest.raises(InvalidToolArgs):
        validate_rpc(call, uid=UID)


@pytest.mark.parametrize(
    "model",
    [
        "l10n.ve.compliance.client",
        "l10n.ve.obligation",
        "l10n.ve.document",
        "l10n.ve.cartelera.status",
        "l10n.ve.alert",
    ],
)
def test_business_search_requires_explicit_company_filter(model):
    for method in ("search_read", "search_count"):
        call = _client(model, method=method, domain=[], fields=["id"])
        if method == "search_count":
            call = _client(model, method=method, domain=[])
        with pytest.raises(InvalidToolArgs):
            validate_rpc(call, uid=UID)


def test_wrong_company_field_is_not_a_company_filter():
    call = _client(
        "l10n.ve.obligation",
        domain=[("client_id", "in", [1, 2])],
        fields=["id"],
    )
    with pytest.raises(InvalidToolArgs):
        validate_rpc(call, uid=UID)
    assert not has_company_filter(call)


def test_company_filter_values_must_be_subset_of_allowed_companies():
    call = _client(
        "l10n.ve.obligation",
        domain=[("client_id.company_id", "in", [1, 2])],
        fields=["id"],
    )
    assert has_company_filter(call, allowed_company_ids=[1, 2]) is True
    assert has_company_filter(call, allowed_company_ids=[1]) is False
    with pytest.raises(InvalidToolArgs):
        validate_rpc(call, uid=UID, allowed_company_ids=[1])


def test_res_company_does_not_require_a_company_filter():
    call = _client(
        "res.company",
        domain=[("active", "=", True)],
        fields=["id"],
    )
    assert validate_rpc(call, uid=UID) is call
    assert has_company_filter(call)


def test_allowlist_contains_no_write_methods():
    for policy in MODEL_POLICIES.values():
        assert not (policy.methods & {"create", "write", "unlink"})
        assert not any(method.startswith("action_") for method in policy.methods)
        assert "read_group" not in policy.methods
        assert "fields_get" not in policy.methods
        assert "search" not in policy.methods


def _counting_client():
    requests = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json=True)
    )
    client = OdooClient(
        url="http://odoo.test",
        database="contea",
        api_key="test-key",
        transport=transport,
        allowed_company_ids=[1, 2],
        uid=UID,
    )
    return client, requests


def test_invalid_xml_id_is_rejected_before_http():
    client, requests = _counting_client()
    call = _client(
        "res.users",
        method="has_group",
        ids=[UID],
        group_ext_id="base.group_no_such_group",
    )
    with pytest.raises(InvalidToolArgs):
        client.execute(call)
    assert requests == []


def test_write_is_rejected_before_http():
    client, requests = _counting_client()
    call = RpcCall(
        model="res.company",
        method="write",
        ids=[1],
        fields=["id"],
        domain=[],
    )
    with pytest.raises(InvalidToolArgs):
        client.execute(call)
    assert requests == []


def test_forbidden_operator_is_rejected_before_http():
    client, requests = _counting_client()
    call = _client("res.company", domain=[("name", "like", "x")], fields=["id"])
    with pytest.raises(InvalidToolArgs):
        client.execute(call)
    assert requests == []
