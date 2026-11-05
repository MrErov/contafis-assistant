"""Embedder real de la ingesta y carga de .env en la CLI (ingest_cli/ingest)."""

from __future__ import annotations

import sys
import types

import pytest

from contafis_assistant.rag import ingest_cli
from contafis_assistant.rag.ingest import build_default_embedder


class _FakeEmbeddings:
    """Doble de `OpenAIEmbeddings` que solo guarda los kwargs recibidos."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


@pytest.fixture
def fake_langchain_openai(monkeypatch):
    """Evita instalar el cliente real de OpenAI (no hay credenciales ni red)."""
    module = types.ModuleType("langchain_openai")
    module.OpenAIEmbeddings = _FakeEmbeddings
    monkeypatch.setitem(sys.modules, "langchain_openai", module)
    return _FakeEmbeddings


def test_embedder_uses_model_from_settings_argument(fake_langchain_openai, monkeypatch):
    """El modelo de `Settings` manda sobre cualquier `os.getenv` residual."""
    monkeypatch.setenv("EMBEDDING_MODEL", "modelo-del-entorno")

    embedder = build_default_embedder("modelo-de-settings")

    assert embedder.kwargs["model"] == "modelo-de-settings"


def test_embedder_forwards_credentials_from_settings(fake_langchain_openai):
    """api_key y base_url provienen de Settings y llegan al embedder."""
    embedder = build_default_embedder(
        "modelo-x",
        api_key="clave-desde-settings",
        base_url="https://integrate.api.nvidia.com/v1",
    )

    assert embedder.kwargs["model"] == "modelo-x"
    assert embedder.kwargs["api_key"] == "clave-desde-settings"
    assert embedder.kwargs["base_url"] == "https://integrate.api.nvidia.com/v1"


def test_embedder_omits_credentials_when_not_supplied(fake_langchain_openai):
    """Sin key/base_url se delega en el entorno y no se pasan kwargs vacíos."""
    embedder = build_default_embedder("modelo-x")

    assert embedder.kwargs == {"model": "modelo-x"}


def test_embedder_requires_a_model(fake_langchain_openai, monkeypatch):
    """Sin modelo explícito ni `EMBEDDING_MODEL` en el entorno falla alto."""
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="EMBEDDING_MODEL es obligatorio"):
        build_default_embedder()


def test_load_settings_reads_env_file_from_repo_root(settings_env, monkeypatch, tmp_path):
    """La CLI lee el `.env` del repo aunque el CWD sea otro.

    `pydantic-settings` prioriza `os.environ` sobre el `dotenv`, por eso se borra
    la variable antes de escribir el `.env` temporal.
    """
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    (tmp_path / ".env").write_text(
        "ODOO_URL=http://127.0.0.1:8069\n"
        "ODOO_API_KEY=clave-dotenv\n"
        "OPENAI_API_KEY=openai-dotenv\n"
        "OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1\n"
        "LLM_MODEL=llm-dotenv\n"
        "EMBEDDING_MODEL=embeddings-dotenv\n"
        "CHROMA_PATH=data/test-chroma\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ingest_cli, "REPO_ROOT", tmp_path)

    settings = ingest_cli._load_settings()

    assert settings.embedding_model == "embeddings-dotenv"
    assert settings.openai_base_url == "https://integrate.api.nvidia.com/v1"


def test_load_settings_without_env_file_still_works(settings_env, monkeypatch, tmp_path):
    """Si no hay `.env` se sigue construyendo Settings (sin romper la CLI)."""
    monkeypatch.setattr(ingest_cli, "REPO_ROOT", tmp_path)

    settings = ingest_cli._load_settings()

    assert settings.embedding_model == "embedding-test"
