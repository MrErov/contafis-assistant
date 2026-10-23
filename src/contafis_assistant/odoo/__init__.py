"""Acceso read-only a Odoo 19 por JSON/2 (allowlist, cliente y scope)."""

from __future__ import annotations

from contafis_assistant.odoo.allowlist import (
    ALLOWED_GROUP_EXT_IDS,
    COMPANY_FILTER_FIELDS,
    MODEL_POLICIES,
    RpcCall,
    has_company_filter,
    validate_rpc,
)
from contafis_assistant.odoo.client import OdooClient, ToolBudget
from contafis_assistant.odoo.errors import (
    InvalidToolArgs,
    NoBudgetError,
    OdooAuthError,
    OdooConcurrencyError,
    OdooError,
    OdooResponseError,
    OdooTransientError,
)
from contafis_assistant.odoo.scope import (
    ScopeReason,
    ScopeService,
    ScopeSnapshot,
    ScopeStartupError,
    classify_scope,
)

__all__ = [
    "ALLOWED_GROUP_EXT_IDS",
    "COMPANY_FILTER_FIELDS",
    "InvalidToolArgs",
    "MODEL_POLICIES",
    "NoBudgetError",
    "OdooAuthError",
    "OdooClient",
    "OdooConcurrencyError",
    "OdooError",
    "OdooResponseError",
    "OdooTransientError",
    "RpcCall",
    "ScopeReason",
    "ScopeService",
    "ScopeSnapshot",
    "ScopeStartupError",
    "ToolBudget",
    "classify_scope",
    "has_company_filter",
    "validate_rpc",
]
