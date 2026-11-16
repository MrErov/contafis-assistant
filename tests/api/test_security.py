"""CR-01: la API key falla cerrada cuando no hay secreto configurado."""

from __future__ import annotations

from fastapi.testclient import TestClient

from contafis_assistant.api.security import verify_api_key
from contafis_assistant.main import create_app


def test_verify_api_key_without_expected_secret_fails_closed():
    """Sin secreto esperado no se acepta ninguna key, ni siquiera ausente."""
    assert verify_api_key(None, None) is False
    assert verify_api_key("cualquier-key", None) is False
    assert verify_api_key(None, "") is False


def test_verify_api_key_without_expected_secret_allows_test_env():
    """Excepción acotada: APP_ENV=test mantiene operativa la suite."""
    assert verify_api_key(None, None, app_env="test") is True
    assert verify_api_key("cualquier-key", "", app_env="test") is True


def test_verify_api_key_still_compares_when_secret_is_configured():
    """Con secreto configurado se mantiene la comparación en tiempo constante."""
    assert verify_api_key(None, "s3cr3t") is False
    assert verify_api_key("incorrecto", "s3cr3t") is False
    assert verify_api_key("s3cr3t", "s3cr3t") is True


def test_health_fails_closed_without_shared_secret(make_settings):
    """APP_ENV=development sin API_SHARED_SECRET -> /health responde 401."""
    settings = make_settings(APP_ENV="development")
    assert settings.api_shared_secret is None

    client = TestClient(create_app(settings=settings))

    assert client.get("/health").status_code == 401
    assert (
        client.get("/health", headers={"X-API-Key": "cualquier-key"}).status_code
        == 401
    )


def test_health_not_blocked_without_secret_in_test_env(make_settings):
    """APP_ENV=test sin API_SHARED_SECRET no bloquea por API key."""
    settings = make_settings()
    assert settings.app_env == "test"
    assert settings.api_shared_secret is None

    client = TestClient(create_app(settings=settings))

    # El runtime no está inicializado, así que /health responde 503;
    # lo relevante es que el middleware no lo corta con 401.
    assert client.get("/health").status_code != 401
