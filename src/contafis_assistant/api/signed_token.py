"""Token efímero firmado que reemplaza el ``sid`` de Odoo (P0-04).

Formato (idéntico al generador de Odoo)::

    base64url(payload_json).base64url(HMAC-SHA256(secret, payload_b64))

El payload es un JSON canónico ``{"company_id": int, "exp": int, "uid": int}``
(claves ordenadas, sin espacios). El assistant solo confía en tokens firmados
con ``API_SHARED_SECRET``; el valor del token nunca se persiste ni se registra.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

#: Header que transporta el token firmado.
TOKEN_HEADER = "X-ContaFis-Token"

#: Vida útil acordada con Odoo.
TOKEN_TTL_SECONDS = 60

#: Margen de reloj aceptado entre Odoo y el assistant.
TOKEN_CLOCK_SKEW_SECONDS = 5

#: Vigencia máxima aceptada al verificar (rechaza tokens de larga duración).
TOKEN_MAX_FUTURE_SECONDS = TOKEN_TTL_SECONDS + TOKEN_CLOCK_SKEW_SECONDS


class SignedTokenError(ValueError):
    """Token ausente, malformado, alterado o expirado."""


@dataclass(frozen=True)
class SignedTokenClaims:
    """Claims verificados del token."""

    uid: int
    company_id: int
    exp: int


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _normalize_secret(secret: str | None) -> str:
    if not isinstance(secret, str):
        raise SignedTokenError("El secreto compartido no está configurado.")
    secret = secret.strip()
    if not secret:
        raise SignedTokenError("El secreto compartido no está configurado.")
    return secret


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SignedTokenError(f"El claim {name} debe ser un entero positivo.")
    return value


def _payload(uid: int, company_id: int, exp: int) -> bytes:
    return json.dumps(
        {"uid": uid, "company_id": company_id, "exp": exp},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sign_token(
    secret: str,
    *,
    uid: int,
    company_id: int,
    ttl_seconds: int = TOKEN_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Firma un token para tests y herramientas internas (Odoo es el emisor)."""
    secret = _normalize_secret(secret)
    uid = _positive_int(int(uid), "uid")
    company_id = _positive_int(int(company_id), "company_id")
    ttl_seconds = int(ttl_seconds)
    if ttl_seconds <= 0 or ttl_seconds > TOKEN_TTL_SECONDS:
        raise SignedTokenError(
            f"El TTL debe estar entre 1 y {TOKEN_TTL_SECONDS} segundos."
        )

    issued_at = int(time.time() if now is None else now)
    exp = issued_at + ttl_seconds
    payload_b64 = _b64url_encode(_payload(uid, company_id, exp))
    signature = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{payload_b64}.{_b64url_encode(signature)}"


def verify_token(
    token: str,
    secret: str | None,
    *,
    now: float | None = None,
) -> SignedTokenClaims:
    """Verifica firma, formato y expiración; lanza ``SignedTokenError``."""
    secret = _normalize_secret(secret)
    if not isinstance(token, str):
        raise SignedTokenError("El token debe ser una cadena.")

    parts = token.strip().split(".")
    if len(parts) != 2:
        raise SignedTokenError("El token tiene un formato inválido.")
    payload_b64, signature_b64 = parts
    if not payload_b64 or not signature_b64:
        raise SignedTokenError("El token tiene un formato inválido.")

    try:
        signature = _b64url_decode(signature_b64)
        payload_bytes = _b64url_decode(payload_b64)
    except Exception as exc:  # base64 inválido
        raise SignedTokenError("El token tiene un formato inválido.") from exc

    expected = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected):
        raise SignedTokenError("La firma del token no es válida.")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SignedTokenError("El payload del token no es válido.") from exc
    if not isinstance(payload, dict):
        raise SignedTokenError("El payload del token no es válido.")

    uid = _positive_int(payload.get("uid"), "uid")
    company_id = _positive_int(payload.get("company_id"), "company_id")
    exp = _positive_int(payload.get("exp"), "exp")

    current = time.time() if now is None else float(now)
    if exp < current:
        raise SignedTokenError("El token expiró.")
    if exp > current + TOKEN_MAX_FUTURE_SECONDS:
        raise SignedTokenError("El token tiene una vigencia mayor a la permitida.")

    return SignedTokenClaims(uid=uid, company_id=company_id, exp=exp)


__all__ = [
    "TOKEN_CLOCK_SKEW_SECONDS",
    "TOKEN_HEADER",
    "TOKEN_MAX_FUTURE_SECONDS",
    "TOKEN_TTL_SECONDS",
    "SignedTokenClaims",
    "SignedTokenError",
    "sign_token",
    "verify_token",
]
