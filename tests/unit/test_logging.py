"""Logging estructurado y sin PII (RF-051, RF-053, RNF-005/008/015)."""

from __future__ import annotations

import io
import json
import logging

import pytest

from contafis_assistant.odoo.errors import OdooTransientError
from contafis_assistant.observability.logging import (
    JsonFormatter,
    configure_logging,
    get_logger,
    log_odoo_error,
    log_tool_result_summary,
    log_turn,
)
from contafis_assistant.observability.redaction import redact_text

API_KEY = "test-odoo-key"
CONNECTION_URL = "http://usuario:clave@127.0.0.1:8069"
REQUEST_ID = "6f1a2b3c-4d5e-4f60-8a91-b2c3d4e5f607"


class CollectingHandler(logging.Handler):
    """Handler de prueba que conserva las líneas JSON ya formateadas."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def json_log():
    handler = CollectingHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("contafis.test.observability")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, handler
    logger.handlers = []


@pytest.fixture(autouse=True)
def clean_root_handlers():
    root = logging.getLogger()
    previous = list(root.handlers)
    root.handlers = []
    yield
    root.handlers = previous


def test_module_does_not_configure_the_root_logger_on_import():
    import contafis_assistant.observability.logging as observability_logging

    assert callable(observability_logging.configure_logging)
    assert not any(
        isinstance(handler.formatter, observability_logging.JsonFormatter)
        for handler in logging.getLogger().handlers
        if handler.formatter is not None
    )


def test_configure_logging_emits_json_lines_without_basic_config():
    stream = io.StringIO()

    logger = configure_logging(level="INFO", logger="contafis.test.config", stream=stream)
    logger.info("evento de prueba")

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]

    assert lines
    assert logger.propagate is False
    for line in lines:
        assert isinstance(json.loads(line), dict)


def test_get_logger_returns_a_named_logger():
    assert get_logger("contafis.turno") is logging.getLogger("contafis.turno")


def test_info_turn_is_json_with_request_id_and_without_message_or_raw_results(json_log):
    logger, handler = json_log
    message = "obligaciones de Servicios Andinos, S.A. rif J-12345678-9"

    log_turn(
        logger,
        request_id=REQUEST_ID,
        status="ok",
        tools_used=["resolve_entity", "query_obligations"],
        latency_ms=42,
        thread_id="hilo-1",
        tool_call_count=2,
    )

    payload = json.loads(handler.lines[-1])

    assert payload["event"] == "turn"
    assert payload["request_id"] == REQUEST_ID
    assert payload["status"] == "ok"
    assert payload["tools_used"] == ["resolve_entity", "query_obligations"]
    assert payload["tool_call_count"] == 2
    assert payload["latency_ms"] == 42
    assert payload["thread_id"] == "hilo-1"
    text = "\n".join(handler.lines)
    assert message not in text
    assert "obligaciones: total 6" not in text
    assert "items" not in text


def test_tool_result_summary_only_logs_names_and_counts(json_log):
    logger, handler = json_log

    log_tool_result_summary(
        logger,
        request_id=REQUEST_ID,
        tool="query_clients",
        returned=6,
        total=6,
        truncated=False,
        warnings=["count_unavailable"],
    )

    payload = json.loads(handler.lines[-1])

    assert payload["event"] == "tool_result"
    assert payload["tool"] == "query_clients"
    assert payload["returned"] == 6
    assert payload["total"] == 6
    assert payload["truncated"] is False
    assert payload["warnings"] == ["count_unavailable"]
    assert set(payload) == {
        "event",
        "request_id",
        "tool",
        "returned",
        "total",
        "truncated",
        "warnings",
    }


def test_odoo_error_is_logged_without_headers_url_or_api_key(json_log):
    logger, handler = json_log

    log_odoo_error(
        logger,
        request_id=REQUEST_ID,
        exc=OdooTransientError(
            "Odoo no está disponible (HTTP 503) al llamar a "
            f"{CONNECTION_URL}; headers: Authorization: Bearer {API_KEY}"
        ),
    )

    text = "\n".join(handler.lines)
    payload = json.loads(handler.lines[-1])

    assert API_KEY not in text
    assert CONNECTION_URL not in text
    assert "usuario:clave" not in text
    assert "Authorization" not in text
    assert "Bearer" not in text
    assert payload["event"] == "odoo_error"
    assert payload["request_id"] == REQUEST_ID
    assert payload["error_type"] == "OdooTransientError"
    assert "[url]" in payload["message"]
    assert "[secreto]" in payload["message"]


def test_logged_warning_messages_are_redacted(json_log):
    logger, handler = json_log
    warning_message = (
        "No se pudo resolver la entidad: timeout en "
        "http://usuario:clave@127.0.0.1:8069 rif J-12345678-9 "
        "correo@andinos.com"
    )

    log_turn(
        logger,
        request_id=REQUEST_ID,
        status="upstream_error",
        tools_used=[],
        latency_ms=7,
        warnings=[warning_message],
    )

    text = "\n".join(handler.lines)
    payload = json.loads(handler.lines[-1])

    assert payload["warnings"] == [redact_text(warning_message)]
    assert "J-12345678-9" not in text
    assert "correo@andinos.com" not in text
    assert "usuario:clave" not in text


def test_every_logged_line_is_a_single_json_object(json_log):
    logger, handler = json_log

    log_turn(
        logger,
        request_id=REQUEST_ID,
        status="partial",
        tools_used=["query_obligations"],
        latency_ms=13,
        tool_call_count=1,
    )
    log_tool_result_summary(
        logger,
        request_id=REQUEST_ID,
        tool="query_obligations",
        returned=6,
        total=6,
    )

    assert len(handler.lines) == 2
    for line in handler.lines:
        assert "\n" not in line
        assert isinstance(json.loads(line), dict)
