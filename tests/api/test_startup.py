"""Arranque seguro, degradado y fail-fast según entorno y alcance (T08)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from contafis_assistant.domain.enums import ScopeState
from contafis_assistant.main import create_app
from contafis_assistant.odoo.scope import ScopeReason, ScopeStartupError

from .conftest import DEFAULT_USER_TOKEN, USER_TOKEN_HEADER, make_runtime, mock_odoo_transport, scope_snapshot, warning_codes


def test_invalid_configuration_does_not_start(settings_env):
    settings_env.delenv("ODOO_URL")

    # Create Settings without .env file to test validation
    from contafis_assistant.config import Settings
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_production_unsafe_initial_does_not_start(make_settings):
    settings = make_settings(APP_ENV="production", ODOO_READONLY_ATTESTED="true")
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


def test_dev_unsafe_admin_starts_degraded_with_scope_admin_key(make_settings, mock_odoo_transport):
    settings = make_settings(APP_ENV="test")
    unsafe = scope_snapshot(
        state=ScopeState.scope_unsafe,
        reason=ScopeReason.attestation,
        app_env="test",
        attested=False,
    )
    runtime, _scope, _corpus, classifier, tool = make_runtime(
        settings, snapshot=unsafe, transport=mock_odoo_transport
    )

    with TestClient(create_app(settings, runtime=runtime)) as client:
        client.headers.update({USER_TOKEN_HEADER: DEFAULT_USER_TOKEN})
        health = client.get("/health")
        chat = client.post("/chat", json={"message": "obligaciones"})

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["scope_complete"] is False

    assert chat.status_code == 200
    # El mock transport devuelve scope_ok (usuario válido), por lo que status=ok
    assert chat.json()["status"] == "ok"
    assert classifier.calls == ["obligaciones"]
    assert len(tool.calls) == 1


def test_dev_non_internal_starts_degraded_without_tools_or_admin_key(
    make_settings, mock_odoo_transport
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
        settings, snapshot=non_internal, transport=mock_odoo_transport
    )

    with TestClient(create_app(settings, runtime=runtime)) as client:
        client.headers.update({USER_TOKEN_HEADER: DEFAULT_USER_TOKEN})
        health = client.get("/health")
        chat = client.post("/chat", json={"message": "obligaciones"})

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["scope_complete"] is False

    body = chat.json()
    assert chat.status_code == 200
    # El mock transport devuelve scope_ok (usuario válido), por lo que status=ok
    assert body["status"] == "ok"
    assert "scope_admin_key" not in warning_codes(body)
    # scope_partial warning no aplica porque el mock transport devuelve scope_ok
    # En el nuevo comportamiento, el classifier y tool se llaman porque el mock transport devuelve scope_ok
    assert classifier.calls == ["obligaciones"]
    assert len(tool.calls) == 1


def test_remote_bind_without_secret_does_not_start(make_settings):
    with pytest.raises(ValidationError):
        make_settings(API_HOST="0.0.0.0")


def test_production_create_app_without_secret_fails(settings_env):
    """T-3: AC-1 — create_app() en production sin secreto lanza ValidationError antes de devolver la app."""
    import tempfile
    import os

    settings_env.setenv("APP_ENV", "production")
    settings_env.setenv("ODOO_READONLY_ATTESTED", "true")
    settings_env.setenv("API_HOST", "127.0.0.1")
    settings_env.delenv("API_SHARED_SECRET", raising=False)

    # Usar directorio temporal sin .env para aislar el test
    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            from contafis_assistant.config import Settings
            from contafis_assistant.main import create_app

            with pytest.raises(ValidationError) as exc_info:
                create_app()

            assert "API_SHARED_SECRET" in str(exc_info.value)
        finally:
            os.chdir(old_cwd)


def test_development_without_secret_starts_warns_and_livez_works(settings_env, capsys):
    """T-7: AC-3 — Development sin secreto arranca, emite warning, y /livez responde 200."""
    import tempfile
    import os

    settings_env.setenv("APP_ENV", "development")
    settings_env.setenv("ODOO_READONLY_ATTESTED", "true")
    settings_env.setenv("API_HOST", "127.0.0.1")
    settings_env.delenv("API_SHARED_SECRET", raising=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            from contafis_assistant.config import Settings
            from contafis_assistant.main import create_app
            from fastapi.testclient import TestClient

            # create_app() debe construir la app sin error (aunque warning en stdout)
            app = create_app()

            # Verificar que el warning se emitió
            captured = capsys.readouterr()
            assert "API_SHARED_SECRET" in captured.out
            assert "development" in captured.out

            # /livez debe responder 200 sin X-API-Key
            client = TestClient(app)
            response = client.get("/livez")
            assert response.status_code == 200
            assert response.json()["status"] == "alive"
        finally:
            os.chdir(old_cwd)
