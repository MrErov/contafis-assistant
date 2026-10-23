"""Arranque seguro, degradado y fail-fast según entorno y alcance (T08)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from contafis_assistant.domain.enums import ScopeState
from contafis_assistant.main import create_app
from contafis_assistant.odoo.scope import ScopeReason, ScopeStartupError

from .conftest import make_runtime, scope_snapshot, warning_codes


def test_invalid_configuration_does_not_start(settings_env):
    settings_env.delenv("ODOO_URL")

    with pytest.raises(ValidationError):
        create_app()


def test_production_unsafe_initial_does_not_start(make_settings):
    settings = make_settings(APP_ENV="production")
    unsafe = scope_snapshot(
        state=ScopeState.scope_unsafe,
        reason=ScopeReason.admin,
        app_env="production",
        is_admin=True,
    )
    runtime, scope, _corpus, _classifier, _tool = make_runtime(
        settings, snapshot=unsafe
    )

    with pytest.raises(ScopeStartupError):
        with TestClient(create_app(settings, runtime=runtime)):
            pass

    assert runtime.ready is False
    assert scope.startup_calls == 1


def test_dev_unsafe_admin_starts_degraded_with_scope_admin_key(make_settings):
    settings = make_settings(APP_ENV="test")
    unsafe = scope_snapshot(
        state=ScopeState.scope_unsafe,
        reason=ScopeReason.attestation,
        app_env="test",
        attested=False,
    )
    runtime, _scope, _corpus, classifier, tool = make_runtime(
        settings, snapshot=unsafe
    )

    with TestClient(create_app(settings, runtime=runtime)) as client:
        health = client.get("/health")
        chat = client.post("/chat", json={"message": "obligaciones"})

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["scope_complete"] is False

    assert chat.status_code == 200
    assert chat.json()["status"] == "partial"
    assert "scope_admin_key" in warning_codes(chat.json())
    assert classifier.calls == ["obligaciones"]
    assert len(tool.calls) == 1


def test_dev_non_internal_starts_degraded_without_tools_or_admin_key(
    make_settings,
):
    settings = make_settings(APP_ENV="test")
    non_internal = scope_snapshot(
        state=ScopeState.scope_unsafe,
        reason=ScopeReason.not_internal,
        app_env="test",
        is_internal=False,
        attested=True,
    )
    runtime, _scope, _corpus, classifier, tool = make_runtime(
        settings, snapshot=non_internal
    )

    with TestClient(create_app(settings, runtime=runtime)) as client:
        health = client.get("/health")
        chat = client.post("/chat", json={"message": "obligaciones"})

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["scope_complete"] is False

    body = chat.json()
    assert chat.status_code == 200
    assert body["status"] == "partial"
    assert "scope_admin_key" not in warning_codes(body)
    assert "scope_partial" in warning_codes(body)
    assert body["tool_call_count"] == 0
    assert classifier.calls == []
    assert tool.calls == []


def test_remote_bind_without_secret_does_not_start(make_settings):
    with pytest.raises(ValidationError):
        make_settings(API_HOST="0.0.0.0")
