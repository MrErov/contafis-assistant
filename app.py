"""UI Streamlit single-file (T06+).

Login → Chat con sidebar (panel de alcance + chips) + Logout.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

import uuid
from typing import Any

import httpx
import streamlit as st

from contafis_assistant.domain.contracts import ChatResponse, ScopeInfo


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

ASSISTANT_API_URL = os.getenv("ASSISTANT_API_URL", "http://localhost:8000")
UI_API_TIMEOUT_SECONDS = float(os.getenv("UI_API_TIMEOUT_SECONDS", "10"))
API_SHARED_SECRET = os.getenv("API_SHARED_SECRET")


# ---------------------------------------------------------------------------
# Estado de sesión
# ---------------------------------------------------------------------------

SESSION_KEYS = [
    "user_token",
    "thread_id",
    "messages",
    "scope_info",
    "scope_loading",
    "scope_error",
    "scope_chip",
    "auth_error",
    "chip_refresh_nonce",
]


def _init_state() -> None:
    """Inicializa claves de sesión ausentes."""
    for key in SESSION_KEYS:
        if key not in st.session_state:
            st.session_state[key] = None
    # Valores por defecto
    if st.session_state["messages"] is None:
        st.session_state["messages"] = []
    if st.session_state["scope_info"] is None:
        st.session_state["scope_info"] = None
    if st.session_state["scope_chip"] is None:
        st.session_state["scope_chip"] = None


# ---------------------------------------------------------------------------
# Helpers HTTP
# ---------------------------------------------------------------------------

def _api_headers(token: str) -> dict[str, str]:
    """Headers para requests a la API."""
    headers = {
        "Content-Type": "application/json",
        "X-User-Token": token,
    }
    if API_SHARED_SECRET:
        headers["X-API-Key"] = API_SHARED_SECRET
    return headers


def _api_post(endpoint: str, payload: dict[str, Any], token: str) -> httpx.Response:
    """POST a la API con manejo de errores."""
    url = f"{ASSISTANT_API_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        resp = httpx.request(
            "POST",
            url,
            headers=_api_headers(token),
            json=payload,
            timeout=UI_API_TIMEOUT_SECONDS,
        )
        return resp
    except httpx.TimeoutException:
        raise RuntimeError("timeout") from None
    except httpx.ConnectError:
        raise RuntimeError("connection_error") from None
    except httpx.HTTPError:
        raise RuntimeError("http_error") from None


def _api_get(endpoint: str, token: str) -> httpx.Response:
    """GET a la API."""
    url = f"{ASSISTANT_API_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        resp = httpx.request(
            "GET",
            url,
            headers=_api_headers(token),
            timeout=UI_API_TIMEOUT_SECONDS,
        )
        return resp
    except httpx.TimeoutException:
        raise RuntimeError("timeout") from None
    except httpx.ConnectError:
        raise RuntimeError("connection_error") from None
    except httpx.HTTPError:
        raise RuntimeError("http_error") from None


# ---------------------------------------------------------------------------
# Manejo de errores amigables
# ---------------------------------------------------------------------------

ERROR_MESSAGES = {
    401: "El token de Odoo no es válido o fue revocado. Por favor, inicia sesión de nuevo.",
    503: "El servicio no está disponible temporalmente. Inténtalo de nuevo en unos minutos.",
    "timeout": "La request ha tardado demasiado. Verifica tu conexión e inténtalo de nuevo.",
    "connection_error": "No se puede conectar al servidor. Verifica que el backend esté corriendo.",
    "http_error": "Error de comunicación con el servidor.",
}


def _friendly_error(exc: Exception | int | str) -> str:
    """Convierte error/exc en mensaje amigable."""
    if isinstance(exc, int):
        return ERROR_MESSAGES.get(exc, f"Error HTTP {exc}")
    if isinstance(exc, str):
        return ERROR_MESSAGES.get(exc, "Error desconocido")
    if isinstance(exc, RuntimeError):
        return ERROR_MESSAGES.get(str(exc), "Error desconocido")
    return "Error inesperado"


# ---------------------------------------------------------------------------
# Composición de mensaje con chip
# ---------------------------------------------------------------------------

def _compose_message(chip: str | None, prompt: str) -> str:
    """Compone el mensaje final con prefijo de chip si hay uno activo."""
    if chip:
        return f"Compañía {chip}: {prompt}"
    return prompt


# ---------------------------------------------------------------------------
# Carga de ScopeInfo
# ---------------------------------------------------------------------------

def _load_scope_info(token: str) -> ScopeInfo | None:
    """Carga ScopeInfo desde la API."""
    try:
        resp = _api_get("/scope", token)
        if resp.status_code == 200:
            return ScopeInfo.model_validate(resp.json())
        elif resp.status_code == 401:
            st.session_state["auth_error"] = "El token de Odoo no es válido o fue revocado."
            # Limpiar token en 401; messages a [] para evitar error en _render_messages antes del rerun
            for key in ["user_token", "thread_id", "scope_info", "scope_chip"]:
                st.session_state[key] = None
            st.session_state["messages"] = []
            st.rerun()
            return None
        else:
            st.session_state["scope_error"] = "No se pudo cargar el alcance de compañías."
            return None
    except RuntimeError as exc:
        st.session_state["scope_error"] = _friendly_error(exc)
        return None


# ---------------------------------------------------------------------------
# Sidebar: panel de alcance + logout
# ---------------------------------------------------------------------------

def _render_sidebar(token: str) -> None:
    """Renderiza el sidebar con panel de alcance y logout."""
    with st.sidebar:
        st.subheader("Alcance")

        # Botón refrescar - usa una key distinta para evitar conflictos
        refresh_clicked = st.button("Refrescar alcance", key="refresh_scope_btn")

        # Cargar scope solo si se presiona refrescar (no automáticamente)
        if st.session_state.get("do_refresh_scope"):
            st.session_state["do_refresh_scope"] = False
            with st.spinner("Cargando alcance..."):
                scope = _load_scope_info(token)
                if scope:
                    st.session_state["scope_info"] = scope

        # Verificar si se hizo click en refrescar (el botón devuelve True al hacer click)
        if refresh_clicked:
            st.session_state["do_refresh_scope"] = True
            st.rerun()

        scope: ScopeInfo | None = st.session_state.get("scope_info")

        # Error de carga
        if st.session_state.get("scope_error"):
            st.warning(st.session_state["scope_error"])

        # Panel de compañías
        if scope and scope.companies:
            st.write("**Compañías visibles:**")
            for comp in scope.companies:
                is_active = st.session_state.get("scope_chip") == comp.name
                btn_label = f"✓ {comp.name}" if is_active else comp.name
                if st.button(btn_label, key=f"chip_{comp.id}"):
                    if is_active:
                        st.session_state["scope_chip"] = None
                    else:
                        st.session_state["scope_chip"] = comp.name
                    st.rerun()

            # Mostrar chip activo
            if st.session_state.get("scope_chip"):
                st.info(f"Filtrando por: {st.session_state['scope_chip']}")
        elif scope and not scope.companies:
            st.info("No hay compañías visibles para este token.")

        # Separador
        st.divider()

        # Botón logout con key única
        if st.button("Cerrar Sesión", key="logout_btn", type="secondary", use_container_width=True):
            for key in SESSION_KEYS:
                st.session_state[key] = None
            st.rerun()


# ---------------------------------------------------------------------------
# Render de mensajes del chat
# ---------------------------------------------------------------------------

def _render_messages() -> None:
    """Renderiza el historial de mensajes."""
    for msg in st.session_state.get("messages", []):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant":
                if "status" in msg:
                    st.caption(f"Estado: {msg['status']}")
                warnings = msg.get("warnings")
                if warnings:
                    for w in warnings:
                        st.caption(f"⚠ {w.get('message', '')}")
                sources = msg.get("sources")
                if sources:
                    with st.expander("Fuentes"):
                        for s in sources:
                            st.write(f"- {s.get('document', 'N/A')} (pág. {s.get('page', '?')})")


# ---------------------------------------------------------------------------
# App principal
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="ContaFis Assistant",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("📊 ContaFis Assistant")

    _init_state()

    # Rama: Login
    if not st.session_state.get("user_token"):
        # Error de auth previo
        if st.session_state.get("auth_error"):
            st.error(st.session_state["auth_error"])
            st.session_state["auth_error"] = None

        with st.form("login_form"):
            st.subheader("Iniciar sesión")
            st.write("Ingresa tu API Key personal de Odoo 19 (Preferencias → Seguridad → Claves de API).")
            token_input = st.text_input(
                "Odoo API Token",
                type="password",
                key="token_input",
                placeholder="Ingresa tu API Key...",
            )
            submitted = st.form_submit_button("Iniciar sesión", type="primary", use_container_width=True)

            if submitted:
                token = token_input.strip() if token_input else ""
                if token:
                    st.session_state["user_token"] = token
                    st.session_state["thread_id"] = str(uuid.uuid4())
                    st.session_state["messages"] = []
                    st.session_state["scope_info"] = None
                    st.session_state["scope_chip"] = None
                    st.rerun()
                else:
                    st.warning("El token no puede estar vacío.")
        return

    # Rama: Chat (usuario autenticado)
    token = st.session_state["user_token"]

    # Sidebar
    _render_sidebar(token)

    # Historial
    _render_messages()

    # Input del chat
    prompt = st.chat_input("Escribe tu consulta...")
    if prompt:
        chip = st.session_state.get("scope_chip")
        message = _compose_message(chip, prompt)
        if not message.strip():
            return

        # Agrega mensaje de usuario al historial
        st.session_state["messages"].append({"role": "user", "content": message})

        # Enviar a la API
        payload = {"message": message, "thread_id": st.session_state["thread_id"]}
        try:
            resp = _api_post("/chat", payload, token)
        except RuntimeError as exc:
            st.error(_friendly_error(exc))
            return

        if resp.status_code == 401:
            st.session_state["auth_error"] = "El token de Odoo no es válido o fue revocado."
            for key in ["user_token", "thread_id", "messages", "scope_info", "scope_chip"]:
                st.session_state[key] = None
            st.rerun()
            return

        if resp.status_code == 503:
            st.error(_friendly_error(503))
            return

        if resp.status_code != 200:
            st.error(f"Error HTTP {resp.status_code}")
            return

        # Procesar respuesta
        try:
            chat_resp = ChatResponse.model_validate(resp.json())
        except Exception:
            st.error("Respuesta inválida del servidor.")
            return

        # Agrega respuesta del asistente
        assistant_msg = {
            "role": "assistant",
            "content": chat_resp.answer,
            "status": chat_resp.status,
            "warnings": [w.model_dump() for w in chat_resp.warnings] if chat_resp.warnings else [],
            "sources": [s.model_dump() for s in chat_resp.sources] if chat_resp.sources else [],
        }
        st.session_state["messages"].append(assistant_msg)
        st.rerun()


if __name__ == "__main__":
    main()