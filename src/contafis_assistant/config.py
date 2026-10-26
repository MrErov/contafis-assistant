"""Configuración por entorno (RNF-003) con validaciones de arranque."""

from __future__ import annotations

import ipaddress
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_loopback(host: str) -> bool:
    normalized = host.strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    """Configuración tipada. Lee de variables de entorno y nunca de secretos fijos."""

    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        case_sensitive=False,
    )

    # Odoo JSON/2
    odoo_url: str
    odoo_db: str = "contea"
    odoo_protocol: str = "json2"
    odoo_api_key: SecretStr
    odoo_timeout_seconds: float = 15
    odoo_readonly_attested: bool = False
    app_env: Literal["development", "test", "production"] = "development"

    # LLM y embeddings
    openai_api_key: SecretStr
    llm_model: str
    embedding_model: str
    llm_timeout_seconds: float = 30

    # Corpus y retrieval
    chroma_path: str
    chroma_collection: str = "contafis_normativa_v1"
    bm25_index_path: str = "data/bm25_index.json"
    bm25_index_version: int = 1
    retrieval_dense_top_k: int = 10
    retrieval_bm25_top_k: int = 10
    retrieval_final_top_k: int = 5
    rrf_k: int = 60
    dense_min_score: float = 0.30
    bm25_min_score: float = 1.0
    retrieval_timeout_seconds: float = 10

    # Resolución de entidades
    fuzzy_min_score: int = 85

    # Observabilidad
    langsmith_tracing: bool | None = None
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "contafis-assistant"

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_shared_secret: SecretStr | None = None

    # Límites
    max_message_chars: int = 2000
    max_tool_calls: int = Field(default=3, ge=1, le=3)
    recursion_limit: int = 12
    detail_limit: int = 50
    memory_max_turns: int = 20
    memory_max_threads: int = 500
    scope_check_ttl_seconds: int = 300

    @field_validator("api_shared_secret", mode="before")
    @classmethod
    def _empty_secret_is_none(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            return value if value.get_secret_value().strip() else None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _validate_startup(self) -> "Settings":
        if self.odoo_protocol != "json2":
            raise ValueError("ODOO_PROTOCOL solo admite 'json2'")

        if self.recursion_limit < 2 * self.max_tool_calls + 4:
            raise ValueError(
                "RECURSION_LIMIT debe ser >= 2*MAX_TOOL_CALLS+4 "
                f"(actual: {self.recursion_limit}, "
                f"mínimo: {2 * self.max_tool_calls + 4})"
            )

        if not _is_loopback(self.api_host) and self.api_shared_secret is None:
            raise ValueError(
                "API_SHARED_SECRET es obligatorio cuando API_HOST no es loopback"
            )

        if self.langsmith_tracing is None:
            self.langsmith_tracing = self.app_env == "development"

        if self.app_env == "production" and not self.odoo_readonly_attested:
            raise ValueError(
                "ODOO_READONLY_ATTESTED=true es obligatorio en producción"
            )

        return self
