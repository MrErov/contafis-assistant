"""Trazas metadata-only y deshabilitadas en test (RF-052, RF-078, RNF-015)."""

from __future__ import annotations

import json

import pytest

from contafis_assistant.config import Settings
from contafis_assistant.domain.contracts import EntityRef, ObligationsResult
from contafis_assistant.domain.enums import EntityType, Intent, Status
from contafis_assistant.graph.build import GraphDependencies, run_turn
from contafis_assistant.graph.classify import ClassificationDraft, ClassificationResult
from contafis_assistant.graph.executor import ToolOutcome
from contafis_assistant.graph.state import ToolName, TurnState
from contafis_assistant.odoo.tools.entities import EntityResolution
from contafis_assistant.observability.tracing import (
    TRACE_FIELDS,
    LangSmithTracer,
    NullTracer,
    TraceEvent,
    hide_content_env,
    trace_turn_state,
    tracer_from_settings,
    tracing_enabled,
)

KNOWN_NAMES = ["Servicios Andinos, S.A.", "Distribuidora El Sol, C.A."]
USER_MESSAGE = (
    "obligaciones de Servicios Andinos, S.A. rif J-12345678-9 "
    "contacto@andinos.com 0412-1234567"
)
REQUEST_ID = "6f1a2b3c-4d5e-4f60-8a91-b2c3d4e5f607"


class FakeSink:
    """Sink inyectable: recolecta payloads sin tocar la red."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def emit(self, payload: dict) -> None:
        self.payloads.append(payload)


class FakeClassifier:
    def __call__(self, _message: str) -> ClassificationResult:
        return ClassificationResult(
            classification=ClassificationDraft(
                intent=Intent.erp,
                entity_mentions=["Servicios Andinos, S.A."],
            )
        )


class FakeResolver:
    def __call__(self, _mention: str) -> EntityResolution:
        return EntityResolution(
            status=Status.ok,
            entity=EntityRef(
                id=12,
                name="Servicios Andinos, S.A.",
                type=EntityType.client,
                company_id=85,
                company_name="Andina",
            ),
        )


class SpyTool:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _args) -> ToolOutcome:
        self.calls += 1
        return ToolOutcome(
            status=Status.ok,
            data=[ObligationsResult(total=6, returned=6, truncated=False)],
        )


def _deps(tracer) -> GraphDependencies:
    return GraphDependencies(
        classifier=FakeClassifier(),
        tools={ToolName.query_obligations: SpyTool()},
        entity_resolver=FakeResolver(),
        tracer=tracer,
        known_names=KNOWN_NAMES,
    )


def _serialize(payloads: list[dict]) -> str:
    return "\n".join(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) for payload in payloads
    )


def test_tracing_is_disabled_in_test_environment(settings_env):
    settings = Settings(_env_file=None)

    assert settings.app_env == "test"
    assert settings.langsmith_tracing is False
    assert tracing_enabled(settings) is False

    sink = FakeSink()
    tracer = tracer_from_settings(settings, sink=sink, known_names=KNOWN_NAMES)

    assert isinstance(tracer, NullTracer)
    tracer.record_turn(TraceEvent(request_id="r", status="ok"))
    assert sink.payloads == []


def test_tracing_is_enabled_by_default_in_development(settings_env):
    settings_env.setenv("APP_ENV", "development")
    settings = Settings(_env_file=None)

    assert tracing_enabled(settings) is True

    sink = FakeSink()
    tracer = tracer_from_settings(settings, sink=sink, known_names=KNOWN_NAMES)

    assert isinstance(tracer, LangSmithTracer)
    assert tracer.project == settings.langsmith_project


def test_turn_emits_metadata_only_event_without_pii(settings_env):
    settings_env.setenv("APP_ENV", "development")
    settings = Settings(_env_file=None)
    sink = FakeSink()
    tracer = tracer_from_settings(settings, sink=sink, known_names=KNOWN_NAMES)

    deps = _deps(tracer)
    state = run_turn(
        USER_MESSAGE,
        deps=deps,
        thread_id="hilo-traza",
        request_id=REQUEST_ID,
    )

    assert sink.payloads
    payload = sink.payloads[-1]

    assert set(payload) == TRACE_FIELDS
    assert payload["event"] == "turn"
    assert payload["request_id"] == REQUEST_ID
    assert payload["thread_id"] == "hilo-traza"
    assert payload["status"] == state.status.value
    assert payload["tools_used"] == ["resolve_entity", "query_obligations"]
    assert payload["tool_call_count"] == state.tool_call_count
    assert isinstance(payload["latency_ms"], int)
    assert payload["latency_ms"] >= 0
    serialized = _serialize(sink.payloads)
    for leak in (
        USER_MESSAGE,
        "Servicios Andinos",
        "Distribuidora El Sol",
        "J-12345678-9",
        "contacto@andinos.com",
        "0412-1234567",
        "obligaciones: total 6",
        state.answer,
    ):
        assert leak not in serialized


def test_null_tracer_records_nothing():
    tracer = NullTracer()

    tracer.record_turn(TraceEvent(request_id="r", status="ok"))

    assert tracer.events == []


def test_langsmith_tracer_redacts_payload_before_emitting():
    sink = FakeSink()
    tracer = LangSmithTracer(sink=sink, known_names=KNOWN_NAMES)

    tracer.record_turn(
        TraceEvent(
            request_id=REQUEST_ID,
            status="ok",
            tools_used=("query_obligations",),
            tool_call_count=1,
            latency_ms=5,
            thread_id="hilo",
        )
    )

    assert sink.payloads == [
        {
            "event": "turn",
            "request_id": REQUEST_ID,
            "thread_id": "hilo",
            "status": "ok",
            "tools_used": ["query_obligations"],
            "tool_call_count": 1,
            "latency_ms": 5,
        }
    ]


def test_trace_event_payload_only_exposes_metadata():
    event = TraceEvent(
        request_id="r",
        status="partial",
        tools_used=("query_obligations", "search_normativa"),
        tool_call_count=2,
        latency_ms=9,
        thread_id="hilo",
    )

    payload = event.as_payload()

    assert set(payload) == TRACE_FIELDS
    assert payload["tools_used"] == ["query_obligations", "search_normativa"]


def test_trace_event_rejects_free_or_sensitive_fields():
    with pytest.raises(Exception):
        TraceEvent(
            request_id="r",
            status="ok",
            message="obligaciones de Servicios Andinos, S.A.",
        )


def test_hide_content_env_hides_inputs_and_outputs():
    env = hide_content_env()

    assert env["LANGSMITH_HIDE_INPUTS"] == "true"
    assert env["LANGSMITH_HIDE_OUTPUTS"] == "true"


def test_trace_turn_state_ignores_none_tracer():
    state = TurnState(request_id="r", thread_id="hilo", message="hola")

    assert trace_turn_state(None, state, latency_ms=1) is None


def test_trace_turn_state_emits_from_turn_state():
    sink = FakeSink()
    tracer = LangSmithTracer(sink=sink, known_names=KNOWN_NAMES)
    state = TurnState(
        request_id=REQUEST_ID,
        thread_id="hilo",
        message="obligaciones",
        status=Status.ok,
        tools_used=["query_obligations"],
        tool_call_count=1,
    )

    trace_turn_state(tracer, state, latency_ms=3)

    payload = sink.payloads[-1]
    assert payload["request_id"] == REQUEST_ID
    assert payload["status"] == "ok"
    assert payload["tools_used"] == ["query_obligations"]
    assert payload["latency_ms"] == 3
