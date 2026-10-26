"""Observabilidad transversal: redacción, logging estructurado y trazas."""

from __future__ import annotations

from contafis_assistant.observability.logging import (
    JsonFormatter,
    configure_logging,
    get_logger,
    log_odoo_error,
    log_tool_result_summary,
    log_turn,
)
from contafis_assistant.observability.redaction import (
    redact_exception,
    redact_payload,
    redact_text,
    safe_error_message,
    wrap_untrusted,
)
from contafis_assistant.observability.tracing import (
    LangSmithTracer,
    NullTracer,
    TraceEvent,
    Tracer,
    hide_content_env,
    record_turn,
    trace_turn_state,
    tracer_from_settings,
    tracing_enabled,
)

__all__ = [
    "JsonFormatter",
    "LangSmithTracer",
    "NullTracer",
    "TraceEvent",
    "Tracer",
    "configure_logging",
    "get_logger",
    "hide_content_env",
    "log_odoo_error",
    "log_tool_result_summary",
    "log_turn",
    "record_turn",
    "redact_exception",
    "redact_payload",
    "redact_text",
    "safe_error_message",
    "trace_turn_state",
    "tracer_from_settings",
    "tracing_enabled",
    "wrap_untrusted",
]
