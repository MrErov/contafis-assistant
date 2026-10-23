"""Cliente Odoo JSON/2: headers, contexto, secuencia, retry y errores (T02)."""

from __future__ import annotations

import json
import logging
import threading
import time

import httpx
import pytest

from contafis_assistant.odoo.allowlist import RpcCall
from contafis_assistant.odoo.client import OdooClient, ToolBudget
from contafis_assistant.odoo.errors import (
    InvalidToolArgs,
    NoBudgetError,
    OdooAuthError,
    OdooConcurrencyError,
    OdooResponseError,
    OdooTransientError,
)

API_KEY = "s3cr3t-odoo-key"


def make_client(handler, *, allowed_company_ids=(1, 2), uid=7, timeout=5.0):
    requests = []

    def wrapped(request):
        requests.append(request)
        return handler(request, len(requests))

    transport = httpx.MockTransport(wrapped)
    client = OdooClient(
        url="http://odoo.example:8069",
        database="contea",
        api_key=API_KEY,
        timeout=timeout,
        transport=transport,
        allowed_company_ids=list(allowed_company_ids),
        uid=uid,
    )
    return client, requests


def search_call(**overrides):
    data = {
        "model": "res.company",
        "method": "search_read",
        "domain": [("active", "=", True)],
        "fields": ["id", "name"],
        "offset": 0,
        "limit": 50,
    }
    data.update(overrides)
    return RpcCall(**data)


def test_url_headers_and_body_for_json2():
    client, requests = make_client(lambda request, attempt: httpx.Response(200, json=[]))

    result = client.execute(search_call())

    assert result == []
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://odoo.example:8069/json/2/res.company/search_read"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["X-Odoo-Database"] == "contea"
    assert request.headers["Content-Type"].startswith("application/json")

    body = json.loads(request.content)
    assert body["domain"] == [["active", "=", True]]
    assert body["fields"] == ["id", "name"]
    assert body["offset"] == 0
    assert body["limit"] == 50
    assert body["context"] == {"allowed_company_ids": [1, 2]}


def test_allowed_company_ids_are_injected_in_every_call():
    responses = iter(
        [
            {"uid": 7, "allowed_company_ids": [1, 2]},
            [{"id": 1}],
            True,
            True,
            True,
        ]
    )
    client, requests = make_client(
        lambda request, attempt: httpx.Response(200, json=next(responses))
    )

    client.execute(RpcCall(model="res.users", method="context_get"))
    client.execute(
        RpcCall(
            model="res.company",
            method="search_read",
            domain=[("active", "=", True)],
            fields=["id"],
        )
    )
    client.execute(
        RpcCall(
            model="res.users",
            method="has_group",
            ids=[7],
            group_ext_id="base.group_user",
        )
    )
    client.execute(
        RpcCall(
            model="res.users",
            method="has_group",
            ids=[7],
            group_ext_id="base.group_system",
        )
    )
    client.execute(
        RpcCall(
            model="res.users",
            method="has_group",
            ids=[7],
            group_ext_id="base.group_erp_manager",
        )
    )

    assert len(requests) == 5
    for request in requests:
        body = json.loads(request.content)
        assert body["context"] == {"allowed_company_ids": [1, 2]}

    has_group_body = json.loads(requests[2].content)
    assert has_group_body["ids"] == [7]
    assert has_group_body["group_ext_id"] == "base.group_user"


def test_context_active_test_is_injected_without_free_context():
    client, requests = make_client(lambda request, attempt: httpx.Response(200, json=[]))

    client.execute(search_call(active_test=False))

    body = json.loads(requests[0].content)
    assert body["context"] == {"allowed_company_ids": [1, 2], "active_test": False}


def test_sequential_calls_do_not_overlap():
    state = {"in_flight": 0, "max_in_flight": 0, "order": []}
    guard = threading.Lock()

    def handler(request, attempt):
        with guard:
            state["in_flight"] += 1
            state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
            state["order"].append(attempt)
        time.sleep(0.02)
        with guard:
            state["in_flight"] -= 1
        return httpx.Response(200, json=[])

    client, _ = make_client(handler)
    client.execute(search_call())
    client.execute(search_call())

    assert state["order"] == [1, 2]
    assert state["max_in_flight"] == 1


def test_concurrent_execute_is_rejected_before_second_http():
    entered = threading.Event()
    release = threading.Event()

    def handler(request, attempt):
        entered.set()
        release.wait(timeout=2)
        return httpx.Response(200, json=[])

    client, requests = make_client(handler)
    result = {}

    def first_call():
        result["value"] = client.execute(search_call())

    thread = threading.Thread(target=first_call)
    thread.start()
    try:
        assert entered.wait(timeout=2)
        with pytest.raises(OdooConcurrencyError):
            client.execute(search_call())
        assert len(requests) == 1
    finally:
        release.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert result["value"] == []


def test_timeout_retries_once_with_budget():
    def handler(request, attempt):
        if attempt == 1:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json=[{"id": 1}])

    client, requests = make_client(handler)
    budget = ToolBudget(max_calls=3)

    result = client.execute(search_call(), budget=budget)

    assert result == [{"id": 1}]
    assert len(requests) == 2
    assert budget.used == 1


def test_5xx_retries_once_with_budget():
    def handler(request, attempt):
        if attempt == 1:
            return httpx.Response(503, json={"message": "busy"})
        return httpx.Response(200, json=[{"id": 1}])

    client, requests = make_client(handler)
    budget = ToolBudget(max_calls=3)

    result = client.execute(search_call(), budget=budget)

    assert result == [{"id": 1}]
    assert len(requests) == 2
    assert budget.used == 1


