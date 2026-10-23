"""Health agregado, degradado por corpus y 503 por refresh unsafe (T08)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from contafis_assistant import __version__
from contafis_assistant.domain.enums import CorpusStatus, ScopeState
from contafis_assistant.main import create_app
from contafis_assistant.odoo.scope import ScopeReason

from .conftest import (
    FakeCorpus,
    corpus_state,
    make_runtime,
    scope_snapshot,
    warning_codes,
)


def test_health_reports_aggregated_state_without_secrets_or_pii(make_settings):
    settings = make_settings(
        API_SHARED_SECRET="api-super-secret",
        ODOO_API_KEY="odoo-super-secret",
    )
    snapshot = scope_snapshot(
        user_company_ids=[987654321],
        active_company_ids=[987654321],
    )
    runtime, _scope, _corpus, _classifier, _tool = make_runtime(
        settings,
        snapshot=snapshot,
        corpus=FakeCorpus(corpus_state(version=3)),
    )

    with TestClient(create_app(settings, runtime=runtime)) as client:
        response = client.get(
            "/health", headers={"X-API-Key": "api-super-secret"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "app_version": __version__,
        "scope_state": "scope_ok",
        "scope_complete": True,
        "corpus_status": "ok",
        "corpus_version": 3,
        "chroma_available": True,
        "bm25_available": True,
        "odoo_available": True,
        "worker_count": 1,
    }

    raw = response.text
    for forbidden in (
        "api-super-secret",
        "odoo-super-secret",
        "987654321",
        "RIF",
        "Compañía Secreta",
    ):
        assert forbidden not in raw


def test_scope_partial_starts_and_responds_partial_never_503(make_settings):
    settings = make_settings()
    partial = scope_snapshot(
        state=ScopeState.scope_partial,
        reason=ScopeReason.incomplete_coverage,
        user_company_ids=[1],
        active_company_ids=[1, 2],
    )
    runtime, _scope, _corpus, _classifier, _tool = make_runtime(
        settings, snapshot=partial
    )

    with TestClient(create_app(settings, runtime=runtime)) as client:
        health = client.get("/health")
        chat = client.post("/chat", json={"message": "obligaciones"})

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["scope_state"] == "scope_partial"
    assert health.json()["scope_complete"] is False

    assert chat.status_code == 200
    assert chat.json()["status"] == "partial"
    assert "scope_partial" in warning_codes(chat.json())


def test_production_refresh_unsafe_returns_503_and_does_not_run_tools(
    make_settings,
):
    settings = make_settings(APP_ENV="production")
    safe = scope_snapshot(app_env="production")
    runtime, scope, _corpus, classifier, tool = make_runtime(
        settings, snapshot=safe
    )

    with TestClient(create_app(settings, runtime=runtime)) as client:
        assert client.get("/health").status_code == 200

        scope.snapshot = scope_snapshot(
            state=ScopeState.scope_unsafe,
            reason=ScopeReason.admin,
            app_env="production",
            is_admin=True,
        )
        classifier_calls = len(classifier.calls)
        tool_calls = len(tool.calls)

        health = client.get("/health")
        chat = client.post("/chat", json={"message": "obligaciones"})

    assert health.status_code == 503
    assert health.json()["status"] == "unhealthy"
    assert chat.status_code == 503
    assert len(classifier.calls) == classifier_calls
    assert len(tool.calls) == tool_calls


def test_invalid_corpus_degrades_health_without_crashing(make_settings):
    settings = make_settings()
    broken = FakeCorpus(
        corpus_state(
            status=CorpusStatus.mismatch,
            version=9,
            chroma_status=CorpusStatus.missing,
            bm25_status=CorpusStatus.mismatch,
        )
    )
    runtime, _scope, _corpus, _classifier, _tool = make_runtime(
        settings, corpus=broken
    )

    with TestClient(create_app(settings, runtime=runtime)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["corpus_status"] == "mismatch"
    assert body["corpus_version"] == 9
    assert body["chroma_available"] is False
    assert body["bm25_available"] is False
    assert body["scope_state"] == "scope_ok"


def test_odoo_unavailable_is_reported_without_leaking_details(make_settings):
    settings = make_settings()
    broken_scope = scope_snapshot(
        state=ScopeState.scope_unsafe,
        reason=ScopeReason.odoo_error,
        is_internal=False,
        attested=False,
    )
    runtime, _scope, _corpus, _classifier, _tool = make_runtime(
        settings, snapshot=broken_scope
    )

    with TestClient(create_app(settings, runtime=runtime)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["odoo_available"] is False
    assert "error" not in response.text.lower()
