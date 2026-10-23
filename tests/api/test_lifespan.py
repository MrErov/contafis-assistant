"""Lifespan: wiring único, cierre limpio y compuerta de inicialización (T08)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from contafis_assistant import main
from contafis_assistant.graph.build import GraphDependencies
from contafis_assistant.main import create_app
from contafis_assistant.memory.threads import ThreadStore

from .conftest import FakeOdooClient, make_runtime


def test_lifespan_wires_runtime_and_closes_resources(make_settings):
    settings = make_settings()
    store = ThreadStore()
    odoo = FakeOdooClient()
    created: list[object] = []

    def factory(resolved_settings):
        runtime, _scope, _corpus, _classifier, _tool = make_runtime(
            resolved_settings,
            store=store,
            odoo_client=odoo,
        )
        created.append(runtime)
        return runtime

    app = create_app(settings, runtime_factory=factory)

    with TestClient(app) as client:
        runtime = app.state.runtime
        assert runtime is created[0]
        assert runtime.ready is True
        assert runtime.settings is settings
        assert runtime.store is store
        assert isinstance(runtime.deps, GraphDependencies)
        assert runtime.deps.max_tool_calls == settings.max_tool_calls
        assert runtime.deps.store is store
        assert runtime.scope.startup_calls == 1
        assert runtime.corpus.closed is False
        assert client.get("/health").status_code == 200

    assert runtime.ready is False
    assert runtime.scope.closed is True
    assert runtime.corpus.closed is True
    assert odoo.closed is True


def test_lifespan_does_not_create_additional_workers(make_settings):
    settings = make_settings()
    runtime, _scope, _corpus, _classifier, _tool = make_runtime(settings)

    assert main.UVICORN_WORKERS == 1
    assert runtime.worker_count == 1

    with TestClient(create_app(settings, runtime=runtime)):
        assert runtime.worker_count == 1


def test_requests_before_initialization_are_rejected_with_503(make_settings):
    settings = make_settings()
    runtime, _scope, _corpus, classifier, tool = make_runtime(settings)
    app = create_app(settings, runtime=runtime)
    client = TestClient(app)

    health = client.get("/health")
    chat = client.post("/chat", json={"message": "obligaciones"})

    assert health.status_code == 503
    assert chat.status_code == 503
    assert runtime.ready is False
    assert classifier.calls == []
    assert tool.calls == []
