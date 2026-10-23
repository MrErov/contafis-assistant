"""Compuerta de alcance (scope_check) con TTL, lock y degradación explícita.

Clasifica en ``scope_ok``, ``scope_partial`` o ``scope_unsafe`` y aplica las
consecuencias de §8.3 y RF-062/RNF-013/RNF-016. No usa ``ir.model.data``: los
grupos se resuelven con ``res.users.has_group`` y XML IDs cerrados.
"""

from __future__ import annotations

import threading
from datetime import datetime
from enum import Enum
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.enums import ScopeState, WarningCode
from contafis_assistant.domain.time import Clock
from contafis_assistant.odoo.allowlist import ALLOWED_GROUP_EXT_IDS, RpcCall
from contafis_assistant.odoo.client import OdooClient
from contafis_assistant.odoo.errors import OdooError


class ScopeReason(str, Enum):
    ok = "ok"
    admin = "admin"
    attestation = "attestation"
    not_internal = "not_internal"
    incomplete_coverage = "incomplete_coverage"
    no_active_companies = "no_active_companies"
    odoo_error = "odoo_error"


class ScopeSnapshot(BaseModel):
    """Resultado tipado del scope_check y sus consecuencias operativas."""

    model_config = ConfigDict(extra="forbid")

    state: ScopeState
    reason: ScopeReason
    uid: int | None = None
    user_company_ids: list[int] = Field(default_factory=list)
    active_company_ids: list[int] = Field(default_factory=list)
    is_admin: bool = False
    is_internal: bool = True
    attested: bool = False
    app_env: Literal["development", "test", "production"] = "development"
    checked_at: datetime

    @property
    def scope_complete(self) -> bool:
        return self.state is ScopeState.scope_ok

    @property
    def admin_key_warning(self) -> bool:
        return self.state is ScopeState.scope_unsafe and self.reason in {
            ScopeReason.admin,
            ScopeReason.attestation,
        }

    @property
    def tools_enabled(self) -> bool:
        if self.state is ScopeState.scope_unsafe:
            return self.app_env != "production" and self.reason in {
                ScopeReason.admin,
                ScopeReason.attestation,
            }
        return True

    @property
    def healthy(self) -> bool:
        return not (
            self.app_env == "production" and self.state is ScopeState.scope_unsafe
        )

    @property
    def warnings(self) -> list[WarningCode]:
        if self.state is ScopeState.scope_partial:
            return [WarningCode.scope_partial]
        if self.admin_key_warning:
            return [WarningCode.scope_admin_key]
        return []


def classify_scope(
    *,
    is_admin: bool,
    is_internal: bool,
    attested: bool,
    user_company_ids: Sequence[int],
    active_company_ids: Sequence[int],
    app_env: Literal["development", "test", "production"],
    checked_at: datetime,
    uid: int | None = None,
) -> ScopeSnapshot:
    """Clasificación pura de §8.3; sin I/O."""

    user_ids = _normalize_ids(user_company_ids)
    active_ids = _normalize_ids(active_company_ids)

    common = {
        "uid": uid,
        "user_company_ids": user_ids,
        "active_company_ids": active_ids,
        "is_admin": is_admin,
        "is_internal": is_internal,
        "attested": attested,
        "app_env": app_env,
        "checked_at": checked_at,
    }

    if not is_internal:
        return ScopeSnapshot(
            state=ScopeState.scope_unsafe,
            reason=ScopeReason.not_internal,
            **common,
        )

    if is_admin:
        return ScopeSnapshot(
            state=ScopeState.scope_unsafe,
            reason=ScopeReason.admin,
            **common,
        )

    if not attested:
        return ScopeSnapshot(
            state=ScopeState.scope_unsafe,
            reason=ScopeReason.attestation,
            **common,
        )

    if not active_ids:
        return ScopeSnapshot(
            state=ScopeState.scope_partial,
            reason=ScopeReason.no_active_companies,
            **common,
        )

    if not set(active_ids).issubset(set(user_ids)):
        return ScopeSnapshot(
            state=ScopeState.scope_partial,
            reason=ScopeReason.incomplete_coverage,
            **common,
        )

    return ScopeSnapshot(
        state=ScopeState.scope_ok,
        reason=ScopeReason.ok,
        **common,
    )


class ScopeStartupError(RuntimeError):
    """Producción con scope_unsafe en el chequeo inicial (RNF-016)."""


