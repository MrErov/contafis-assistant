"""Cliente Odoo 19 read-only con timeout, errores tipados y retry acotado.

Dos modos de autenticación, dos protocolos:

* **API key** → ``POST /json/2/{model}/{method}`` (Odoo 19, ``Bearer``).
* **Sesión de usuario** → ``POST /web/dataset/call_kw`` (JSON-RPC clásico con
  cookie). El JSON/2 sólo acepta ``Authorization: Bearer``: con una cookie de
  sesión responde 401, así que el camino del widget de Odoo no puede usarlo.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping
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
        api_key: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        allowed_company_ids: Sequence[int] | None = None,
        uid: int | None = None,
        session_id: str | None = None,
    ) -> None:
        if api_key is None and session_id is None:
            raise ValueError("Se requiere api_key o session_id")
        self._url = url.rstrip("/")
        self._database = database
        self._api_key = api_key
        self._session_id = session_id
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
        session = bool(self._session_id)
        if session:
            url = f"{self._url}/web/dataset/call_kw"
            body = self._build_call_kw_body(call, allowed_company_ids)
        else:
            url = f"{self._url}/json/2/{call.model}/{call.method}"
            body = self._build_body(call, allowed_company_ids)

        try:
            response = self._http.post(url, headers=self._headers(), json=body)
        except httpx.TimeoutException:
            raise OdooTransientError("timeout al llamar a Odoo") from None
        except httpx.TransportError:
            raise OdooTransientError("error de transporte al llamar a Odoo") from None

        return self._parse_response(response, session=session)

    def _parse_response(
        self,
        response: httpx.Response,
        *,
        session: bool = False,
    ) -> Any:
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

        if session:
            return _unwrap_call_kw(payload)

        if _looks_like_error_payload(payload):
            raise OdooResponseError(
                "Odoo devolvió un error en la respuesta", status=status
            )

        return payload

    # ------------------------------------------------------------------
    # Construcción de request
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Odoo-Database": self._database,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "contafis-assistant",
        }
        if self._session_id:
            headers["Cookie"] = f"session_id={self._session_id}"
        else:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _build_body(
        self,
        call: RpcCall,
        allowed_company_ids: Sequence[int],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if call.ids is not None:
            body["ids"] = list(call.ids)

        body["context"] = self._build_context(call, allowed_company_ids)

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

    def _build_call_kw_body(
        self,
        call: RpcCall,
        allowed_company_ids: Sequence[int],
    ) -> dict[str, Any]:
        """Sobre JSON-RPC de ``/web/dataset/call_kw`` para el modo sesión.

        ``args`` lleva los posicionales que el método de Odoo espera —dominio,
        ``ids`` o XML ID del grupo, en ese orden— y ``kwargs`` el resto, igual
        que lo hace el cliente web.
        """
        args: list[Any] = []
        if call.domain is not None:
            args.append(_jsonable(call.domain))
        elif call.ids is not None:
            args.append(list(call.ids))
        if call.group_ext_id is not None:
            args.append(call.group_ext_id)

        kwargs: dict[str, Any] = {}
        if call.fields is not None:
            kwargs["fields"] = list(call.fields)
        if call.offset is not None:
            kwargs["offset"] = call.offset
        if call.limit is not None:
            kwargs["limit"] = call.limit
        kwargs["context"] = self._build_context(call, allowed_company_ids)

        return {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "model": call.model,
                "method": call.method,
                "args": args,
                "kwargs": kwargs,
            },
        }

    @staticmethod
    def _build_context(
        call: RpcCall,
        allowed_company_ids: Sequence[int],
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "allowed_company_ids": [int(value) for value in allowed_company_ids]
        }
        if call.active_test is not None:
            context["active_test"] = call.active_test
        return context

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


def _unwrap_call_kw(payload: Any) -> Any:
    """Desenvuelve la respuesta de ``/web/dataset/call_kw``.

    Odoo responde HTTP 200 incluso cuando la llamada falla: el error viaja en
    la clave ``error`` y el éxito en ``result``.
    """
    if not isinstance(payload, Mapping):
        return payload

    error = payload.get("error")
    if isinstance(error, Mapping):
        if _is_session_expired_error(error):
            raise OdooAuthError("la sesión de Odoo expiró o no es válida")
        raise OdooResponseError("Odoo devolvió un error en la respuesta")

    return payload.get("result")


def _is_session_expired_error(error: Mapping[str, Any]) -> bool:
    if error.get("code") == 100:
        return True
    data = error.get("data")
    if isinstance(data, Mapping):
        return data.get("name") == "odoo.http.SessionExpiredException"
    return False
