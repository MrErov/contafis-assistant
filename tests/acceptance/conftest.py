"""Fixtures para tests de aceptación: permite loopback para TestClient/anyio."""

from __future__ import annotations

import socket
from datetime import datetime, timezone

import pytest

from contafis_assistant.config import Settings
from contafis_assistant.domain.contracts import ObligationsResult, Warning
from contafis_assistant.domain.enums import (
    CorpusStatus,
    Intent,
    ScopeState,
    Status,
    WarningSource,
)
from contafis_assistant.graph.build import GraphDependencies
from contafis_assistant.graph.classify import ClassificationDraft, ClassificationResult
from contafis_assistant.graph.executor import ToolOutcome
from contafis_assistant.graph.state import Slots, ToolName
from contafis_assistant.memory.threads import ThreadStore
from contafis_assistant.odoo.scope import ScopeReason, ScopeSnapshot
from contafis_assistant.rag.corpus import CorpusState

_ORIGINAL_SOCKET = socket.socket
_ORIGINAL_CREATE_CONNECTION = socket.create_connection


def _is_loopback(address) -> bool:
    if not isinstance(address, tuple) or not address:
        return False
    host = str(address[0]).strip().lower().strip("[]")
    return host in {"127.0.0.1", "::1", "localhost"}


class _LoopbackOnlySocket(_ORIGINAL_SOCKET):
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
    return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Bloquea destinos externos sin romper el self-pipe de asyncio."""
    monkeypatch.setattr(socket, "socket", _LoopbackOnlySocket)
    monkeypatch.setattr(socket, "create_connection", _guarded_create_connection)


@pytest.fixture
def make_settings(monkeypatch):
    """Factory para Settings con variables de test."""
    _REQUIRED_ENV = {
        "ODOO_URL": "http://127.0.0.1:8069",
        "ODOO_API_KEY": "test-odoo-key",
        "OPENAI_API_KEY": "test-openai-key",
        "LLM_MODEL": "gpt-test",
        "EMBEDDING_MODEL": "embedding-test",
        "CHROMA_PATH": "data/test-chroma",
        "APP_ENV": "test",
        "ODOO_READONLY_ATTESTED": "true",
    }
    _ENV_PREFIXES = (
        "ODOO_", "OPENAI_", "LLM_", "EMBEDDING_", "CHROMA_", "BM25_",
        "RETRIEVAL_", "RRF_", "DENSE_", "FUZZY_", "LANGSMITH_", "API_",
        "MAX_", "RECURSION_", "DETAIL_", "MEMORY_", "SCOPE_",
    )
    _ENV_EXACT = {"APP_ENV"}

    def factory(**env: str) -> Settings:
        for key in list(monkeypatch._setenv.keys() if hasattr(monkeypatch, '_setenv') else []):
            if key in _ENV_EXACT or any(key.startswith(p) for p in _ENV_PREFIXES):
                monkeypatch.delenv(key, raising=False)
        for key, value in _REQUIRED_ENV.items():
            monkeypatch.setenv(key, value)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return Settings(_env_file=None)

    return factory


def scope_snapshot(
    *,
    state: ScopeState = ScopeState.scope_ok,
    reason: ScopeReason = ScopeReason.ok,
    app_env: str = "test",
    is_admin: bool = False,
    is_internal: bool = True,
    attested: bool = True,
    user_company_ids: list[int] | None = None,
    active_company_ids: list[int] | None = None,
) -> ScopeSnapshot:
    return ScopeSnapshot(
        state=state,
        reason=reason,
        uid=7,
        user_company_ids=(
            [1] if user_company_ids is None else list(user_company_ids)
        ),
        active_company_ids=(
            [1] if active_company_ids is None else list(active_company_ids)
        ),
        is_admin=is_admin,
        is_internal=is_internal,
        attested=attested,
        app_env=app_env,
        checked_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
    )


def corpus_state(
    *,
    status: CorpusStatus = CorpusStatus.ok,
    version: int | None = 1,
    chroma_status: CorpusStatus | None = None,
    bm25_status: CorpusStatus | None = None,
) -> CorpusState:
    return CorpusState(
        status=status,
        version=version,
        manifest_status=(
            CorpusStatus.ok if status is CorpusStatus.ok else status
        ),
        bm25_status=bm25_status or status,
        chroma_status=chroma_status or status,
        document_count=2,
        collection="contafis_normativa_v1",
    )


class CountingClassifier:
    def __init__(self, intent: Intent = Intent.erp):
        self.intent = intent
        self.messages: list[str] = []

    def __call__(self, message: str) -> ClassificationResult:
        self.messages.append(message)
        return ClassificationResult(
            classification=ClassificationDraft(
                intent=self.intent,
                slots=Slots(topic="obligations"),
            )
        )


class CountingTool:
    def __init__(self, outcome: ToolOutcome | None = None):
        self.outcome = outcome or ToolOutcome(
            status=Status.ok,
            data=[ObligationsResult(total=0, returned=0, truncated=False)],
        )
        self.calls: list = []

    def __call__(self, args) -> ToolOutcome:
        self.calls.append(args)
        return self.outcome


class FakeScope:
    def __init__(self, snapshot: ScopeSnapshot):
        self.snapshot = snapshot
        self.startup_calls = 0
        self.current_calls = 0
        self.closed = False

    def startup(self) -> ScopeSnapshot:
        self.startup_calls += 1
        return self.snapshot

    def current(self) -> ScopeSnapshot:
        self.current_calls += 1
        return self.snapshot

    def close(self) -> None:
        self.closed = True


class FakeCorpus:
    def __init__(self, state: CorpusState):
        self.state = state
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeOdooClient:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


def make_runtime(
    settings: Settings,
    *,
    snapshot: ScopeSnapshot | None = None,
    corpus: FakeCorpus | None = None,
    classifier: CountingClassifier | None = None,
    tool: CountingTool | None = None,
    tools: dict | None = None,
    store: ThreadStore | None = None,
    odoo_client: FakeOdooClient | None = None,
):
    from contafis_assistant.main import ChatRuntime

    snapshot = snapshot or scope_snapshot()
    scope = FakeScope(snapshot=snapshot)
    corpus = corpus or FakeCorpus(state=corpus_state())
    classifier = classifier or CountingClassifier()
    tool = tool or CountingTool()
    store = store or ThreadStore()
    deps = GraphDependencies(
        classifier=classifier,
        tools=tools if tools is not None else {ToolName.query_obligations: tool},
        store=store,
        scope_snapshot=snapshot,
    )
    runtime = ChatRuntime(
        settings=settings,
        store=store,
        deps=deps,
        scope=scope,
        corpus=corpus,
        odoo_client=odoo_client,
    )
    return runtime, scope, corpus, classifier, tool