"""Enums estables compartidos por el contrato y el dominio."""

from __future__ import annotations

from enum import Enum


class Status(str, Enum):
    ok = "ok"
    needs_clarification = "needs_clarification"
    not_found = "not_found"
    insufficient_context = "insufficient_context"
    out_of_scope = "out_of_scope"
    partial = "partial"
    iteration_limit = "iteration_limit"
    upstream_error = "upstream_error"
    invalid_request = "invalid_request"


class Intent(str, Enum):
    erp = "erp"
    normativa = "normativa"
    mixta = "mixta"
    conversacional = "conversacional"
    fuera_de_alcance = "fuera_de_alcance"


class ScopeState(str, Enum):
    scope_ok = "scope_ok"
    scope_partial = "scope_partial"
    scope_unsafe = "scope_unsafe"


class CorpusStatus(str, Enum):
    ok = "ok"
    missing = "missing"
    empty = "empty"
    mismatch = "mismatch"


class Kind(str, Enum):
    obligations = "obligations"
    clients = "clients"
    documents = "documents"
    alerts = "alerts"
    companies = "companies"


class EntityType(str, Enum):
    company = "company"
    client = "client"


class WarningSource(str, Enum):
    odoo = "odoo"
    rag = "rag"
    system = "system"


class DocumentSource(str, Enum):
    current_snapshot = "current_snapshot"
    live_document = "live_document"
    previous_snapshot = "previous_snapshot"


class ObligationState(str, Enum):
    pending = "pending"
    paid = "paid"
    overdue = "overdue"
    cancelled = "cancelled"


class DocumentState(str, Enum):
    valid = "valid"
    expired = "expired"
    pending = "pending"
    rejected = "rejected"
    missing = "missing"


class AlertState(str, Enum):
    pending = "pending"
    sent = "sent"
    done = "done"


class ComplianceStatus(str, Enum):
    al_dia = "al_dia"
    pendiente = "pendiente"
    vencido = "vencido"


class WarningCode(str, Enum):
    """Códigos estables de la sección 8.4. Nunca se combinan con `/`."""

    thread_context_unavailable = "thread_context_unavailable"
    thread_evicted = "thread_evicted"
    thread_history_truncated = "thread_history_truncated"
    scope_partial = "scope_partial"
    scope_admin_key = "scope_admin_key"
    company_archived = "company_archived"
    no_active_companies = "no_active_companies"
    cartelera_fallback_live_documents = "cartelera_fallback_live_documents"
    cartelera_fallback_previous_snapshot = "cartelera_fallback_previous_snapshot"
    cartelera_snapshot_incomplete = "cartelera_snapshot_incomplete"
    count_unavailable = "count_unavailable"
    truncated = "truncated"
    null_obligation_type = "null_obligation_type"
    null_document_state = "null_document_state"
    null_alert_date = "null_alert_date"
    effective_state_adjusted = "effective_state_adjusted"
    no_due_date = "no_due_date"
    no_expiry_date = "no_expiry_date"
    fuzzy_match = "fuzzy_match"
    out_of_scope_component = "out_of_scope_component"
    source_unavailable = "source_unavailable"
    retrieval_low_score = "retrieval_low_score"
    corpus_unavailable = "corpus_unavailable"
    page_metadata_missing = "page_metadata_missing"
    manifest_mismatch = "manifest_mismatch"
    odoo_retry = "odoo_retry"
    no_alerts_today = "no_alerts_today"
    invalid_tool_args = "invalid_tool_args"
