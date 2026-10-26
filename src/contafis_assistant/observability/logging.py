"""Logging estructurado en JSON, sin PII ni resultados crudos (RF-053).

El módulo nunca configura el logging por sí solo al importarse: la
configuración es explícita mediante `configure_logging`.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TextIO

from contafis_assistant.observability.redaction import redact_text, safe_error_message

LOGGER_NAME = "contafis.observability"

_TEXT_REDACTOR = redact_text


class JsonFormatter(logging.Formatter):
    """Formatea cada registro como un único objeto JSON por línea."""

    def __init__(self, redactor=None) -> None:
        super().__init__()
        self._redactor = redactor

    def _redact(self, value: str) -> str:
        if self._redactor is None:
            return _TEXT_REDACTOR(value)
        return self._redactor(value)

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        try:
            payload: Any = json.loads(message)
            if not isinstance(payload, dict):
                payload = {"message": message}
        except (ValueError, TypeError):
            payload = {"message": message}

        if record.exc_info and "error_type" not in payload:
            payload["error_type"] = record.exc_info[0].__name__

        return json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)


def get_logger(name: str) -> logging.Logger:
    """Logger nombrado, sin configuración implícita."""

    return logging.getLogger(name)


def configure_logging(
    *,
    level: str | int = "INFO",
    logger: str | logging.Logger | None = None,
    stream: TextIO | None = None,
    redactor=None,
) -> logging.Logger:
    """Configura salida JSON por línea y desactiva la propagación."""

    resolved_level = (
        level if isinstance(level, int) else logging.getLevelName(str(level).upper())
    )
    target = logger if logger is not None else LOGGER_NAME
    resolved = target if isinstance(target, logging.Logger) else logging.getLogger(target)

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter(redactor=redactor))
    resolved.handlers = [handler]
    resolved.setLevel(resolved_level)
    resolved.propagate = False
    return resolved


def _emit(
    logger: logging.Logger,
    payload: Mapping[str, Any],
    level: int = logging.INFO,
) -> None:
    text = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
    logger.log(level, text)


def _tools(tools_used: Iterable[str] | None) -> list[str]:
    return [str(tool) for tool in (tools_used or [])]


def log_turn(
    logger: logging.Logger,
    *,
    request_id: str,
    status: str,
    tools_used: Iterable[str] | None = (),
    latency_ms: int | None = None,
    thread_id: str | None = None,
    tool_call_count: int | None = None,
    warnings: Iterable[str] | None = None,
) -> None:
    """Resumen del turno: nunca incluye el mensaje ni resultados crudos."""

    payload: dict[str, Any] = {
        "event": "turn",
        "request_id": request_id,
        "status": status,
        "tools_used": _tools(tools_used),
    }
    if thread_id is not None:
        payload["thread_id"] = thread_id
    if tool_call_count is not None:
        payload["tool_call_count"] = tool_call_count
    if latency_ms is not None:
        payload["latency_ms"] = latency_ms
    if warnings is not None:
        payload["warnings"] = [
            redact_text(str(message)) for message in warnings
        ]

    _emit(logger, payload)


def log_tool_result_summary(
    logger: logging.Logger,
    *,
    request_id: str,
    tool: str,
    returned: int | None = None,
    total: int | None = None,
    truncated: bool = False,
    warnings: Sequence[str] | None = None,
) -> None:
    """Solo nombres de tool y conteos: jamás ítems ni mensajes."""

    payload: dict[str, Any] = {
        "event": "tool_result",
        "request_id": request_id,
        "tool": tool,
        "truncated": bool(truncated),
        "warnings": [str(code) for code in (warnings or [])],
    }
    if returned is not None:
        payload["returned"] = returned
    if total is not None:
        payload["total"] = total

    _emit(logger, payload)


def log_odoo_error(
    logger: logging.Logger,
    *,
    request_id: str,
    exc: BaseException,
) -> None:
    """Error de Odoo sin headers, sin URL con credenciales y sin API key."""

    payload = {
        "event": "odoo_error",
        "request_id": request_id,
        "error_type": type(exc).__name__,
        "message": safe_error_message(exc),
    }
    _emit(logger, payload, level=logging.ERROR)


__all__ = [
    "LOGGER_NAME",
    "JsonFormatter",
    "configure_logging",
    "get_logger",
    "log_odoo_error",
    "log_tool_result_summary",
    "log_turn",
]
