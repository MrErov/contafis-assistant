"""Fixtures compartidas de la API (T08).

El bloqueo de red global impide crear sockets porque el event loop de Windows
usa ``socketpair`` para su self-pipe. En esta capa se permite solo loopback,
que es lo que usa TestClient/anyio, y se siguen bloqueando destinos externos.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

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


def _is_loopback(address: Any) -> bool:
    if not isinstance(address, tuple) or not address:
        return False
    host = str(address[0]).strip().lower().strip("[]")
    return host in {"127.0.0.1", "::1", "localhost"}


class _LoopbackOnlySocket(_ORIGINAL_SOCKET):
    def connect(self, address: Any) -> None:
        if not _is_loopback(address):
            raise AssertionError("Network access is blocked in tests")
        super().connect(address)

    def connect_ex(self, address: Any) -> int:
        if not _is_loopback(address):
            raise AssertionError("Network access is blocked in tests")
        return super().connect_ex(address)

    def bind(self, address: Any) -> None:
        if not _is_loopback(address):
            raise AssertionError("Network access is blocked in tests")
        super().bind(address)


def _guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
    if not _is_loopback(address):
        raise AssertionError("Network access is blocked in tests")
    return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Bloquea destinos externos sin romper el self-pipe de asyncio."""
    monkeypatch.setattr(socket, "socket", _LoopbackOnlySocket)
    monkeypatch.setattr(socket, "create_connection", _guarded_create_connection)


@pytest.fixture
def make_settings(settings_env):
    def factory(**env: str) -> Settings:
        for key, value in env.items():
            settings_env.setenv(key, value)
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
        app_env=app_env,  # type: ignore[arg-type]
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


@dataclass
class FakeScope:
    snapshot: ScopeSnapshot
    startup_calls: int = 0
    current_calls: int = 0
    closed: bool = False

    def startup(self) -> ScopeSnapshot:
        self.startup_calls += 1
        return self.snapshot

    def current(self) -> ScopeSnapshot:
        self.current_calls += 1
        return self.snapshot

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeCorpus:
    state: CorpusState = field(default_factory=corpus_state)
    closed: bool = False

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeOdooClient:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class CountingClassifier:
    def __init__(self, intent: Intent = Intent.erp) -> None:
        self.intent = intent
        self.calls: list[str] = []

    def __call__(self, message: str) -> ClassificationResult:
        self.calls.append(message)
        return ClassificationResult(
            classification=ClassificationDraft(
                intent=self.intent,
                slots=Slots(topic="obligations"),
            )
        )


class CountingTool:
    def __init__(self, outcome: ToolOutcome | None = None) -> None:
        self.outcome = outcome or ToolOutcome(
            status=Status.ok,
            data=[ObligationsResult(total=0, returned=0, truncated=False)],
        )
        self.calls: list[Any] = []

    def __call__(self, args: Any) -> ToolOutcome:
        self.calls.append(args)
        return self.outcome


def warning_codes(response: Any) -> set[str]:
    return {
        warning["code"] if isinstance(warning, dict) else warning.code
        for warning in response.get("warnings", [])
    }


def make_runtime(
    settings: Settings,
    *,
    snapshot: ScopeSnapshot | None = None,
    corpus: FakeCorpus | None = None,
    classifier: CountingClassifier | None = None,
    tool: CountingTool | None = None,
    tools: dict[Any, Any] | None = None,
    store: ThreadStore | None = None,
    odoo_client: FakeOdooClient | None = None,
):
    from contafis_assistant.main import ChatRuntime

    snapshot = snapshot or scope_snapshot()
    scope = FakeScope(snapshot=snapshot)
    corpus = corpus or FakeCorpus()
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


def scope_warning(code: str, message: str = "warning") -> Warning:
    return Warning(
        code=code,  # type: ignore[arg-type]
        message=message,
        source=WarningSource.system,
    )
