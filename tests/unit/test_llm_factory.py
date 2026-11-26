"""Tests para llm_factory: Factory Pattern para NVIDIA NIM y Amazon Bedrock."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from contafis_assistant.config import Settings
from contafis_assistant.llm_factory import get_chat_model


class TestLLMFactory:
    """Tests para la función get_chat_model que selecciona el proveedor LLM."""

    def test_get_chat_model_nim_default(self, settings_env):
        """Por defecto (llm_provider=nim) debe devolver ChatOpenAI configurado para NIM."""
        settings = Settings(_env_file=None)
        settings.llm_provider = "nim"
        
        with patch("langchain_openai.ChatOpenAI") as mock_chat_openai:
            mock_instance = MagicMock()
            mock_chat_openai.return_value = mock_instance
            
            model = get_chat_model(settings)
            
            mock_chat_openai.assert_called_once()
            call_kwargs = mock_chat_openai.call_args.kwargs
            assert call_kwargs["model"] == settings.llm_model
            assert call_kwargs["api_key"] == settings.openai_api_key.get_secret_value()
            assert call_kwargs["base_url"] == settings.openai_base_url
            assert call_kwargs["timeout"] == settings.llm_timeout_seconds
            assert model is mock_instance

    def test_get_chat_model_nim_classifier(self, settings_env):
        """Cuando is_classifier=True, debe usar classifier_model_id."""
        settings = Settings(_env_file=None)
        settings.llm_provider = "nim"
        settings.classifier_model = "custom-classifier-model"
        
        with patch("langchain_openai.ChatOpenAI") as mock_chat_openai:
            mock_instance = MagicMock()
            mock_chat_openai.return_value = mock_instance
            
            model = get_chat_model(settings, is_classifier=True)
            
            call_kwargs = mock_chat_openai.call_args.kwargs
            assert call_kwargs["model"] == settings.classifier_model_id

    def test_get_chat_model_nim_answer_composer(self, settings_env):
        """Cuando is_classifier=False (default), debe usar answer_model_id para Answer Composer."""
        settings = Settings(_env_file=None)
        settings.llm_provider = "nim"
        settings.answer_model = "custom-answer-model"
        
        with patch("langchain_openai.ChatOpenAI") as mock_chat_openai:
            mock_instance = MagicMock()
            mock_chat_openai.return_value = mock_instance
            
            model = get_chat_model(settings, is_classifier=False)
            
            call_kwargs = mock_chat_openai.call_args.kwargs
            assert call_kwargs["model"] == settings.answer_model_id

    def test_get_chat_model_bedrock(self, settings_env):
        """Cuando llm_provider=bedrock, debe devolver ChatBedrockConverse."""
        settings = Settings(_env_file=None)
        settings.llm_provider = "bedrock"
        settings.aws_region = "us-west-2"
        settings.answer_model = "amazon.nova-pro-v1:0"
        
        with patch("langchain_aws.ChatBedrockConverse") as mock_bedrock:
            mock_instance = MagicMock()
            mock_bedrock.return_value = mock_instance
            
            model = get_chat_model(settings, is_classifier=False)
            
            mock_bedrock.assert_called_once()
            call_kwargs = mock_bedrock.call_args.kwargs
            assert call_kwargs["model"] == settings.answer_model_id
            assert call_kwargs["region_name"] == settings.aws_region
            assert model is mock_instance

    def test_get_chat_model_bedrock_classifier(self, settings_env):
        """Bedrock con is_classifier=True debe usar classifier_model_id."""
        settings = Settings(_env_file=None)
        settings.llm_provider = "bedrock"
        settings.classifier_model = "anthropic.claude-3-haiku-20240307-v1:0"
        
        with patch("langchain_aws.ChatBedrockConverse") as mock_bedrock:
            mock_instance = MagicMock()
            mock_bedrock.return_value = mock_instance
            
            model = get_chat_model(settings, is_classifier=True)
            
            call_kwargs = mock_bedrock.call_args.kwargs
            assert call_kwargs["model"] == settings.classifier_model_id

    def test_get_chat_model_bedrock_answer_composer(self, settings_env):
        """Bedrock con is_classifier=False debe usar answer_model_id."""
        settings = Settings(_env_file=None)
        settings.llm_provider = "bedrock"
        settings.answer_model = "anthropic.claude-3-5-sonnet-20241022-v2:0"
        
        with patch("langchain_aws.ChatBedrockConverse") as mock_bedrock:
            mock_instance = MagicMock()
            mock_bedrock.return_value = mock_instance
            
            model = get_chat_model(settings, is_classifier=False)
            
            call_kwargs = mock_bedrock.call_args.kwargs
            assert call_kwargs["model"] == settings.answer_model_id

    def test_get_chat_model_invalid_provider(self, settings_env):
        """Proveedor inválido debe lanzar ValueError."""
        settings = Settings(_env_file=None)
        settings.llm_provider = "invalid"  # type: ignore
        
        with pytest.raises(ValueError, match="Proveedor LLM no soportado"):
            get_chat_model(settings)


class TestSettingsBedrockConfig:
    """Tests para las nuevas configuraciones de Bedrock en Settings."""

    def test_llm_provider_defaults_to_nim(self, settings_env):
        """llm_provider debe default a 'nim'."""
        settings = Settings(_env_file=None)
        assert settings.llm_provider == "nim"

    def test_llm_provider_can_be_bedrock(self, settings_env):
        """llm_provider puede configurarse como 'bedrock'."""
        settings_env.setenv("LLM_PROVIDER", "bedrock")
        settings = Settings(_env_file=None)
        assert settings.llm_provider == "bedrock"

    def test_invalid_llm_provider_fails(self, settings_env):
        """LLM_PROVIDER inválido debe fallar validación."""
        settings_env.setenv("LLM_PROVIDER", "invalid")
        with pytest.raises(Exception):
            Settings(_env_file=None)

    def test_aws_region_default(self, settings_env):
        """aws_region debe default a 'us-east-1'."""
        settings = Settings(_env_file=None)
        assert settings.aws_region == "us-east-1"

    def test_aws_region_custom(self, settings_env):
        """aws_region puede configurarse."""
        settings_env.setenv("AWS_REGION", "eu-west-1")
        settings = Settings(_env_file=None)
        assert settings.aws_region == "eu-west-1"

    def test_bedrock_llm_model_default(self, settings_env):
        """bedrock_llm_model debe default a 'amazon.nova-lite-v1:0'."""
        settings = Settings(_env_file=None)
        assert settings.bedrock_llm_model == "amazon.nova-lite-v1:0"

    def test_bedrock_llm_model_custom(self, settings_env):
        """bedrock_llm_model puede configurarse."""
        settings_env.setenv("BEDROCK_LLM_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0")
        settings = Settings(_env_file=None)
        assert settings.bedrock_llm_model == "anthropic.claude-3-5-sonnet-20241022-v2:0"