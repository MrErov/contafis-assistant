"""Tools ERP de solo lectura (T03)."""

from contafis_assistant.odoo.tools.alerts import query_alerts, sanitize_alert_message
from contafis_assistant.odoo.tools.clients import query_clients
from contafis_assistant.odoo.tools.companies import query_companies
from contafis_assistant.odoo.tools.documents import query_documents
from contafis_assistant.odoo.tools.entities import (
    EntityResolution,
    resolve_entity,
)
from contafis_assistant.odoo.tools.obligations import query_obligations

__all__ = [
    "EntityResolution",
    "query_alerts",
    "query_clients",
    "query_companies",
    "query_documents",
    "query_obligations",
    "resolve_entity",
    "sanitize_alert_message",
]
