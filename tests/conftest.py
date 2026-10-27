"""Fixtures base de la suite: entorno determinista, reloj fijo y sin red."""

from __future__ import annotations

import os
import socket
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_REQUIRED_ENV = {
    "ODOO_URL": "http://127.0.0.1:8069",
    "ODOO_API_KEY": "test-odoo-key",
    "OPENAI_API_KEY": "test-openai-key",
    "LLM_MODEL": "gpt-test",
    "EMBEDDING_MODEL": "embedding-test",
    "CHROMA_PATH": "data/test-chroma",
    "APP_ENV": "test",
}

_ENV_PREFIXES = (
    "ODOO_",
    "OPENAI_",
    "LLM_",
    "EMBEDDING_",
    "CHROMA_",
    "BM25_",
    "RETRIEVAL_",
    "RRF_",
    "DENSE_",
    "FUZZY_",
    "LANGSMITH_",
    "API_",
    "MAX_",
    "RECURSION_",
    "DETAIL_",
    "MEMORY_",
    "SCOPE_",
)
_ENV_EXACT = {"APP_ENV"}


def _is_loopback(address):
    """Verifica si la dirección es loopback (127.0.0.1, ::1, localhost)."""
    if not isinstance(address, tuple) or not address:
        return False
    host = str(address[0]).strip().lower().strip("[]")
    return host in {"127.0.0.1", "::1", "localhost"}


class _LoopbackOnlySocket(socket.socket):
    """Socket que solo permite conexiones loopback."""
    def connect(self, address):
        if not _is_loopback(address):
            raise AssertionError("Network access is blocked in tests")
        super().connect(address)

    def connect_ex(self, address):
        if not _is_loopback(address):
            raise AssertionError("Network access is blocked in tests")
        return super().connect_ex(address)

    def bind(self, address):
        if not _is_loopback(address):
            raise AssertionError("Network access is blocked in tests")
        super().bind(address)


def _guarded_create_connection(address, *args, **kwargs):
    if not _is_loopback(address):
        raise AssertionError("Network access is blocked in tests")
    return socket.create_connection(address, *args, **kwargs)


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Bloquea destinos externos sin romper el self-pipe de asyncio (socketpair)."""
    monkeypatch.setattr(socket, "socket", _LoopbackOnlySocket)
    monkeypatch.setattr(socket, "create_connection", _guarded_create_connection)


@pytest.fixture(autouse=True)
def utc_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(os, "tzset"):
        os.tzset()


@pytest.fixture
def settings_env(monkeypatch):
    """Limpia las variables de RNF-003 y deja solo las requeridas de test."""
    for key in list(os.environ):
        if key in _ENV_EXACT or key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


@pytest.fixture
def fixed_now():
    return datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def fixed_today():
    return date(2026, 1, 15)
