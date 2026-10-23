"""Autenticación opcional por `X-API-Key` con comparación en tiempo constante."""

from __future__ import annotations

from hmac import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException
from pydantic import SecretStr

API_KEY_HEADER = "X-API-Key"


def _secret_text(value: SecretStr | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, SecretStr):
        text = value.get_secret_value()
    else:
        text = str(value)
    return text if text else None


def verify_api_key(
    provided: str | None,
    expected: str | None,
) -> bool:
    """Compara la key recibida con el secreto configurado.

    Sin secreto configurado no hay autenticación. La comparación usa
    ``hmac.compare_digest`` y nunca se hace por igualdad de strings.
    """
    if not expected:
        return True
    if not isinstance(provided, str) or not provided:
        return False
    return compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


class ApiKeyGuard:
    """Dependencia de FastAPI para todas las rutas, incluido `/health`."""

    def __init__(self, secret: SecretStr | str | None = None) -> None:
        self._expected = _secret_text(secret)

    @property
    def expected(self) -> str | None:
        return self._expected

    def check(self, provided: str | None) -> bool:
        return verify_api_key(provided, self._expected)

    def __call__(
        self,
        x_api_key: Annotated[
            str | None,
            Header(alias=API_KEY_HEADER),
        ] = None,
    ) -> None:
        if not verify_api_key(x_api_key, self._expected):
            raise HTTPException(status_code=401, detail="Unauthorized")


__all__ = [
    "API_KEY_HEADER",
    "ApiKeyGuard",
    "verify_api_key",
]
