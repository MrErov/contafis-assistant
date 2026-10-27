"""Cliente Odoo 19 JSON/2 read-only con timeout, errores tipados y retry acotado."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Sequence

import httpx

from contafis_assistant.odoo.allowlist import (
    RpcCall,
    has_company_filter,
    validate_rpc,
)
from contafis_assistant.odoo.errors import (
    InvalidToolArgs,
    NoBudgetError,
    OdooAuthError,
    OdooConcurrencyError,
    OdooResponseError,
    OdooTransientError,
)

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 2
_CONCURRENCY_MARKERS = ("unhashable type", "invalid field 'count'")


@dataclass
class ToolBudget:
    """Presupuesto de invocaciones del turno; un reintento o paginación consume."""

    max_calls: int = 3
    used: int = 0

    def __post_init__(self) -> None:
        if self.max_calls < 0:
            raise ValueError("max_calls no puede ser negativo")
        if self.used < 0:
            raise ValueError("used no puede ser negativo")

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.used)

    def can_spend(self) -> bool:
        return self.remaining > 0

    def spend(self, amount: int = 1) -> None:
        if amount <= 0:
            raise ValueError("amount debe ser positivo")
        if self.used + amount > self.max_calls:
            raise NoBudgetError()
        self.used += amount


class OdooClient:
    """Ejecuta RPC ya validados contra ``POST /json/2/{model}/{method}``.

    Las llamadas se serializan con un lock no bloqueante: no hay dos RPC en
    paralelo dentro del proceso. El contexto siempre incluye
    ``allowed_company_ids`` y la API key nunca aparece en logs ni excepciones.
    """

    def __init__(
        self,
        *,
        url: str,
        database: str,
        api_key: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        allowed_company_ids: Sequence[int] | None = None,
        uid: int | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._database = database
        self._api_key = api_key
        self._timeout = timeout
        self._allowed_company_ids = [int(value) for value in (allowed_company_ids or [])]
        self.uid = uid
        self._lock = threading.Lock()
        self._http = httpx.Client(timeout=timeout, transport=transport)

    # ------------------------------------------------------------------
    # Configuración / ciclo de vida
    # ------------------------------------------------------------------

    def set_allowed_company_ids(self, company_ids: Sequence[int]) -> None:
        self._allowed_company_ids = [int(value) for value in company_ids]

    @property
    def allowed_company_ids(self) -> list[int]:
        return list(self._allowed_company_ids)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "OdooClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------

    def execute(
        self,
        call: RpcCall,
        *,
        budget: ToolBudget | None = None,
        previous_truncated: RpcCall | None = None,
        allowed_company_ids: Sequence[int] | None = None,
    ) -> Any:
        if not isinstance(call, RpcCall):
            raise InvalidToolArgs("se esperaba un RpcCall tipado")

        allowed = (
            [int(value) for value in allowed_company_ids]
            if allowed_company_ids is not None
            else list(self._allowed_company_ids)
        )

        validated = validate_rpc(
            call,
            uid=self.uid,
            allowed_company_ids=allowed,
        )

        if previous_truncated is not None:
            self._validate_truncation_retry(previous_truncated, validated)

        if not self._lock.acquire(blocking=False):
            raise OdooConcurrencyError(
                "llamada concurrente a Odoo bloqueada; el cliente es secuencial"
            )

        try:
            if previous_truncated is not None:
                self._spend_or_raise(budget)
            return self._execute_with_retry(validated, budget, allowed)
        finally:
            self._lock.release()

    def _execute_with_retry(
        self,
        call: RpcCall,
        budget: ToolBudget | None,
        allowed_company_ids: Sequence[int],
    ) -> Any:
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._post(call, allowed_company_ids)
            except OdooAuthError:
                raise
            except OdooTransientError as exc:
                if attempt >= _MAX_ATTEMPTS:
                    raise
                if budget is None or not budget.can_spend():
                    raise
                budget.spend()
                logger.warning(
                    "odoo_retry model=%s method=%s status=%s attempt=%s",
                    call.model,
                    call.method,
                    exc.status,
                    attempt,
                )

    def _post(self, call: RpcCall, allowed_company_ids: Sequence[int]) -> Any:
        url = f"{self._url}/json/2/{call.model}/{call.method}"
        body = self._build_body(call, allowed_company_ids)

        try:
            response = self._http.post(url, headers=self._headers(), json=body)
        except httpx.TimeoutException:
            raise OdooTransientError("timeout al llamar a Odoo") from None
        except httpx.TransportError:
            raise OdooTransientError("error de transporte al llamar a Odoo") from None

        return self._parse_response(response)

    def _parse_response(self, response: httpx.Response) -> Any:
        status = response.status_code
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None

        if status in {401, 403}:
            raise OdooAuthError(
                f"Odoo rechazó las credenciales (HTTP {status})",
                status=status,
            )

        if _is_concurrency_payload(payload):
            raise OdooConcurrencyError(
                f"Odoo reportó una anomalía de concurrencia (HTTP {status})",
                status=status,
            )

        if status >= 500:
            raise OdooTransientError(
                f"Odoo no está disponible (HTTP {status})", status=status
            )

        if status >= 400:
            raise OdooResponseError(
                f"Odoo rechazó la llamada (HTTP {status})", status=status
            )

        if payload is None:
            raise OdooResponseError(
                "Odoo devolvió una respuesta no JSON", status=status
            )

        if _looks_like_error_payload(payload):
            raise OdooResponseError(
                "Odoo devolvió un error en la respuesta", status=status
            )

        return payload

    # ------------------------------------------------------------------
    # Construcción de request
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "X-Odoo-Database": self._database,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "contafis-assistant",
        }

    def _build_body(
        self,
        call: RpcCall,
        allowed_company_ids: Sequence[int],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if call.ids is not None:
            body["ids"] = list(call.ids)

        context: dict[str, Any] = {
            "allowed_company_ids": [int(value) for value in allowed_company_ids]
        }
        if call.active_test is not None:
            context["active_test"] = call.active_test
        body["context"] = context

        if call.domain is not None:
            body["domain"] = _jsonable(call.domain)
        if call.fields is not None:
            body["fields"] = list(call.fields)
        if call.offset is not None:
            body["offset"] = call.offset
        if call.limit is not None:
            body["limit"] = call.limit
        if call.group_ext_id is not None:
            body["group_ext_id"] = call.group_ext_id

        return body

    # ------------------------------------------------------------------
    # Presupuesto y truncamiento
    # ------------------------------------------------------------------

    @staticmethod
    def _spend_or_raise(budget: ToolBudget | None) -> None:
        if budget is None:
            raise NoBudgetError(
                "una paginación por truncamiento requiere presupuesto"
            )
        budget.spend()

    @staticmethod
    def _validate_truncation_retry(
        previous: RpcCall,
        current: RpcCall,
    ) -> None:
        same_target = (
            previous.model == current.model
            and previous.method == current.method
            and previous.ids == current.ids
            and previous.domain == current.domain
            and previous.fields == current.fields
        )
        if not same_target:
            return
        if (
            previous.offset == current.offset
            and previous.limit == current.limit
        ):
            raise InvalidToolArgs(
                "el reintento por truncamiento debe cambiar offset o limit"
            )


def _jsonable(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _is_concurrency_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    text = json.dumps(payload, ensure_ascii=False, default=str).lower()
    return any(marker in text for marker in _CONCURRENCY_MARKERS)


def _looks_like_error_payload(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and bool(payload.get("name"))
        and "message" in payload
    )
