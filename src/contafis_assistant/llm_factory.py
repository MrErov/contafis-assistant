"""Factory Pattern para instanciar modelos de chat según proveedor (NVIDIA NIM / Amazon Bedrock).

Este módulo centraliza la creación de instancias de modelos de lenguaje,
permitiendo alternar entre proveedores sin cambiar el código de negocio.
"""

from __future__ import annotations

from typing import Any

from contafis_assistant.config import Settings


def get_chat_model(settings: Settings, *, is_classifier: bool = False) -> Any:
    """Retorna una instancia de modelo de chat configurada según el proveedor.

    Args:
        settings: Configuración de la aplicación.
        is_classifier: Si True, usa el modelo configurado para clasificador
            (``classifier_model_id``). Si False, usa el modelo para Answer Composer
            (``answer_model_id``).

    Returns:
        Instancia de ``ChatOpenAI`` (para NIM) o ``ChatBedrockConverse`` (para Bedrock).

    Raises:
        ValueError: Si ``llm_provider`` no es "nim" ni "bedrock".
    """
    provider = settings.llm_provider

    if provider == "nim":
        return _get_nim_model(settings, is_classifier=is_classifier)
    elif provider == "bedrock":
        return _get_bedrock_model(settings, is_classifier=is_classifier)
    else:
        raise ValueError(f"Proveedor LLM no soportado: {provider}")


def _get_nim_model(settings: Settings, *, is_classifier: bool) -> Any:
    """Crea ChatOpenAI configurado para NVIDIA NIM."""
    from langchain_openai import ChatOpenAI

    model_id = (
        settings.classifier_model_id if is_classifier else settings.answer_model_id
    )

    return ChatOpenAI(
        model=model_id,
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
    )


def _get_bedrock_model(settings: Settings, *, is_classifier: bool) -> Any:
    """Crea ChatBedrockConverse configurado para Amazon Bedrock.

    Usa credenciales IAM del entorno (boto3 las resuelve automáticamente desde
    variables de entorno, ~/.aws/credentials, o role de instancia/ECS/EKS).
    """
    from langchain_aws import ChatBedrockConverse

    model_id = (
        settings.classifier_model_id if is_classifier else settings.answer_model_id
    )

    return ChatBedrockConverse(
        model=model_id,
        region_name=settings.aws_region,
        # Credenciales se toman del entorno via boto3 (IAM role, env vars, etc.)
    )