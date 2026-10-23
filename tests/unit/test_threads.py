from __future__ import annotations

import threading
import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from contafis_assistant.domain.contracts import EntityRef
from contafis_assistant.domain.enums import EntityType, WarningCode
from contafis_assistant.memory.threads import (
    ThreadFilters,
    ThreadStore,
    ThreadTurn,
    TimeRange,
)


def _entity(name: str = "Acme", identifier: int = 1) -> EntityRef:
    return EntityRef(id=identifier, name=name, type=EntityType.company)


def _codes(warnings) -> set[WarningCode]:
    return {warning.code for warning in warnings}


def test_missing_thread_id_generates_uuid4_without_warning():
    store = ThreadStore()

    resolution = store.resolve(None)

    assert uuid.UUID(resolution.thread_id).version == 4
    assert resolution.is_new is True
    assert resolution.warnings == []
    assert resolution.history == []


def test_existing_thread_is_resumed_with_history():
    store = ThreadStore()
    first = store.resolve("hilo-uno")
    store.append_turn(
        first.thread_id,
        ThreadTurn(entity=_entity(), filters=ThreadFilters(tax_type="iva")),
    )

    resumed = store.resolve("hilo-uno")

    assert resumed.is_new is False
    assert resumed.thread_id == "hilo-uno"
    assert len(resumed.history) == 1
    assert resumed.history[0].entity == _entity()
    assert resumed.last_entity == _entity()


def test_lru_eviction_warns_thread_evicted():
    store = ThreadStore(max_threads=2)
    store.resolve("a")
    store.resolve("b")
    store.resolve("a")
    store.resolve("c")

    evicted = store.resolve("b")

    assert evicted.is_new is True
    assert WarningCode.thread_evicted in _codes(evicted.warnings)
    assert evicted.history == []


def test_unknown_uuid4_warns_thread_context_unavailable():
    store = ThreadStore()
    unknown = str(uuid.uuid4())

    resolution = store.resolve(unknown)

    assert resolution.thread_id == unknown
    assert resolution.is_new is True
    assert WarningCode.thread_context_unavailable in _codes(resolution.warnings)


def test_unknown_non_uuid4_starts_without_warning():
    store = ThreadStore()

    resolution = store.resolve("cliente-nuevo")

    assert resolution.thread_id == "cliente-nuevo"
    assert resolution.is_new is True
    assert resolution.warnings == []


def test_max_turns_truncates_history_with_warning():
    store = ThreadStore(max_turns=2)
    store.resolve("tope")

    store.append_turn("tope", ThreadTurn(filters=ThreadFilters(tax_type="t0")))
    store.append_turn("tope", ThreadTurn(filters=ThreadFilters(tax_type="t1")))
    truncated = store.append_turn(
        "tope", ThreadTurn(filters=ThreadFilters(tax_type="t2"))
    )

    assert WarningCode.thread_history_truncated in _codes(truncated.warnings)
    assert len(truncated.history) == 2
    assert [turn.filters.tax_type for turn in truncated.history] == ["t1", "t2"]


def test_two_concurrent_threads_do_not_leak_history():
    store = ThreadStore(max_turns=1000)

    def worker(prefix: str) -> None:
        store.resolve(prefix)
        for index in range(100):
            store.append_turn(
                prefix,
                ThreadTurn(entity=_entity(f"{prefix}-{index}", index + 1)),
            )

    first = threading.Thread(target=worker, args=("uno",))
    second = threading.Thread(target=worker, args=("dos",))
    first.start()
    second.start()
    first.join()
    second.join()

    one = store.resolve("uno")
    two = store.resolve("dos")

    assert len(one.history) == 100
    assert len(two.history) == 100
    assert all(turn.entity.name.startswith("uno-") for turn in one.history)
    assert all(turn.entity.name.startswith("dos-") for turn in two.history)


def test_history_only_keeps_entity_filters_and_period():
    store = ThreadStore()
    store.resolve("limpio")
    store.append_turn(
        "limpio",
        ThreadTurn(
            entity=_entity(),
            filters=ThreadFilters(tax_type="iva", states=["overdue"]),
            period=TimeRange(start=date(2026, 1, 1), end=date(2026, 1, 31)),
        ),
    )

    payload = store.resolve("limpio").history[0].model_dump(mode="json")

    assert set(payload) == {"entity", "filters", "period"}
    assert "data" not in payload
    assert "sources" not in payload

    with pytest.raises(ValidationError):
        ThreadTurn.model_validate(
            {"entity": _entity().model_dump(), "data": [], "sources": []}
        )
