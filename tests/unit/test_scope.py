"""Compuerta de alcance: clasificación, degradación, TTL y lock (T02)."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from contafis_assistant.domain.enums import ScopeState, WarningCode
from contafis_assistant.odoo.allowlist import ALLOWED_GROUP_EXT_IDS, RpcCall
from contafis_assistant.odoo.errors import OdooTransientError
from contafis_assistant.odoo.scope import (
    ScopeReason,
    ScopeService,
    ScopeStartupError,
    classify_scope,
)

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
UID = 7


def groups(*, internal=True, system=False, erp=False):
    return {
        "base.group_user": internal,
        "base.group_system": system,
        "base.group_erp_manager": erp,
    }


DEFAULT_GROUPS = groups()


class FakeClock:
    def __init__(self, now=NOW):
        self._now = now

    def now(self):
        return self._now

    def advance(self, seconds):
        self._now = self._now + timedelta(seconds=seconds)


class FakeOdooClient:
    """Doble in-process; no hay HTTP ni red."""

    def __init__(
        self,
        *,
        context=None,
        groups=None,
        companies=None,
        company_ids=None,
        fail=None,
        context_delay=0.0,
    ):
        self.uid = None
        self.calls = []
        self.allowed_company_ids = []
        self.company_ids = list(company_ids) if company_ids is not None else [10, 20]
        self.context = (
            dict(context)
            if context is not None
            else {"uid": UID, "allowed_company_ids": [10, 20]}
        )
        self.groups = dict(groups) if groups is not None else dict(DEFAULT_GROUPS)
        self.companies = (
            list(companies)
            if companies is not None
            else [{"id": 10, "name": "A"}, {"id": 20, "name": "B"}]
        )
        self.fail = fail
        self.context_delay = context_delay

    def set_allowed_company_ids(self, ids):
        self.allowed_company_ids = list(ids)

    def execute(self, call, *, budget=None, previous_truncated=None):
        self.calls.append(call)
        if self.fail is not None:
            raise self.fail
        if call.method == "context_get":
            if self.context_delay:
                threading.Event().wait(self.context_delay)
            return dict(self.context)
        if call.method == "read":
            return [{"id": call.ids[0], "company_ids": list(self.company_ids)}]
        if call.method == "has_group":
            return bool(self.groups.get(call.group_ext_id, False))
        if call.model == "res.company" and call.method == "search_read":
            return list(self.companies)
        raise AssertionError(f"llamada inesperada: {call}")


def make_service(
    client,
    *,
    env="test",
    attested=True,
    ttl_seconds=300,
    now=NOW,
):
    clock = FakeClock(now)
    service = ScopeService(
        client,
        app_env=env,
        attested=attested,
        ttl_seconds=ttl_seconds,
        clock=clock,
    )
    return service, clock


def context_get_count(client):
    return sum(1 for call in client.calls if call.method == "context_get")


# ---------------------------------------------------------------------------
# Clasificación pura
# ---------------------------------------------------------------------------


def test_scope_ok_requires_internal_non_admin_attested_full_coverage():
    snapshot = classify_scope(
        is_admin=False,
        is_internal=True,
        attested=True,
        user_company_ids=[10, 20],
        active_company_ids=[10, 20],
        app_env="test",
        checked_at=NOW,
        uid=UID,
    )

    assert snapshot.state is ScopeState.scope_ok
    assert snapshot.reason is ScopeReason.ok
    assert snapshot.scope_complete is True
    assert snapshot.tools_enabled is True
    assert snapshot.healthy is True
    assert snapshot.warnings == []


def test_scope_partial_when_active_companies_are_not_covered():
    snapshot = classify_scope(
        is_admin=False,
        is_internal=True,
        attested=True,
        user_company_ids=[10],
        active_company_ids=[10, 20],
        app_env="test",
        checked_at=NOW,
        uid=UID,
    )

    assert snapshot.state is ScopeState.scope_partial
    assert snapshot.reason is ScopeReason.incomplete_coverage
    assert snapshot.scope_complete is False
    assert snapshot.tools_enabled is True
    assert snapshot.healthy is True
    assert snapshot.warnings == [WarningCode.scope_partial]


def test_scope_partial_when_there_are_no_active_companies():
    snapshot = classify_scope(
        is_admin=False,
        is_internal=True,
        attested=True,
        user_company_ids=[],
        active_company_ids=[],
        app_env="test",
        checked_at=NOW,
        uid=UID,
    )

    assert snapshot.state is ScopeState.scope_partial
    assert snapshot.reason is ScopeReason.no_active_companies
    assert snapshot.scope_complete is False
    assert snapshot.warnings == [WarningCode.scope_partial]


def test_scope_unsafe_for_admin_in_dev_test_allows_degraded_tools():
    snapshot = classify_scope(
        is_admin=True,
        is_internal=True,
        attested=True,
        user_company_ids=[10, 20],
        active_company_ids=[10, 20],
        app_env="test",
        checked_at=NOW,
        uid=UID,
    )

    assert snapshot.state is ScopeState.scope_unsafe
    assert snapshot.reason is ScopeReason.admin
    assert snapshot.scope_complete is False
    assert snapshot.tools_enabled is True
    assert snapshot.healthy is True
    assert snapshot.admin_key_warning is True
    assert snapshot.warnings == [WarningCode.scope_admin_key]


def test_scope_unsafe_for_false_or_revoked_attestation():
    snapshot = classify_scope(
        is_admin=False,
        is_internal=True,
        attested=False,
        user_company_ids=[10, 20],
        active_company_ids=[10, 20],
        app_env="development",
        checked_at=NOW,
        uid=UID,
    )

    assert snapshot.state is ScopeState.scope_unsafe
    assert snapshot.reason is ScopeReason.attestation
    assert snapshot.tools_enabled is True
    assert snapshot.warnings == [WarningCode.scope_admin_key]


def test_scope_unsafe_for_not_internal_disables_tools_without_admin_key():
    snapshot = classify_scope(
        is_admin=False,
        is_internal=False,
        attested=True,
        user_company_ids=[10, 20],
        active_company_ids=[10, 20],
        app_env="test",
        checked_at=NOW,
        uid=UID,
    )

    assert snapshot.state is ScopeState.scope_unsafe
    assert snapshot.reason is ScopeReason.not_internal
    assert snapshot.scope_complete is False
    assert snapshot.tools_enabled is False
    assert snapshot.admin_key_warning is False
    assert snapshot.warnings == []


def test_not_internal_wins_over_admin_and_never_emits_admin_key():
    snapshot = classify_scope(
        is_admin=True,
        is_internal=False,
        attested=True,
        user_company_ids=[10, 20],
        active_company_ids=[10, 20],
        app_env="development",
        checked_at=NOW,
        uid=UID,
    )

    assert snapshot.state is ScopeState.scope_unsafe
    assert snapshot.reason is ScopeReason.not_internal
    assert snapshot.admin_key_warning is False
    assert snapshot.warnings == []


def test_production_unsafe_is_unhealthy_and_disables_tools():
    snapshot = classify_scope(
        is_admin=True,
        is_internal=True,
        attested=True,
        user_company_ids=[10, 20],
        active_company_ids=[10, 20],
        app_env="production",
        checked_at=NOW,
        uid=UID,
    )

    assert snapshot.state is ScopeState.scope_unsafe
    assert snapshot.healthy is False
    assert snapshot.tools_enabled is False
    assert snapshot.warnings == [WarningCode.scope_admin_key]


def test_production_partial_is_healthy_and_enabled():
    snapshot = classify_scope(
        is_admin=False,
        is_internal=True,
        attested=True,
        user_company_ids=[10],
        active_company_ids=[10, 20],
        app_env="production",
        checked_at=NOW,
        uid=UID,
    )

    assert snapshot.state is ScopeState.scope_partial
    assert snapshot.healthy is True
    assert snapshot.tools_enabled is True
    assert snapshot.scope_complete is False


# ---------------------------------------------------------------------------
# ScopeService con cliente fake
# ---------------------------------------------------------------------------


def test_startup_ok_runs_the_expected_scope_rpcs_sequentially():
    client = FakeOdooClient()
    service, _ = make_service(client)

    snapshot = service.startup()

    assert snapshot.state is ScopeState.scope_ok
    assert snapshot.scope_complete is True
    assert client.uid == UID
    assert client.allowed_company_ids == [10, 20]

    assert client.calls[0].method == "context_get"
    has_group_calls = [call for call in client.calls if call.method == "has_group"]
    assert {call.group_ext_id for call in has_group_calls} == ALLOWED_GROUP_EXT_IDS
    assert all(call.ids == [UID] for call in has_group_calls)
    assert len(has_group_calls) == 3

    company_calls = [
        call
        for call in client.calls
        if call.model == "res.company" and call.method == "search_read"
    ]
    assert len(company_calls) == 1
    assert company_calls[0].domain == [("active", "=", True)]
    assert all(call.model != "ir.model.data" for call in client.calls)


def test_startup_dev_admin_degrades_without_raising():
    client = FakeOdooClient(groups=groups(system=True))
    service, _ = make_service(client, env="test")

    snapshot = service.startup()

    assert snapshot.state is ScopeState.scope_unsafe
    assert snapshot.tools_enabled is True
    assert snapshot.warnings == [WarningCode.scope_admin_key]


def test_startup_dev_false_attestation_degrades_without_raising():
    client = FakeOdooClient()
    service, _ = make_service(client, env="development", attested=False)

    snapshot = service.startup()

    assert snapshot.state is ScopeState.scope_unsafe
    assert snapshot.reason is ScopeReason.attestation
    assert snapshot.warnings == [WarningCode.scope_admin_key]


def test_startup_dev_not_internal_degrades_disabling_tools():
    client = FakeOdooClient(groups=groups(internal=False))
    service, _ = make_service(client, env="test")

    snapshot = service.startup()

    assert snapshot.state is ScopeState.scope_unsafe
    assert snapshot.reason is ScopeReason.not_internal
    assert snapshot.tools_enabled is False
    assert snapshot.warnings == []


@pytest.mark.parametrize(
    "client_kwargs,attested",
    [
        ({"groups": groups(system=True)}, True),
        ({"groups": groups(erp=True)}, True),
        ({}, False),
        ({"groups": groups(internal=False)}, True),
    ],
)
def test_production_unsafe_at_startup_fails(client_kwargs, attested):
    client = FakeOdooClient(**client_kwargs)
    service, _ = make_service(client, env="production", attested=attested)

    with pytest.raises(ScopeStartupError):
        service.startup()


def test_production_partial_at_startup_does_not_fail():
    client = FakeOdooClient(context={"uid": UID, "allowed_company_ids": [10]})
    service, _ = make_service(client, env="production", attested=True)

    snapshot = service.startup()

    assert snapshot.state is ScopeState.scope_partial
    assert snapshot.scope_complete is False
    assert snapshot.warnings == [WarningCode.scope_partial]


def test_refresh_to_unsafe_in_production_is_unhealthy_without_raising():
    client = FakeOdooClient()
    service, _ = make_service(client, env="production", attested=True)

    first = service.startup()
    assert first.state is ScopeState.scope_ok

    client.groups = groups(system=True)
    second = service.refresh(force=True)

    assert second.state is ScopeState.scope_unsafe
    assert second.healthy is False
    assert second.tools_enabled is False
    assert second.warnings == [WarningCode.scope_admin_key]


def test_scope_uses_allowed_company_ids_from_context_get():
    client = FakeOdooClient(context={"uid": UID, "allowed_company_ids": [10]})
    service, _ = make_service(client)

    snapshot = service.startup()

    assert snapshot.state is ScopeState.scope_partial
    assert snapshot.reason is ScopeReason.incomplete_coverage
    assert client.allowed_company_ids == [10]


def test_scope_falls_back_to_user_company_ids_when_context_has_none():
    client = FakeOdooClient(context={"uid": UID})
    service, _ = make_service(client)

    snapshot = service.startup()

    read_calls = [call for call in client.calls if call.method == "read"]
    assert len(read_calls) == 1
    assert read_calls[0].model == "res.users"
    assert read_calls[0].ids == [UID]
    assert read_calls[0].fields == ["company_ids"]
    assert snapshot.state is ScopeState.scope_ok
    assert client.allowed_company_ids == [10, 20]


def test_ttl_reuses_snapshot_until_expiry():
    client = FakeOdooClient()
    service, clock = make_service(client, ttl_seconds=300)

    service.current()
    service.current()
    assert context_get_count(client) == 1

    clock.advance(299)
    service.current()
    assert context_get_count(client) == 1

    clock.advance(2)
    service.current()
    assert context_get_count(client) == 2


def test_lock_serializes_concurrent_refreshes():
    client = FakeOdooClient(context_delay=0.05)
    service, _ = make_service(client)
    barrier = threading.Barrier(2)
    results = []

    def worker():
        barrier.wait(timeout=2)
        results.append(service.current())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert all(snapshot.state is ScopeState.scope_ok for snapshot in results)
    assert context_get_count(client) == 1


def test_odoo_error_during_scope_check_fails_closed():
    client = FakeOdooClient(fail=OdooTransientError("odoo down"))
    service, _ = make_service(client, env="test", attested=True)

    snapshot = service.refresh(force=True)

    assert snapshot.state is ScopeState.scope_unsafe
    assert snapshot.reason is ScopeReason.odoo_error
    assert snapshot.tools_enabled is False
    assert snapshot.scope_complete is False


def test_odoo_error_at_production_startup_fails():
    client = FakeOdooClient(fail=OdooTransientError("odoo down"))
    service, _ = make_service(client, env="production", attested=True)

    with pytest.raises(ScopeStartupError):
        service.startup()


def test_scope_only_calls_allowlisted_read_methods():
    client = FakeOdooClient()
    service, _ = make_service(client)

    service.startup()

    allowed_methods = {
        "context_get",
        "read",
        "has_group",
        "search_read",
        "search_count",
    }
    assert all(call.method in allowed_methods for call in client.calls)
    assert all(call.method not in {"create", "write", "unlink"} for call in client.calls)
