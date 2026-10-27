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


def test_no_odoo_user_or_password_fields(settings_env):
    assert "odoo_user" not in Settings.model_fields
    assert "odoo_password" not in Settings.model_fields

    settings_env.setenv("ODOO_USER", "admin")
    settings_env.setenv("ODOO_PASSWORD", "admin")
    settings = Settings(_env_file=None)
    assert not hasattr(settings, "odoo_user")
    assert not hasattr(settings, "odoo_password")
