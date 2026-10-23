"""Historial de hilos en memoria con lock, LRU, topes y warnings."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.contracts import EntityRef, Warning
from contafis_assistant.domain.enums import WarningCode, WarningSource

MEMORY_MAX_THREADS = 500
MEMORY_MAX_TURNS = 20

Topic = Literal["obligations", "clients", "documents", "alerts", "companies"]
ObligationStateValue = Literal["pending", "overdue", "paid", "cancelled"]

WARNING_MESSAGES = {
    WarningCode.thread_evicted: "El hilo anterior expiró; se inició uno nuevo.",
    WarningCode.thread_context_unavailable: (
        "El contexto del hilo no está disponible; se inició uno nuevo."
    ),
    WarningCode.thread_history_truncated: (
        "El historial del hilo se recortó al límite configurado."
    ),
}


def _warning(code: WarningCode) -> Warning:
    return Warning(
        code=code,
        message=WARNING_MESSAGES[code],
        source=WarningSource.system,
    )


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date | None = None
    end: date | None = None


class ThreadFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: int | None = Field(default=None, ge=1)
    client_id: int | None = Field(default=None, ge=1)
    tax_type: str | None = None
    states: list[ObligationStateValue] = Field(default_factory=list)
    topic: Topic | None = None
    document_type_id: int | None = Field(default=None, ge=1)
    offset: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=1)


class ThreadTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: EntityRef | None = None
    filters: ThreadFilters = Field(default_factory=ThreadFilters)
    period: TimeRange | None = None


class ThreadResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    history: list[ThreadTurn] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
    is_new: bool = False

    @property
    def last_entity(self) -> EntityRef | None:
        if not self.history:
            return None
        return self.history[-1].entity


def _is_uuid4(value: str) -> bool:
    try:
        return uuid.UUID(value).version == 4
    except (ValueError, AttributeError):
        return False


class ThreadStore:
    """Almacén in-process de hilos con LRU y set acotado de expulsados."""

    def __init__(
        self,
        *,
        max_threads: int = MEMORY_MAX_THREADS,
        max_turns: int = MEMORY_MAX_TURNS,
        evicted_limit: int = 1000,
    ) -> None:
        if max_threads < 1:
            raise ValueError("max_threads debe ser al menos 1")
        if max_turns < 1:
            raise ValueError("max_turns debe ser al menos 1")
        if evicted_limit < 1:
            raise ValueError("evicted_limit debe ser al menos 1")
        self._max_threads = max_threads
        self._max_turns = max_turns
        self._evicted_limit = evicted_limit
        self._lock = threading.RLock()
        self._threads: OrderedDict[str, list[ThreadTurn]] = OrderedDict()
        self._evicted: OrderedDict[str, None] = OrderedDict()

    @property
    def max_threads(self) -> int:
        return self._max_threads

    @property
    def max_turns(self) -> int:
        return self._max_turns

    @property
    def thread_count(self) -> int:
        with self._lock:
            return len(self._threads)

    def resolve(self, thread_id: str | None = None) -> ThreadResolution:
        with self._lock:
            if thread_id is None or not str(thread_id).strip():
                new_id = str(uuid.uuid4())
                self._create_locked(new_id)
                return self._resolution_locked(new_id, is_new=True)
            return self._resolve_locked(str(thread_id))

    def append_turn(
        self,
        thread_id: str,
        turn: ThreadTurn,
    ) -> ThreadResolution:
        with self._lock:
            if thread_id not in self._threads:
                resolution = self._resolve_locked(thread_id)
                warnings = list(resolution.warnings)
                is_new = True
            else:
                warnings = []
                is_new = False
                self._threads.move_to_end(thread_id)

            history = self._threads[thread_id]
            history.append(turn)
            if len(history) > self._max_turns:
                excess = len(history) - self._max_turns
                del history[:excess]
                if not any(
                    warning.code == WarningCode.thread_history_truncated
                    for warning in warnings
                ):
                    warnings.append(
                        _warning(WarningCode.thread_history_truncated)
                    )

            resolution = self._resolution_locked(thread_id, is_new=is_new)
            return resolution.model_copy(update={"warnings": warnings})

    def get(self, thread_id: str) -> ThreadResolution | None:
        with self._lock:
            if thread_id not in self._threads:
                return None
            self._threads.move_to_end(thread_id)
            return self._resolution_locked(thread_id, is_new=False)

    def _resolve_locked(self, thread_id: str) -> ThreadResolution:
        if thread_id in self._threads:
            self._threads.move_to_end(thread_id)
            return self._resolution_locked(thread_id, is_new=False)

        warnings: list[Warning] = []
        if thread_id in self._evicted:
            del self._evicted[thread_id]
            warnings.append(_warning(WarningCode.thread_evicted))
        elif _is_uuid4(thread_id):
            warnings.append(_warning(WarningCode.thread_context_unavailable))

        self._create_locked(thread_id)
        resolution = self._resolution_locked(thread_id, is_new=True)
        return resolution.model_copy(update={"warnings": warnings})

    def _create_locked(self, thread_id: str) -> None:
        while len(self._threads) >= self._max_threads:
            evicted_id, _ = self._threads.popitem(last=False)
            self._remember_evicted_locked(evicted_id)
        self._threads[thread_id] = []

    def _remember_evicted_locked(self, thread_id: str) -> None:
        self._evicted[thread_id] = None
        self._evicted.move_to_end(thread_id)
        while len(self._evicted) > self._evicted_limit:
            self._evicted.popitem(last=False)

    def _resolution_locked(
        self,
        thread_id: str,
        *,
        is_new: bool,
    ) -> ThreadResolution:
        return ThreadResolution(
            thread_id=thread_id,
            history=list(self._threads[thread_id]),
            is_new=is_new,
        )
