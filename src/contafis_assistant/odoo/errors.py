"""Errores tipados de la capa Odoo.

Ningún error transporta headers, API keys ni cuerpos crudos de respuesta.
"""

from __future__ import annotations


class OdooError(Exception):
    """Base de todos los fallos controlados de Odoo."""

    code = "upstream_error"
    count_unavailable_possible = False
    is_concurrency = False

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class InvalidToolArgs(OdooError):
    """La tool intentó un RPC fuera de la allowlist; se bloquea antes del HTTP."""

    code = "invalid_tool_args"

    def __init__(self, reason: str) -> None:
        super().__init__(f"Argumentos de tool inválidos: {reason}")
        self.reason = reason


class NoBudgetError(OdooError):
    """No queda presupuesto para un reintento o una nueva paginación."""

    code = "iteration_limit"

    def __init__(self, message: str = "Presupuesto de tools agotado") -> None:
        super().__init__(message)


class OdooAuthError(OdooError):
    """Credenciales rechazadas (401/403). Nunca se reintenta."""


class OdooTransientError(OdooError):
    """Error transitorio: timeout, red, 5xx o anomalía de concurrencia."""

    count_unavailable_possible = True


class OdooConcurrencyError(OdooTransientError):
    """Anomalía de concurrencia documentada o solapamiento de llamadas."""

    is_concurrency = True


class OdooResponseError(OdooError):
    """Respuesta no utilizable: 4xx no transitorio o JSON inválido."""
