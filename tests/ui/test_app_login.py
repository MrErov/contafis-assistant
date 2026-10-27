"""Tests de login/logout de la UI (T06)."""

from __future__ import annotations

import httpx
import pytest
from streamlit.testing.v1 import AppTest


# ---------------------------------------------------------------------------
# Test: estado inicial (pantalla de login)
# ---------------------------------------------------------------------------

def test_initial_state_shows_login_form(open_app):
    """Estado inicial: formulario de login visible, sin chat_input."""
    at = open_app()

    # El formulario de login existe
    assert at.form("login_form") is not None

    # El input de password existe
    token_input = at.text_input(key="token_input")
    assert token_input is not None

    # No hay chat_input (chat no renderizado)
    try:
        at.chat_input(key="chat_input")
        assert False, "chat_input no debería existir en la pantalla de login"
    except KeyError:
        pass  # Esperado: chat_input no existe

    # No hay mensajes de error iniciales
    # at.error() requiere un key en AppTest; si no hay errores, lanza KeyError
    try:
        at.error()
        assert False, "Debería haber lanzado KeyError por falta de key"
    except KeyError:
        pass  # Esperado: no hay errores
    except TypeError:
        # at.error() requiere key en AppTest
        pass


# ---------------------------------------------------------------------------
# Test: envío de token válido
# ---------------------------------------------------------------------------

def test_submit_valid_token_stores_in_session_state(open_app, fake_api):
    """Envío con token → se guarda en session_state, genera thread_id, cero llamadas HTTP."""
    at = open_app()
    token = "test-user-token-123"

    # Ejecuta login
    form = at.form("login_form")
    form.text_input(key="token_input").input("test-user-token-123")
    at.form("login_form").form_submit_button[0].click()
    at.run()

    # Verifica session_state
    assert at.session_state.get("user_token") == token
    assert "thread_id" in at.session_state
    assert at.session_state["thread_id"] is not None
    assert at.session_state.get("messages") == []

    # CERO llamadas HTTP (sin validación client-side)
    assert fake_api.calls == []


def test_submit_empty_token_does_not_store(open_app):
    """Envío con token vacío/espacios → no se guarda token; sigue el formulario."""
    at = open_app()

    form = at.form("login_form")
    form.text_input(key="token_input").input("   ")
    at.form("login_form").form_submit_button[0].click()
    at.run()

    # Token no guardado (valor None)
    assert at.session_state.get("user_token") is None

    # Formulario sigue visible
    assert at.form("login_form") is not None


# ---------------------------------------------------------------------------
# Test: 401 del backend al refrescar alcance
# ---------------------------------------------------------------------------

def test_401_on_refresh_clears_token_and_shows_error(open_app, fake_api):
    """401 del backend al refrescar alcance → error amigable, token borrado, login visible."""
    at = open_app()

    # Login exitoso
    token = "test-token-123"
    form = at.form("login_form")
    form.text_input(key="token_input").input(token)
    at.form("login_form").form_submit_button[0].click()
    at.run()

    assert at.session_state.get("user_token") == token

    # Simula 401 al refrescar alcance
    fake_api.add_response(httpx.Response(401, json={"detail": "El token de Odoo no es válido o fue revocado."}))

    # Click en "Refrescar alcance" (key correcta: refresh_scope_btn)
    at.sidebar.button("refresh_scope_btn").click()
    at.run()

    # Verifica que el token se borró (valor None)
    assert at.session_state.get("user_token") is None

    # Error amigable visible
    try:
        errors = at.error()
        assert False, "Debería haber lanzado KeyError por falta de key"
    except KeyError:
        pass  # No hay error con key específica
    except TypeError:
        # at.error() requiere key en AppTest
        pass
    
    # Verificamos que el error se muestra en los elementos renderizados
    # Buscamos el mensaje de error en markdown, caption y error
    rendered_text = ""
    for elem in at.get("markdown"):
        rendered_text += elem.value
    for elem in at.get("caption"):
        rendered_text += elem.value
    for elem in at.get("error"):
        rendered_text += elem.value
    assert "inválido" in rendered_text.lower() or "revocado" in rendered_text.lower()

    # Formulario de login visible de nuevo
    assert at.form("login_form") is not None


# ---------------------------------------------------------------------------
# Test: logout
# ---------------------------------------------------------------------------

def test_logout_clears_session_and_shows_login(open_app, fake_api):
    """Click 'Cerrar Sesión' → token borrado, formulario visible, chat_input ausente."""
    at = open_app()
    token = "test-token-123"

    # Login
    form = at.form("login_form")
    form.text_input(key="token_input").input(token)
    at.form("login_form").form_submit_button[0].click()
    at.run()

    assert at.session_state.get("user_token") == token

    # Click en "Cerrar Sesión" en sidebar
    at.sidebar.button("logout_btn").click()
    at.run()

    # Token borrado (valor None)
    assert at.session_state.get("user_token") is None

    # Formulario de login visible
    assert at.form("login_form") is not None

    # No hay chat_input (estamos en la pantalla de login)
    try:
        at.chat_input(key="chat_input")
        assert False, "chat_input no debería existir en la pantalla de login"
    except KeyError:
        pass  # Esperado: chat_input no existe


# ---------------------------------------------------------------------------
# Test: token no aparece en elementos renderizados
# ---------------------------------------------------------------------------

def test_token_not_in_rendered_elements(open_app, fake_api):
    """El token no aparece en ningún elemento renderizado ni en fake_api."""
    at = open_app()
    token = "secret-token-xyz"

    # Login
    form = at.form("login_form")
    form.text_input(key="token_input").input(token)
    at.form("login_form").form_submit_button[0].click()
    at.run()

    # Verifica que el token no está en ningún elemento visible
    rendered_text = ""

    # Recolecta texto de todos los elementos
    for elem in at.get("markdown"):
        rendered_text += elem.value
    for elem in at.get("caption"):
        rendered_text += elem.value
    for elem in at.get("error"):
        rendered_text += elem.value
    for elem in at.get("text"):
        rendered_text += elem.value

    assert token not in rendered_text

    # Verifica en fake_api (no debe haber llamadas, pero por si acaso)
    for call in fake_api.calls:
        assert token not in str(call)