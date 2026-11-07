"""Tests para el endpoint de liveness GET /livez (T4)."""

import pytest
from fastapi.testclient import TestClient

from contafis_assistant.main import create_app
from contafis_assistant.config import Settings


@pytest.fixture
def client(monkeypatch):
    # Configurar API_SHARED_SECRET para que el middleware exija auth en otras rutas
    monkeypatch.setenv("API_SHARED_SECRET", "test-secret")
    # No inicializar runtime (simula estado pre-startup)
    settings = Settings()
    app = create_app(settings=settings)
    return TestClient(app)


def test_livez_200_sin_header(client):
    """GET /livez -> 200 sin header X-API-Key aunque API_SHARED_SECRET esté definido."""
    response = client.get("/livez")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "alive"
    assert "app_version" in data
    # Sin datos sensibles
    assert "scope" not in data
    assert "corpus" not in data
    assert "chroma" not in str(data).lower()
    assert "odoo" not in str(data).lower()


def test_livez_200_antes_de_startup(client):
    """GET /livez -> 200 aunque runtime.startup() no se haya ejecutado (runtime.ready == False)."""
    # El fixture ya crea la app sin llamar a startup
    response = client.get("/livez")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_livez_post_405(client):
    """POST /livez -> 405 Method Not Allowed."""
    response = client.post("/livez")
    assert response.status_code == 405


def test_health_sigue_401_sin_header(client):
    """GET /health -> 401 sin header X-API-Key (protección vigente)."""
    response = client.get("/health")
    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"