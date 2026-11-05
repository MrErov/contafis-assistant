"""Trazas metadata-only con sink inyectable (RF-052, RF-078, RNF-015).

Una traza solo transporta `request_id`, herramientas invocadas, estado,
latencia y `thread_id` opcional. Nunca el mensaje del usuario, resultados
crudos, nombres, RIF ni emails. En test/CI las trazas quedan deshabilitadas
porque `Settings.langsmith_tracing` es falso fuera de desarrollo.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from contafis_assistant.observability.redaction import redact_payload

logger = logging.getLogger(__name__)

TRACE_FIELDS = frozenset(
    {
        "event",
        "request_id",
        "thread_id",
        "status",
        "tools_used",
        "tool_call_count",
        "latency_ms",
    }
)

HIDE_CONTENT_ENV = {
    "LANGSMITH_HIDE_INPUTS": "true",
    "LANGSMITH_HIDE_OUTPUTS": "true",
}

DEFAULT_PROJECT = "contafis-assistant"


class TraceSink(Protocol):
    def emit(self, payload: dict[str, Any]) -> None: ...


class Tracer(Protocol):
    def record_turn(self, event: "TraceEvent") -> None: ...


@dataclass(frozen=True)
class TraceEvent:
    """Evento de traza con campos cerrados: solo metadatos del turno."""

    request_id: str
    status: str
    tools_used: tuple[str, ...] = ()
    tool_call_count: int = 0
    latency_ms: int = 0
    thread_id: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "event": "turn",
            "request_id": self.request_id,
            "thread_id": self.thread_id,
            "status": self.status,
            "tools_used": list(self.tools_used),
            "tool_call_count": int(self.tool_call_count),
            "latency_ms": int(self.latency_ms),
        }


@dataclass
class NullTracer:
    """No-op: valor por defecto cuando no hay tracing habilitado."""

    events: list[TraceEvent] = field(default_factory=list)

    def record_turn(self, event: TraceEvent) -> None:
        return None


@dataclass
class LangSmithTracer:
    """Emite eventos redactados a un sink inyectable (fake en los tests)."""

    sink: TraceSink
    project: str = DEFAULT_PROJECT
    known_names: Sequence[str] = ()
    events: list[TraceEvent] = field(default_factory=list)

    def record_turn(self, event: TraceEvent) -> None:
        payload = redact_payload(event.as_payload(), self.known_names)
        self.events.append(event)
        # Call sink.emit if available (for tests with FakeSink)
        # LangSmith Client doesn't have emit(), so we log instead
        if hasattr(self.sink, "emit") and callable(getattr(self.sink, "emit")):
            try:
                self.sink.emit(payload)
            except Exception as exc:
                logger.warning("Tracing sink emit failed: %s", exc, exc_info=True)
        else:
            logger.debug("Trace event: %s", payload)


def _default_sink() -> TraceSink:
    from langsmith import Client

    return Client()


def tracing_enabled(settings: Any) -> bool:
    """Verdadero solo si la configuración habilita el tracing explícitamente."""

    return bool(getattr(settings, "langsmith_tracing", False))


def tracer_from_settings(
    settings: Any,
    *,
    sink: TraceSink | None = None,
    known_names: Sequence[str] = (),
) -> Tracer:
    """Construye el tracer; nunca crea un cliente real durante los tests."""

    if not tracing_enabled(settings):
        return NullTracer()
    resolved = sink if sink is not None else _default_sink()
    return LangSmithTracer(
        sink=resolved,
        project=getattr(settings, "langsmith_project", DEFAULT_PROJECT),
        known_names=known_names,
    )


def hide_content_env(settings: Any | None = None) -> dict[str, str]:
    """Variables para ocultar entradas y salidas en LangSmith.

    `LANGSMITH_HIDE_CONTENT=false` permite ver el contenido en desarrollo; el
    valor por defecto (True) mantiene la política de RF-052/RF-078.
    """

    if settings is not None:
        hide = getattr(settings, "langsmith_hide_content", True)
        if not hide:
            return {}
    return dict(HIDE_CONTENT_ENV)


def langsmith_env(settings: Any) -> dict[str, str]:
    """Copia la configuración de tracing a `os.environ`.

    ``pydantic-settings`` lee el ``.env`` pero no lo publica en ``os.environ``;
    LangChain/LangGraph y ``langsmith.Client`` sólo se auto-instrumentan leyendo
    el entorno. Sin esta copia el servidor no genera ninguna traza aunque
    ``LANGSMITH_TRACING=true`` esté en el ``.env``.
    """

    if not tracing_enabled(settings):
        return {}
    env = {
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_PROJECT": str(
            getattr(settings, "langsmith_project", DEFAULT_PROJECT)
        ),
    }
    secret = getattr(settings, "langsmith_api_key", None)
    if secret is not None:
        value = (
            secret.get_secret_value()
            if hasattr(secret, "get_secret_value")
            else str(secret)
        )
        if value:
            env["LANGSMITH_API_KEY"] = value
    endpoint = getattr(settings, "langsmith_endpoint", None)
    if endpoint:
        env["LANGSMITH_ENDPOINT"] = str(endpoint)
    env.update(hide_content_env(settings))
    return env


def record_turn(tracer: Tracer | None, event: TraceEvent) -> None:
    """Registra un turno si hay tracer; si no, no hace nada."""

    if tracer is None:
        return None
    tracer.record_turn(event)
    return None


def trace_turn_state(
    tracer: Tracer | None,
    state: Any,
    *,
    latency_ms: int,
    thread_id: str | None = None,
) -> None:
    """Deriva el evento desde el estado final del grafo y lo registra."""

    if tracer is None:
        return None
    status = getattr(state, "status", None)
    status_value = getattr(status, "value", status) or "unknown"
    event = TraceEvent(
        request_id=str(getattr(state, "request_id", "")),
        status=str(status_value),
        tools_used=tuple(str(tool) for tool in getattr(state, "tools_used", ())),
        tool_call_count=int(getattr(state, "tool_call_count", 0) or 0),
        latency_ms=int(max(0, latency_ms)),
        thread_id=thread_id or getattr(state, "thread_id", None),
    )
    return record_turn(tracer, event)


__all__ = [
    "DEFAULT_PROJECT",
    "HIDE_CONTENT_ENV",
    "TRACE_FIELDS",
    "LangSmithTracer",
    "NullTracer",
    "TraceEvent",
    "TraceSink",
    "Tracer",
    "hide_content_env",
    "langsmith_env",
    "record_turn",
    "trace_turn_state",
    "tracer_from_settings",
    "tracing_enabled",
]
