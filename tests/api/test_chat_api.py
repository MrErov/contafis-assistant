"""Contrato HTTP de `/chat`: validación manual, auth y respuesta completa (T08)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from contafis_assistant.main import create_app

from .conftest import make_runtime, warning_codes, DEFAULT_USER_TOKEN, USER_TOKEN_HEADER, mock_odoo_transport

CHAT_BODY = {"message": "obligaciones de la compañía", "thread_id": "thread_abc"}


def _uuid4(value: str) -> uuid.UUID:
    parsed = uuid.UUID(value)
    assert parsed.version == 4
    return parsed


def _client(settings, mock_odoo_transport):
    runtime, scope, corpus, classifier, tool = make_runtime(settings, transport=mock_odoo_transport)
    client = TestClient(create_app(settings, runtime=runtime))
    client.headers.update({USER_TOKEN_HEADER: DEFAULT_USER_TOKEN})
    return client, classifier, tool


def test_invalid_json_returns_http_200_without_tools_or_llm(make_settings, mock_odoo_transport):
    settings = make_settings()
    client, classifier, tool = _client(settings, mock_odoo_transport)

    with client:
        response = client.post(
            "/chat",
            content=b"{",
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "invalid_request"
    _uuid4(body["request_id"])
    _uuid4(body["thread_id"])
    assert body["tools_used"] == []
    assert body["tool_call_count"] == 0
    assert classifier.calls == []
    assert tool.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": None},
        {"message": 10},
        {"message": ""},
        {"message": "   "},
        {"message": "x" * 2001},
    ],
)
def test_invalid_message_returns_invalid_request(make_settings, mock_odoo_transport, payload):
    settings = make_settings()
    client, classifier, tool = _client(settings, mock_odoo_transport)

    with client:
        response = client.post("/chat", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "invalid_request"
    assert body["data"] == []
    assert body["tool_call_count"] == 0
    assert classifier.calls == []
    assert tool.calls == []


@pytest.mark.parametrize("thread_id", ["bad id", "a/b", "x" * 65, 12, True])
def test_invalid_thread_id_generates_uuid4_and_does_not_process(
    make_settings, mock_odoo_transport, thread_id
):
    settings = make_settings()
    client, classifier, tool = _client(settings, mock_odoo_transport)

    with client:
        response = client.post(
            "/chat",
            json={"message": "obligaciones", "thread_id": thread_id},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "invalid_request"
    _uuid4(body["thread_id"])
    assert body["thread_id"] != str(thread_id)
    assert classifier.calls == []
    assert tool.calls == []


def test_absent_thread_id_generates_uuid4_and_runs_normal_turn(make_settings, mock_odoo_transport):
    settings = make_settings()
    client, classifier, tool = _client(settings, mock_odoo_transport)

    with client:
        response = client.post("/chat", json={"message": "obligaciones"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    _uuid4(body["thread_id"])
    assert classifier.calls == ["obligaciones"]
    assert len(tool.calls) == 1
    assert "thread_context_unavailable" not in warning_codes(body)


def test_valid_thread_id_is_preserved(make_settings, mock_odoo_transport):
    settings = make_settings()
    client, _classifier, _tool = _client(settings, mock_odoo_transport)

    with client:
        response = client.post("/chat", json=CHAT_BODY)

    assert response.status_code == 200
    assert response.json()["thread_id"] == "thread_abc"


def test_response_contains_every_contract_field(make_settings, mock_odoo_transport):
    settings = make_settings()
    client, _classifier, _tool = _client(settings, mock_odoo_transport)

    with client:
        response = client.post("/chat", json=CHAT_BODY)

    body = response.json()
    assert set(body) == {
        "request_id",
        "thread_id",
        "status",
        "answer",
        "data",
        "sources",
        "tools_used",
        "tool_call_count",
        "entities",
        "options",
        "truncated",
        "warnings",
    }
    _uuid4(body["request_id"])
    assert body["answer"]
    assert body["data"][0]["kind"] == "obligations"
    assert body["data"][0]["source"] == "odoo"
    assert body["sources"] == []
    assert body["tools_used"] == ["query_obligations"]
    assert body["tool_call_count"] == 1
    assert body["entities"] == []
    assert body["options"] == []
    assert body["truncated"] is False
    assert body["warnings"] == []


def test_api_key_is_required_for_chat_and_health_when_secret_is_configured(
    make_settings, mock_odoo_transport
):
    settings = make_settings(API_SHARED_SECRET="s3cr3t")
    client, _classifier, _tool = _client(settings, mock_odoo_transport)

    with client:
        assert client.post("/chat", json=CHAT_BODY).status_code == 401
        assert (
            client.post(
                "/chat",
                json=CHAT_BODY,
                headers={"X-API-Key": "incorrecto"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/chat",
                json=CHAT_BODY,
                headers={"X-API-Key": "s3cr3t"},
            ).status_code
            == 200
        )

        assert client.get("/health").status_code == 401
        assert (
            client.get("/health", headers={"X-API-Key": "incorrecto"}).status_code
            == 401
        )
        assert (
            client.get(
                "/health", headers={"X-API-Key": "s3cr3t"}
            ).status_code
            == 200
        )


def test_api_key_uses_constant_time_comparison(make_settings, monkeypatch, mock_odoo_transport):
    from contafis_assistant.api import security

    calls: list[tuple[str, str]] = []

    def spy(provided, expected):
        calls.append((provided, expected))
        return True

    monkeypatch.setattr(security, "compare_digest", spy)
    settings = make_settings(API_SHARED_SECRET="s3cr3t")
    client, _classifier, _tool = _client(settings, mock_odoo_transport)

    with client:
        response = client.post(
            "/chat",
            json=CHAT_BODY,
            headers={"X-API-Key": "s3cr3t"},
        )

    assert response.status_code == 200
    assert calls == [(b"s3cr3t", b"s3cr3t")]


def test_no_comparison_without_configured_secret(make_settings, monkeypatch, mock_odoo_transport):
    from contafis_assistant.api import security

    calls: list[tuple[str, str]] = []

    def spy(provided, expected):
        calls.append((provided, expected))
        return True

    monkeypatch.setattr(security, "compare_digest", spy)
    settings = make_settings()
    client, _classifier, _tool = _client(settings, mock_odoo_transport)

    with client:
        assert client.post("/chat", json=CHAT_BODY).status_code == 200

    assert calls == []
