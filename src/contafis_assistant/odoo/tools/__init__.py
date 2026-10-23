"""Tools ERP de solo lectura (T03)."""

from contafis_assistant.odoo.tools.clients import query_clients
from contafis_assistant.odoo.tools.companies import query_companies
from contafis_assistant.odoo.tools.entities import (
    EntityResolution,
    resolve_entity,
)
from contafis_assistant.odoo.tools.obligations import query_obligations

__all__ = [
    "EntityResolution",
    "query_clients",
    "query_companies",
    "query_obligations",
    "resolve_entity",
]
