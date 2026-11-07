"""
Test de contrato de variables de entorno (T5).

Verifica que:
1. Todas las claves definidas en render.yaml tienen campos correspondientes en Settings
   (Pydantic mapea env vars UPPER_SNAKE_CASE a campos snake_case automáticamente)
2. No existe NVIDIA_API_KEY como campo de la app (se mapea a OPENAI_API_KEY)
"""

import pytest
from src.contafis_assistant.config import Settings


# Lista maestra extraída de render.yaml (las 25 claves reales del blueprint)
# Estas son las variables de entorno en UPPER_SNAKE_CASE
RENDER_YAML_KEYS = {
    # Secretos (8)
    "NVIDIA_API_KEY",
    "LANGSMITH_API_KEY",
    "ODOO_URL",
    "ODOO_DB",
    "ODOO_API_KEY",
    "ODOO_PASSWORD",
    "API_SHARED_SECRET",
    "OPENAI_API_KEY",
    # Literales (17)
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
    "LANGSMITH_TRACING",
    "LANGSMITH_HIDE_CONTENT",
    "ODOO_PROTOCOL",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSIONS",
    "APP_ENV",
    "OPENAI_BASE_URL",
    "LLM_MODEL",
    "CHROMA_PATH",
    "ODOO_READONLY_ATTESTED",
    "ODOO_TIMEOUT_SECONDS",
    "SCOPE_CHECK_TTL_SECONDS",
    "MAX_MESSAGE_CHARS",
    "MAX_TOOL_CALLS",
    "RECURSION_LIMIT",
}

# Claves que NO deben existir en Settings (casos especiales)
EXCLUDED_KEYS = {"NVIDIA_API_KEY", "ODOO_PASSWORD"}


def _env_to_field(env_key: str) -> str:
    """Convierte nombre de variable de entorno UPPER_SNAKE_CASE a nombre de campo snake_case."""
    return env_key.lower()


class TestEnvContract:
    """Contrato entre render.yaml y Settings (Pydantic)."""

    def test_all_render_keys_exist_in_settings(self):
        """Cada clave de render.yaml debe tener campo correspondiente en Settings."""
        settings_fields = set(Settings.model_fields.keys())
        missing = []
        for env_key in RENDER_YAML_KEYS:
            if env_key in EXCLUDED_KEYS:
                continue
            field_name = _env_to_field(env_key)
            if field_name not in settings_fields:
                missing.append(f"{env_key} -> {field_name}")
        assert not missing, f"Claves en render.yaml pero NO en Settings: {sorted(missing)}"

    def test_no_nvidia_api_key_in_settings(self):
        """NVIDIA_API_KEY NO debe ser campo de Settings (se usa OPENAI_API_KEY)."""
        assert "NVIDIA_API_KEY" not in Settings.model_fields, (
            "NVIDIA_API_KEY no debe existir en Settings; "
            "la app usa OPENAI_API_KEY (compatible OpenAI). "
            "render.yaml lo define solo para inyectarlo como OPENAI_API_KEY en el contenedor."
        )
        # También verificar que no existe como campo snake_case
        assert "nvidia_api_key" not in Settings.model_fields

    def test_no_odoo_password_in_settings(self):
        """ODOO_PASSWORD NO debe ser campo de Settings (se usa ODOO_API_KEY)."""
        assert "ODOO_PASSWORD" not in Settings.model_fields
        assert "odoo_password" not in Settings.model_fields

    def test_oai_base_url_is_nvidia_endpoint(self):
        """OPENAI_BASE_URL debe tener valor por defecto NVIDIA."""
        field = Settings.model_fields["openai_base_url"]
        assert field.default == "https://integrate.api.nvidia.com/v1"

    def test_embedding_dimensions_type(self):
        """EMBEDDING_DIMENSIONS debe ser int (no str)."""
        field = Settings.model_fields["embedding_dimensions"]
        assert field.annotation is int or "int" in str(field.annotation)

    def test_odoo_readonly_attested_default_false_staging(self):
        """ODOO_READONLY_ATTESTED default False para staging."""
        field = Settings.model_fields["odoo_readonly_attested"]
        assert field.default is False or field.default == "false"

    @pytest.mark.parametrize("env_key", sorted(RENDER_YAML_KEYS - EXCLUDED_KEYS))
    def test_each_key_has_annotation(self, env_key):
        """Cada clave debe tener anotación de tipo en Settings."""
        field_name = _env_to_field(env_key)
        if field_name in Settings.model_fields:
            field = Settings.model_fields[field_name]
            assert field.annotation is not None, f"{env_key} ({field_name}): sin type annotation"