class ScopeService:
    """Ejecuta y cachea el scope_check con lock y TTL inyectable."""

    def __init__(
        self,
        client: OdooClient,
        *,
        app_env: Literal["development", "test", "production"],
        attested: bool,
        ttl_seconds: int = 300,
        clock: Clock | None = None,
    ) -> None:
        self._client = client
        self._app_env = app_env
        self._attested = attested
        self._ttl_seconds = ttl_seconds
        self._clock = clock or Clock()
        self._lock = threading.RLock()
        self._snapshot: ScopeSnapshot | None = None

    @property
    def snapshot(self) -> ScopeSnapshot | None:
        return self._snapshot

    def startup(self) -> ScopeSnapshot:
        """Chequeo inicial; en producción, unsafe es fail-fast."""
        snapshot = self.refresh(force=True)
        if (
            self._app_env == "production"
            and snapshot.state is ScopeState.scope_unsafe
        ):
            raise ScopeStartupError(
                "scope_unsafe al arrancar en producción; el servicio no inicia"
            )
        return snapshot

    def current(self) -> ScopeSnapshot:
        return self.refresh()

    def refresh(self, *, force: bool = False) -> ScopeSnapshot:
        with self._lock:
            now = self._clock.now()
            if not force and self._snapshot is not None:
                age = (now - self._snapshot.checked_at).total_seconds()
                if 0 <= age < self._ttl_seconds:
                    return self._snapshot
            snapshot = self._check(now)
            self._snapshot = snapshot
            return snapshot

    # ------------------------------------------------------------------
    # Chequeo con RPC secuenciales
    # ------------------------------------------------------------------

    def _check(self, now: datetime) -> ScopeSnapshot:
        try:
            context = self._client.execute(
                RpcCall(model="res.users", method="context_get")
            )
            if not isinstance(context, dict):
                raise OdooError("context_get devolvió un valor inesperado")

            uid = context.get("uid")
            if uid is None:
                uid = getattr(self._client, "uid", None)
            if uid is None:
                raise OdooError("no se pudo determinar el uid del usuario")

            self._client.uid = uid

            allowed = context.get("allowed_company_ids")
            if allowed is None:
                rows = self._client.execute(
                    RpcCall(
                        model="res.users",
                        method="read",
                        ids=[uid],
                        fields=["company_ids"],
                    )
                )
                allowed = rows[0].get("company_ids", []) if rows else []

            allowed_ids = _normalize_ids(allowed or [])
            self._client.set_allowed_company_ids(allowed_ids)

            is_system = bool(
                self._client.execute(
                    RpcCall(
                        model="res.users",
                        method="has_group",
                        ids=[uid],
                        group_ext_id="base.group_system",
                    )
                )
            )
            is_erp_manager = bool(
                self._client.execute(
                    RpcCall(
                        model="res.users",
                        method="has_group",
                        ids=[uid],
                        group_ext_id="base.group_erp_manager",
                    )
                )
            )
            is_internal = bool(
                self._client.execute(
                    RpcCall(
                        model="res.users",
                        method="has_group",
                        ids=[uid],
                        group_ext_id="base.group_user",
                    )
                )
            )

            companies = self._client.execute(
                RpcCall(
                    model="res.company",
                    method="search_read",
                    domain=[("active", "=", True)],
                    fields=["id", "name"],
                )
            )
            active_ids = _normalize_ids(
                [
                    row.get("id")
                    for row in companies or []
                    if isinstance(row, dict) and row.get("id") is not None
                ]
            )

            return classify_scope(
                is_admin=is_system or is_erp_manager,
                is_internal=is_internal,
                attested=self._attested,
                user_company_ids=allowed_ids,
                active_company_ids=active_ids,
                app_env=self._app_env,
                checked_at=now,
                uid=uid,
            )
        except OdooError:
            return ScopeSnapshot(
                state=ScopeState.scope_unsafe,
                reason=ScopeReason.odoo_error,
                uid=getattr(self._client, "uid", None),
                user_company_ids=[],
                active_company_ids=[],
                is_admin=False,
                is_internal=False,
                attested=self._attested,
                app_env=self._app_env,
                checked_at=now,
            )


def _normalize_ids(values: Sequence[int] | None) -> list[int]:
    if not values:
        return []
    normalized: set[int] = set()
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            normalized.add(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(normalized)


__all__ = [
    "ALLOWED_GROUP_EXT_IDS",
    "ScopeReason",
    "ScopeService",
    "ScopeSnapshot",
    "ScopeStartupError",
    "classify_scope",
]
