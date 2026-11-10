"""Tests de API para /digitize_batch (Spec 006, batch de hasta 20 imágenes).

Sin red: NIM vision se mockea igual que en tests/api/test_digitize.py.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import make_fake_invoice_image
from tests.api.test_digitize import make_app_with_odoo_transport

SESSION_HEADER = {"X-Odoo-Session": "valid-session"}

# Delay del lote: se reduce a 0 en los tests de flujo para no dormir de verdad.
ROUTES_MODULE = "contafis_assistant.api.routes"


def _mock_chatopenai(response_content: str):
    """Clase Mock para ChatOpenAI: devuelve ``response_content`` como contenido."""

    class MockChatOpenAI:
        def __init__(self, *args, **kwargs):
            pass

        def invoke(self, messages):
            class MockResponse:
                content = response_content

            return MockResponse()

    return MockChatOpenAI


def _invoice_json(document_type: str = "factura_z", number: str = "001-001-00001234") -> str:
    return json.dumps(
        {
            "document_type": document_type,
            "confidence": 0.9,
            "extracted": {
                "supplier_name": "Distribuidora Andina, C.A.",
                "supplier_rif": "J-31527189-4",
                "invoice_number": number,
                "control_number": "00-000456",
                "invoice_date": "2026-08-15",
                "currency": "VES",
                "subtotal": 1000.0,
                "iva_rate": 0.16,
                "iva_amount": 160.0,
                "total": 1160.0,
                "lines": [],
            },
        },
        ensure_ascii=False,
    )


def _client(settings, monkeypatch, response_content: str | None = None) -> TestClient:
    if response_content is not None:
        monkeypatch.setattr(
            "contafis_assistant.api.digitize.ChatOpenAI",
            _mock_chatopenai(response_content),
        )
    # El lote duerme entre imágenes: en tests se elimina la espera real.
    monkeypatch.setattr(f"{ROUTES_MODULE}.DIGITIZE_BATCH_DELAY_SECONDS", 0)
    return TestClient(make_app_with_odoo_transport(settings))


def _batch_payload(count: int) -> dict:
    return {
        "images": [
            {
                "image_base64": make_fake_invoice_image("factura_z"),
                "filename": f"recibo_{index + 1:02d}.jpg",
                "document_type": "factura_z",
            }
            for index in range(count)
        ]
    }


def test_batch_processes_every_image_in_order(make_settings, monkeypatch):
    settings = make_settings()
    client = _client(settings, monkeypatch, _invoice_json())

    with client:
        response = client.post(
            "/digitize_batch", json=_batch_payload(3), headers=SESSION_HEADER
        )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["succeeded"] == 3
    assert data["failed"] == 0
    assert [item["index"] for item in data["results"]] == [0, 1, 2]
    assert [item["filename"] for item in data["results"]] == [
        "recibo_01.jpg",
        "recibo_02.jpg",
        "recibo_03.jpg",
    ]
    assert all(item["status"] == "ok" for item in data["results"])
    first = data["results"][0]["result"]
    assert first["document_type"] == "factura_z"
    assert first["extracted"]["supplier_rif"] == "J-31527189-4"


def test_batch_waits_between_images_but_not_after_the_last(make_settings, monkeypatch):
    import asyncio

    settings = make_settings()
    client = _client(settings, monkeypatch, _invoice_json())

    slept: list[float] = []
    import contafis_assistant.api.routes as routes

    async def _fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(routes.asyncio, "sleep", _fake_sleep)

    with client:
        response = client.post(
            "/digitize_batch", json=_batch_payload(3), headers=SESSION_HEADER
        )

    assert response.status_code == 200
    assert slept == [routes.DIGITIZE_BATCH_DELAY_SECONDS] * 2


def test_batch_isolates_a_single_failure(make_settings, monkeypatch):
    """Un fallo puntual no invalida el resto del lote."""
    settings = make_settings()

    # Segundo ítem: NIM responde con JSON ilegible -> error aislado.
    calls = {"n": 0}

    class ScriptedLLM:
        def __init__(self, *args, **kwargs):
            pass

        def invoke(self, messages):
            calls["n"] += 1

            class Response:
                content = "no es json {{{" if calls["n"] == 2 else _invoice_json()

            return Response()

    monkeypatch.setattr("contafis_assistant.api.digitize.ChatOpenAI", ScriptedLLM)

    client = _client(settings, monkeypatch)

    with client:
        response = client.post(
            "/digitize_batch", json=_batch_payload(3), headers=SESSION_HEADER
        )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["succeeded"] == 2
    assert data["failed"] == 1
    statuses = [item["status"] for item in data["results"]]
    assert statuses == ["ok", "error", "ok"]
    failed = data["results"][1]
    assert failed["index"] == 1
    assert failed["filename"] == "recibo_02.jpg"
    assert failed["result"] is None
    assert failed["error"]
    assert data["results"][0]["result"]["extracted"]["invoice_number"]


def test_batch_rejects_empty_list(make_settings, monkeypatch):
    client = _client(make_settings(), monkeypatch, _invoice_json())

    with client:
        response = client.post("/digitize_batch", json={"images": []}, headers=SESSION_HEADER)

    assert response.status_code == 422


def test_batch_rejects_more_than_twenty_images(make_settings, monkeypatch):
    client = _client(make_settings(), monkeypatch, _invoice_json())

    with client:
        response = client.post(
            "/digitize_batch", json=_batch_payload(21), headers=SESSION_HEADER
        )

    assert response.status_code == 422


def test_batch_rejects_invalid_base64(make_settings, monkeypatch):
    client = _client(make_settings(), monkeypatch, _invoice_json())
    payload = {
        "images": [
            {
                "image_base64": "!!!no-es-base64!!!",
                "filename": "roto.jpg",
            }
        ]
    }

    with client:
        response = client.post("/digitize_batch", json=payload, headers=SESSION_HEADER)

    assert response.status_code == 422


def test_batch_requires_authentication(make_settings, monkeypatch):
    client = _client(make_settings(), monkeypatch, _invoice_json())

    with client:
        response = client.post("/digitize_batch", json=_batch_payload(1))

    assert response.status_code == 401
