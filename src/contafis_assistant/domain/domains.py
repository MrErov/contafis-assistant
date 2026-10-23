"""Dominios Odoo exactos de la sección 9.3 (sin I/O)."""

from __future__ import annotations

from datetime import date
from typing import Any

Domain = list[Any]


def d_pending(today: date) -> Domain:
    return [
        "|",
        "&",
        ("state", "=", "pending"),
        "|",
        ("due_date", "=", False),
        ("due_date", ">=", today),
        "&",
        ("state", "=", "overdue"),
        ("due_date", ">=", today),
    ]


def d_overdue(today: date) -> Domain:
    return [
        "|",
        "&",
        ("state", "=", "overdue"),
        "|",
        ("due_date", "=", False),
        ("due_date", "<", today),
        "&",
        ("state", "=", "pending"),
        ("due_date", "<", today),
    ]


def d_paid() -> Domain:
    return [("state", "=", "paid")]


def d_cancelled() -> Domain:
    return [("state", "=", "cancelled")]


def d_doc_expired(today: date) -> Domain:
    return [
        "|",
        "&",
        ("state", "in", ["valid", "pending", "expired"]),
        ("expiry_date", "!=", False),
        ("expiry_date", "<", today),
        "&",
        ("state", "=", "expired"),
        ("expiry_date", "=", False),
    ]


def d_doc_pending(today: date) -> Domain:
    return [
        "|",
        ("state", "=", False),
        "|",
        "&",
        ("state", "=", "pending"),
        "|",
        ("expiry_date", "=", False),
        ("expiry_date", ">=", today),
        "&",
        ("state", "=", "expired"),
        ("expiry_date", "!=", False),
        ("expiry_date", ">=", today),
    ]


def d_doc_valid(today: date) -> Domain:
    return [
        "|",
        "&",
        ("state", "=", "valid"),
        ("expiry_date", "=", False),
        "&",
        ("state", "=", "valid"),
        ("expiry_date", ">=", today),
    ]


def d_doc_rejected() -> Domain:
    return [("state", "=", "rejected")]


def d_doc_missing() -> Domain:
    return [("state", "=", "missing")]