def test_retry_without_budget_does_not_happen():
    client, requests = make_client(
        lambda request, attempt: httpx.Response(503, json={"message": "busy"})
    )
    budget = ToolBudget(max_calls=1, used=1)

    with pytest.raises(OdooTransientError):
        client.execute(search_call(), budget=budget)

    assert len(requests) == 1
    assert budget.used == 1


def test_retry_without_budget_object_does_not_happen():
    client, requests = make_client(
        lambda request, attempt: httpx.Response(503, json={"message": "busy"})
    )

    with pytest.raises(OdooTransientError):
        client.execute(search_call())

    assert len(requests) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_are_not_retried(status):
    client, requests = make_client(
        lambda request, attempt: httpx.Response(
            status, json={"name": "Unauthorized", "message": f"bad key {API_KEY}"}
        )
    )
    budget = ToolBudget(max_calls=3)

    with pytest.raises(OdooAuthError) as exc:
        client.execute(search_call(), budget=budget)

    assert len(requests) == 1
    assert budget.used == 0
    assert API_KEY not in str(exc.value)


def test_concurrency_anomaly_retries_once():
    anomaly = {"name": "TypeError", "message": "unhashable type: 'dict'"}

    def handler(request, attempt):
        if attempt == 1:
            return httpx.Response(500, json=anomaly)
        return httpx.Response(200, json=[{"id": 1}])

    client, requests = make_client(handler)
    budget = ToolBudget(max_calls=3)

    result = client.execute(search_call(), budget=budget)

    assert result == [{"id": 1}]
    assert len(requests) == 2
    assert budget.used == 1


def test_concurrency_anomaly_twice_becomes_typed_error():
    anomaly = {"name": "TypeError", "message": "unhashable type: 'dict'"}
    client, requests = make_client(
        lambda request, attempt: httpx.Response(500, json=anomaly)
    )
    budget = ToolBudget(max_calls=3)

    with pytest.raises(OdooConcurrencyError) as exc:
        client.execute(search_call(), budget=budget)

    assert len(requests) == 2
    assert budget.used == 1
    assert exc.value.code == "upstream_error"
    assert exc.value.count_unavailable_possible is True


def test_truncation_retry_must_change_offset_or_limit_before_http():
    previous = search_call(offset=0, limit=50)
    client, requests = make_client(lambda request, attempt: httpx.Response(200, json=[]))

    with pytest.raises(InvalidToolArgs):
        client.execute(
            search_call(offset=0, limit=50),
            previous_truncated=previous,
            budget=ToolBudget(max_calls=3),
        )

    assert requests == []


def test_truncation_retry_with_new_limit_executes_and_consumes_budget():
    previous = search_call(offset=0, limit=50)
    client, requests = make_client(lambda request, attempt: httpx.Response(200, json=[]))
    budget = ToolBudget(max_calls=3)

    client.execute(
        search_call(offset=50, limit=50),
        previous_truncated=previous,
        budget=budget,
    )

    assert len(requests) == 1
    assert budget.used == 1
    body = json.loads(requests[0].content)
    assert body["offset"] == 50


def test_truncation_retry_without_budget_is_rejected_before_http():
    previous = search_call(offset=0, limit=50)
    client, requests = make_client(lambda request, attempt: httpx.Response(200, json=[]))
    budget = ToolBudget(max_calls=1, used=1)

    with pytest.raises(NoBudgetError):
        client.execute(
            search_call(offset=50, limit=50),
            previous_truncated=previous,
            budget=budget,
        )

    assert requests == []


def test_retry_after_pagination_consumes_another_budget():
    previous = search_call(offset=0, limit=50)

    def handler(request, attempt):
        if attempt == 1:
            return httpx.Response(503, json={"message": "busy"})
        return httpx.Response(200, json=[])

    client, requests = make_client(handler)
    budget = ToolBudget(max_calls=3)

    client.execute(
        search_call(offset=50, limit=50),
        previous_truncated=previous,
        budget=budget,
    )

    assert len(requests) == 2
    assert budget.used == 2


def test_json_parse_error_is_typed_and_not_retried():
    client, requests = make_client(
        lambda request, attempt: httpx.Response(200, content=b"not-json")
    )

    with pytest.raises(OdooResponseError):
        client.execute(search_call(), budget=ToolBudget(max_calls=3))

    assert len(requests) == 1


def test_non_transient_4xx_is_not_retried():
    client, requests = make_client(
        lambda request, attempt: httpx.Response(
            400, json={"name": "ValidationError", "message": "bad domain"}
        )
    )
    budget = ToolBudget(max_calls=3)

    with pytest.raises(OdooResponseError):
        client.execute(search_call(), budget=budget)

    assert len(requests) == 1
    assert budget.used == 0


def test_no_secret_in_logs_or_exceptions(caplog):
    caplog.set_level(logging.DEBUG)
    client, _ = make_client(
        lambda request, attempt: httpx.Response(
            401,
            json={
                "name": "werkzeug.exceptions.Unauthorized",
                "message": f"Invalid apikey {API_KEY}",
                "debug": f"headers: Authorization: Bearer {API_KEY}",
            },
        )
    )

    with pytest.raises(OdooAuthError) as exc:
        client.execute(search_call(), budget=ToolBudget(max_calls=3))

    assert API_KEY not in str(exc.value)
    assert API_KEY not in repr(exc.value)
    assert API_KEY not in caplog.text


def test_tool_budget_spend_and_remaining():
    budget = ToolBudget(max_calls=2)
    assert budget.remaining == 2

    budget.spend()
    assert budget.used == 1
    assert budget.remaining == 1

    budget.spend()
    assert budget.remaining == 0
    with pytest.raises(NoBudgetError):
        budget.spend()
