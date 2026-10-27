"""Fixtures para tests de UI (T06+).

Monkeypatch de entorno y de `httpx.request` para tests sin red.
"""

from __future__ import annotations

from collections import deque
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from streamlit.testing.v1 import AppTest

# ---------------------------------------------------------------------------
# Entorno de prueba
# ---------------------------------------------------------------------------

@pytest.fixture
def ui_env(monkeypatch):
    """Configura variables de entorno para la UI."""
    monkeypatch.setenv("ASSISTANT_API_URL", "http://localhost:8000")
    monkeypatch.setenv("UI_API_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("API_SHARED_SECRET", "test-secret")


# ---------------------------------------------------------------------------
# Fake de httpx.request
# ---------------------------------------------------------------------------

class FakeAPI:
    """Fake para `httpx.request` que registra llamadas y devuelve respuestas encoladas."""

    def __init__(self):
        self.calls = []
        self.responses = deque()
        self._original_request = httpx.request

    def add_response(self, response: httpx.Response):
        """Añade una respuesta a la cola."""
        self.responses.append(response)

    def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        if self.responses:
            return self.responses.popleft()
        # Respuesta por defecto
        return httpx.Response(200, json={})

    def reset(self):
        self.calls.clear()
        self.responses.clear()


@pytest.fixture
def fake_api(monkeypatch):
    """Fixture que monkeypatchea `httpx.request` con un fake."""
    fake = FakeAPI()
    monkeypatch.setattr(httpx, "request", fake)
    return fake


# ---------------------------------------------------------------------------
# Helper para abrir la app
# ---------------------------------------------------------------------------

import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
APP_PATH = REPO_ROOT / "app.py"


@pytest.fixture
def open_app():
    """Abre la app Streamlit para testing."""
    def _open(path: str = str(APP_PATH)):
        at = AppTest.from_file(path, default_timeout=10)
        at.run()
        return at
    return _open


# ---------------------------------------------------------------------------
# Helper para login
# ---------------------------------------------------------------------------

def run_login(at: AppTest, token: str = "test-token-123"):
    """Ejecuta el flujo de login en la app."""
    # Encuentra el formulario y el input
    form = at.form("login_form")
    form.text_input(key="token_input").input(token)
    form.form_submit_button("Iniciar sesión").click()
    at.run()
    return at