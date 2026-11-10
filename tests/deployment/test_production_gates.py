"""Gates de producción y tests de seguridad (T8).

Verifica que:
1. Settings rechaza producción sin ODOO_READONLY_ATTESTED=true
2. Settings permite producción con ODOO_READONLY_ATTESTED=true
3. Settings permite desarrollo sin attestation
4. ScopeService.startup() falla en producción con scope_unsafe
5. ScopeService.startup() pasa en desarrollo con scope_unsafe (degradado)
6. ScopeService.startup() falla en producción con OdooError y reason=odoo_error
"""

from __future__ import annotations

import pytest

from src.contafis_assistant.config import Settings
from src.contafis_assistant.odoo.allowlist import RpcCall
from contafis_assistant.odoo.errors import OdooTransientError
from contafis_assistant.odoo.scope import (
    ScopeReason,
    ScopeService,
    ScopeSnapshot,
    ScopeStartupError,
)

from tests.unit.test_scope import FakeOdooClient, NOW, make_service


# ---------------------------------------------------------------------------
# Tests de validación de Settings (config.py)
# ---------------------------------------------------------------------------


def test_production_gate_without_attestation_fails():
    """Settings(app_env="production", odoo_readonly_attested=False) lanza ValueError."""
    with pytest.raises(ValueError) as exc_info:
        Settings(
            odoo_url="http://127.0.0.1:8069",
            odoo_api_key="test-key",
            openai_api_key="test-openai-key",
            llm_model="gpt-test",
            embedding_model="embedding-test",
            chroma_path="data/test-chroma",
            app_env="production",
            odoo_readonly_attested=False,
        )

    assert "ODOO_READONLY_ATTESTED=true es obligatorio en producción" in str(
        exc_info.value
    )


def test_production_gate_with_attestation_passes():
    """Settings(app_env="production", odoo_readonly_attested=True) NO lanza error."""
    settings = Settings(
        odoo_url="http://127.0.0.1:8069",
        odoo_api_key="test-key",
        openai_api_key="test-openai-key",
        llm_model="gpt-test",
        embedding_model="embedding-test",
        chroma_path="data/test-chroma",
        app_env="production",
        odoo_readonly_attested=True,
        api_shared_secret="test-secret",
    )

    assert settings.app_env == "production"
    assert settings.odoo_readonly_attested is True


def test_development_gate_passes_without_attestation():
    """Settings(app_env="development", odoo_readonly_attested=False) NO lanza error."""
    settings = Settings(
        odoo_url="http://127.0.0.1:8069",
        odoo_api_key="test-key",
        openai_api_key="test-openai-key",
        llm_model="gpt-test",
        embedding_model="embedding-test",
        chroma_path="data/test-chroma",
        app_env="development",
        odoo_readonly_attested=False,
    )

    assert settings.app_env == "development"
    assert settings.odoo_readonly_attested is False


# ---------------------------------------------------------------------------
# Tests de ScopeService.startup() con gates de producción
# ---------------------------------------------------------------------------


class FakeOdooClientUnsafe(FakeOdooClient):
    """Cliente fake que devuelve scope_unsafe (attestation=False con attestation_required=True)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Sobrescribimos groups para simular usuario interno no-admin
        self.groups = {
            "base.group_user": True,
            "base.group_system": False,
            "base.group_erp_manager": False,
        }


def test_scope_startup_fails_in_production_with_unsafe():
    """ScopeService.startup() en producción con scope_unsafe lanza ScopeStartupError."""
    client = FakeOdooClientUnsafe()
    # attested=False con attestation_required=True (default) -> scope_unsafe por attestation
    service, _ = make_service(client, env="production", attested=False)

    with pytest.raises(ScopeStartupError) as exc_info:
        service.startup()

    assert "scope_unsafe al arrancar en producción; el servicio no inicia" in str(
        exc_info.value
    )


def test_scope_startup_passes_in_development_with_unsafe():
    """ScopeService.startup() en desarrollo con scope_unsafe NO lanza error y devuelve snapshot."""
    client = FakeOdooClientUnsafe()
    service, _ = make_service(client, env="development", attested=False)

    snapshot = service.startup()

    assert isinstance(snapshot, ScopeSnapshot)
    assert snapshot.state.name == "scope_unsafe"
    assert snapshot.reason is ScopeReason.attestation
    assert snapshot.app_env == "development"
    # En desarrollo, unsafe con admin/attestation permite tools
    assert snapshot.tools_enabled is True


def test_scope_startup_fails_in_production_with_odoo_error():
    """ScopeService.startup() en producción con OdooError lanza ScopeStartupError y reason=odoo_error.
    
    Usa el patrón existente de `FakeOdooClient(fail=...)` y `make_service`.
    """
    # Usar el patrón existente: FakeOdooClient(fail=OdooTransientError(...)) + make_service
    client = FakeOdooClient(fail=OdooTransientError("Odoo connection failed"))
    service, _ = make_service(client, env="production", attested=True)

    with pytest.raises(ScopeStartupError) as exc_info:
        service.startup()

    assert "scope_unsafe al arrancar en producción; el servicio no inicia" in str(
        exc_info.value
    )

    # Verificar que el snapshot capturado antes de lanzar tiene reason=odoo_error
    assert service.snapshot is not None
    assert service.snapshot.reason is ScopeReason.odoo_error
    assert service.snapshot.state.name == "scope_unsafe"
    assert service.snapshot.app_env == "production"