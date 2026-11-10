"""Configuración por entorno y validaciones de arranque (T01)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from contafis_assistant.config import Settings


def test_defaults_follow_rnf_003(settings_env):
    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.odoo_db == "contea"
    assert settings.odoo_protocol == "json2"
    assert settings.odoo_timeout_seconds == 15
    assert settings.odoo_readonly_attested is False
    assert settings.llm_timeout_seconds == 60
    assert settings.retrieval_timeout_seconds == 10
    assert settings.chroma_collection == "contafis_normativa_v1"
    assert settings.vector_backend == "chroma"
    assert settings.bm25_index_path == "data/bm25_index.json"
    assert settings.bm25_index_version == 1
    assert settings.retrieval_dense_top_k == 10
    assert settings.retrieval_bm25_top_k == 10
    assert settings.retrieval_final_top_k == 5
    assert settings.rrf_k == 60
    assert settings.dense_min_score == 0.30
    assert settings.bm25_min_score == 1.0
    assert settings.fuzzy_min_score == 85
    assert settings.langsmith_tracing is False
    assert settings.langsmith_project == "contafis-assistant"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.max_message_chars == 2000
    assert settings.max_tool_calls == 3
    assert settings.recursion_limit == 12
    assert settings.detail_limit == 50
    assert settings.memory_max_turns == 20
    assert settings.memory_max_threads == 500
    assert settings.scope_check_ttl_seconds == 300

    assert settings.odoo_api_key.get_secret_value() == "test-odoo-key"
    assert settings.openai_api_key.get_secret_value() == "test-openai-key"


def test_langsmith_tracing_defaults_true_in_development(settings_env):
    settings_env.setenv("APP_ENV", "development")
    settings = Settings(_env_file=None)

    assert settings.langsmith_tracing is True


def test_missing_required_variable_fails(settings_env):
    settings_env.delenv("ODOO_URL")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_protocol_must_be_json2(settings_env):
    settings_env.setenv("ODOO_PROTOCOL", "xmlrpc")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("value", ["4", "0", "-1"])
def test_max_tool_calls_must_be_between_1_and_3(settings_env, value):
    settings_env.setenv("MAX_TOOL_CALLS", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_recursion_limit_must_cover_tool_budget(settings_env):
    settings_env.setenv("MAX_TOOL_CALLS", "3")
    settings_env.setenv("RECURSION_LIMIT", "9")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)

    settings_env.setenv("RECURSION_LIMIT", "10")
    settings = Settings(_env_file=None)
    assert settings.recursion_limit == 10


def test_remote_host_requires_shared_secret(settings_env):
    settings_env.setenv("API_HOST", "0.0.0.0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)

    settings_env.setenv("API_SHARED_SECRET", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)

    settings_env.setenv("API_SHARED_SECRET", "secreto-de-test")
    settings = Settings(_env_file=None)
    assert settings.api_shared_secret is not None


def test_loopback_host_does_not_require_secret(settings_env):
    settings_env.setenv("API_HOST", "localhost")
    settings = Settings(_env_file=None)

    assert settings.api_shared_secret is None


def test_invalid_app_env_fails(settings_env):
    settings_env.setenv("APP_ENV", "staging")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_vector_backend_accepts_local(settings_env):
    settings_env.setenv("VECTOR_BACKEND", "local")
    settings = Settings(_env_file=None)

    assert settings.vector_backend == "local"


def test_invalid_vector_backend_fails(settings_env):
    settings_env.setenv("VECTOR_BACKEND", "faiss")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_no_odoo_user_or_password_fields(settings_env):
    assert "odoo_user" not in Settings.model_fields
    assert "odoo_password" not in Settings.model_fields

    settings_env.setenv("ODOO_USER", "admin")
    settings_env.setenv("ODOO_PASSWORD", "admin")
    settings = Settings(_env_file=None)
    assert not hasattr(settings, "odoo_user")
    assert not hasattr(settings, "odoo_password")


def test_production_without_shared_secret_fails(settings_env):
    """T-1: AC-1 — Production sin API_SHARED_SECRET falla con ValidationError."""
    settings_env.setenv("APP_ENV", "production")
    settings_env.setenv("ODOO_READONLY_ATTESTED", "true")
    settings_env.setenv("API_HOST", "127.0.0.1")
    settings_env.delenv("API_SHARED_SECRET", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "API_SHARED_SECRET" in str(exc_info.value)


@pytest.mark.parametrize("secret_value", [None, "", "   "])
def test_production_with_empty_or_whitespace_secret_fails(settings_env, secret_value):
    """T-2: AC-1 — Production con secreto vacío o solo espacios falla igual."""
    settings_env.setenv("APP_ENV", "production")
    settings_env.setenv("ODOO_READONLY_ATTESTED", "true")
    settings_env.setenv("API_HOST", "127.0.0.1")
    if secret_value is None:
        settings_env.delenv("API_SHARED_SECRET", raising=False)
    else:
        settings_env.setenv("API_SHARED_SECRET", secret_value)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "API_SHARED_SECRET" in str(exc_info.value)


@pytest.mark.parametrize("app_env", ["development", "test"])
@pytest.mark.parametrize("secret_value", [None, "", "   "])
def test_non_production_without_secret_warns_and_continues(settings_env, capsys, app_env, secret_value):
    """T-6: AC-3 — Non-production sin secreto emite warning en stdout y continúa."""
    settings_env.setenv("APP_ENV", app_env)
    settings_env.setenv("ODOO_READONLY_ATTESTED", "true")
    settings_env.setenv("API_HOST", "127.0.0.1")
    if secret_value is None:
        settings_env.delenv("API_SHARED_SECRET", raising=False)
    else:
        settings_env.setenv("API_SHARED_SECRET", secret_value)

    settings = Settings(_env_file=None)

    assert settings.app_env == app_env
    captured = capsys.readouterr()
    assert "API_SHARED_SECRET" in captured.out
    assert app_env in captured.out


def test_non_production_without_app_env_defaults_to_development_warns(settings_env, capsys):
    """T-6: AC-3 — APP_ENV ausente defaulta a development y emite warning."""
    settings_env.delenv("APP_ENV", raising=False)
    settings_env.setenv("ODOO_READONLY_ATTESTED", "true")
    settings_env.setenv("API_HOST", "127.0.0.1")
    settings_env.delenv("API_SHARED_SECRET", raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    captured = capsys.readouterr()
    assert "API_SHARED_SECRET" in captured.out
    assert "development" in captured.out


def test_answer_model_defaults_to_llm_model(settings_env):
    settings = Settings(_env_file=None)

    assert settings.answer_model is None
    assert settings.answer_model_id == settings.llm_model


def test_answer_model_can_be_overridden_independently(settings_env):
    settings_env.setenv("ANSWER_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
    settings = Settings(_env_file=None)

    assert settings.answer_model == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert settings.answer_model_id == "nvidia/nemotron-3.5-lightning-30b-a3b"
    # El LLM de visión (digitize) no se ve afectado.
    assert settings.llm_model == "gpt-test"


def test_classifier_model_defaults_to_llm_model(settings_env):
    settings = Settings(_env_file=None)

    assert settings.classifier_model is None
    assert settings.classifier_model_id == settings.llm_model


def test_classifier_model_can_be_overridden_without_touching_vision(settings_env):
    settings_env.setenv("CLASSIFIER_MODEL", "deepseek-ai/deepseek-v4.1-flash")
    settings = Settings(_env_file=None)

    assert settings.classifier_model == "deepseek-ai/deepseek-v4.1-flash"
    assert settings.classifier_model_id == "deepseek-ai/deepseek-v4.1-flash"
    # El LLM de visión (digitize) conserva su modelo.
    assert settings.llm_model == "gpt-test"


def test_digitize_timeout_survives_real_nim_vision_latency(settings_env):
    """NIM vision tarda 30-65s con few-shots: el default debe cubrirlo."""
    settings = Settings(_env_file=None)

    assert settings.digitize_timeout_seconds >= 90
    assert settings.digitize_timeout_seconds <= 120
