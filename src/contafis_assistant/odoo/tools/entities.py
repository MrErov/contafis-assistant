"""Resolución de compañías y clientes: normalización, precedencia y fuzzy."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz import fuzz

from contafis_assistant.domain.contracts import EntityRef, Warning
from contafis_assistant.domain.enums import EntityType, Status, WarningCode
from contafis_assistant.odoo.client import ToolBudget
from contafis_assistant.odoo.tools.common import (
    CLIENT_READ_FIELDS,
    COMPANY_READ_FIELDS,
    OdooGateway,
    make_warning,
    many2one_id,
    normalize_ids,
    search_read,
)

FUZZY_MIN_SCORE = 85
MAX_OPTIONS = 5
ENTITY_READ_LIMIT = 2000


class ClarificationReason(str, Enum):
    ambiguous = "ambiguous"
    tie = "tie"
    too_many = "too_many"
    id_collision = "id_collision"
    non_entity = "non_entity"


class EntityResolution(BaseModel):
    """Decisión de resolución lista para el nodo de entidad del grafo."""

    model_config = ConfigDict(extra="forbid")

    status: Status
    entity: EntityRef | None = None
    options: list[EntityRef] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
    reason: ClarificationReason | None = None
    scope_complete: bool = True


def normalize_entity_name(value: str) -> str:
    """Minúsculas, sin acentos y con espacios colapsados."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_accents.casefold().split())


def score_entity_name(query: str, candidate: str) -> float:
    """RapidFuzz ``token_set_ratio`` sobre nombres normalizados."""
    return float(
        fuzz.token_set_ratio(
            normalize_entity_name(query),
            normalize_entity_name(candidate),
        )
    )


def rank_candidates(
    mention: str,
    candidates: Iterable[EntityRef],
    *,
    fuzzy_min_score: int = FUZZY_MIN_SCORE,
) -> list[tuple[EntityRef, float]]:
    """Candidatos sobre el umbral, ordenados ``score desc, id``."""
    normalized_mention = normalize_entity_name(mention)
    scored = [
        (
            candidate,
            float(
                fuzz.token_set_ratio(
                    normalized_mention,
                    normalize_entity_name(candidate.name),
                )
            ),
        )
        for candidate in candidates
    ]
    ranked = [
        (candidate, score)
        for candidate, score in scored
        if score >= fuzzy_min_score
    ]
    return sorted(
        ranked,
        key=lambda item: (-item[1], item[0].id, item[0].type.value),
    )


def resolve_entity(
    gateway: OdooGateway,
    mention: str,
    *,
    allowed_company_ids: Sequence[int],
    scope_complete: bool = True,
    fuzzy_min_score: int = FUZZY_MIN_SCORE,
    budget: ToolBudget | None = None,
) -> EntityResolution:
    """Resuelve una mención con precedencia compañía > cliente > fuzzy.

    La lectura de compañías es única y usa ``active_test=False`` para poder
    informar compañías archivadas. Los clientes se leen una sola vez y el
    fuzzy solo puntúa compañías activas y clientes de compañías activas.
    """
    allowed = normalize_ids(allowed_company_ids)
    mention_text = str(mention).strip()

    if not mention_text:
        return EntityResolution(
            status=Status.needs_clarification,
            reason=ClarificationReason.non_entity,
            scope_complete=scope_complete,
        )
    if not allowed:
        return _not_found(scope_complete=scope_complete, suggestions=[])

    companies = search_read(
        gateway,
        "res.company",
        [],
        COMPANY_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
        limit=ENTITY_READ_LIMIT,
        active_test=False,
    )
    clients = search_read(
        gateway,
        "l10n.ve.compliance.client",
        [("company_id", "in", allowed)],
        CLIENT_READ_FIELDS,
        budget=budget,
        allowed_company_ids=allowed,
        limit=ENTITY_READ_LIMIT,
    )

    company_map = _company_map(companies)
    company_refs = [_company_ref(row) for row in companies]
    company_refs = _dedupe_refs(company_refs)

    client_refs: list[EntityRef] = []
    for row in clients:
        reference = _client_ref(row, company_map)
        if reference is not None:
            client_refs.append(reference)
    client_refs = _dedupe_refs(client_refs)

    if mention_text.isdigit():
        return _resolve_numeric(
            int(mention_text),
            company_refs,
            client_refs,
            scope_complete=scope_complete,
        )

    normalized = normalize_entity_name(mention_text)

    company_exact = [
        reference
        for reference in company_refs
        if normalize_entity_name(reference.name) == normalized
    ]
    if company_exact:
        return _resolve_exact(company_exact, scope_complete=scope_complete)

    client_exact = [
        reference
        for reference in client_refs
        if normalize_entity_name(reference.name) == normalized
    ]
    if client_exact:
        return _resolve_exact(client_exact, scope_complete=scope_complete)

    active_company_ids = {
        reference.id for reference in company_refs if not reference.company_archived
    }
    fuzzy_pool = [
        reference
        for reference in company_refs
        if not reference.company_archived
    ] + [
        reference
        for reference in client_refs
        if reference.company_id in active_company_ids
    ]

    ranked = rank_candidates(
        mention_text,
        fuzzy_pool,
        fuzzy_min_score=fuzzy_min_score,
    )

    if len(ranked) > MAX_OPTIONS:
        return _clarification(
            ClarificationReason.too_many,
            [reference for reference, _score in ranked[:MAX_OPTIONS]],
            scope_complete=scope_complete,
        )
    if len(ranked) > 1:
        top_score = ranked[0][1]
        second_score = ranked[1][1]
        reason = (
            ClarificationReason.tie
            if top_score == second_score
            else ClarificationReason.ambiguous
        )
        return _clarification(
            reason,
            [reference for reference, _score in ranked],
            scope_complete=scope_complete,
        )
    if len(ranked) == 1:
        reference = ranked[0][0]
        return EntityResolution(
            status=Status.ok,
            entity=reference,
            warnings=_archive_warnings(reference)
            + [make_warning(WarningCode.fuzzy_match)],
            scope_complete=scope_complete,
        )

    suggestions = [
        reference
        for reference, _score in rank_candidates(
            mention_text,
            fuzzy_pool,
            fuzzy_min_score=1,
        )[:MAX_OPTIONS]
    ]
    return _not_found(
        scope_complete=scope_complete,
        suggestions=suggestions,
    )


def non_entity_clarification() -> EntityResolution:
    """Aclaración sin opciones entitativas (por ejemplo, mensaje incomprensible)."""
    return EntityResolution(
        status=Status.needs_clarification,
        reason=ClarificationReason.non_entity,
        options=[],
    )


# ---------------------------------------------------------------------------
# Internos
# ---------------------------------------------------------------------------


def _company_map(rows: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    mapping: dict[int, dict[str, Any]] = {}
    for row in rows:
        company_id = many2one_id(row.get("id"))
        if company_id is not None:
            mapping[company_id] = row
    return mapping


def _company_ref(row: dict[str, Any]) -> EntityRef:
    company_id = many2one_id(row.get("id"))
    assert company_id is not None
    return EntityRef(
        id=company_id,
        name=str(row.get("name") or ""),
        type=EntityType.company,
        company_archived=not bool(row.get("active", True)),
    )


def _client_ref(
    row: dict[str, Any],
    companies: dict[int, dict[str, Any]],
) -> EntityRef | None:
    client_id = many2one_id(row.get("id"))
    company_id = many2one_id(row.get("company_id"))
    if client_id is None or company_id is None:
        return None

    company = companies.get(company_id)
    company_name = str(
        (company or {}).get("name") or row.get("company_name") or ""
    )
    archived = True if company is None else not bool(company.get("active", True))
    return EntityRef(
        id=client_id,
        name=str(row.get("name") or ""),
        type=EntityType.client,
        company_id=company_id,
        company_name=company_name,
        rif=row.get("rif"),
        company_archived=archived,
    )


def _dedupe_refs(references: Iterable[EntityRef]) -> list[EntityRef]:
    seen: set[tuple[EntityType, int]] = set()
    result: list[EntityRef] = []
    for reference in references:
        key = (reference.type, reference.id)
        if key in seen:
            continue
        seen.add(key)
        result.append(reference)
    return result


def _resolve_numeric(
    identifier: int,
    companies: list[EntityRef],
    clients: list[EntityRef],
    *,
    scope_complete: bool,
) -> EntityResolution:
    company_hit = next(
        (reference for reference in companies if reference.id == identifier),
        None,
    )
    client_hit = next(
        (reference for reference in clients if reference.id == identifier),
        None,
    )

    if company_hit is not None and client_hit is not None:
        return _clarification(
            ClarificationReason.id_collision,
            [company_hit, client_hit],
            scope_complete=scope_complete,
        )
    if company_hit is not None:
        return EntityResolution(
            status=Status.ok,
            entity=company_hit,
            warnings=_archive_warnings(company_hit),
            scope_complete=scope_complete,
        )
    if client_hit is not None:
        return EntityResolution(
            status=Status.ok,
            entity=client_hit,
            warnings=_archive_warnings(client_hit),
            scope_complete=scope_complete,
        )
    return _not_found(scope_complete=scope_complete, suggestions=[])


def _resolve_exact(
    matches: list[EntityRef],
    *,
    scope_complete: bool,
) -> EntityResolution:
    if len(matches) > 1:
        ordered = sorted(matches, key=lambda reference: reference.id)
        return _clarification(
            ClarificationReason.ambiguous,
            ordered,
            scope_complete=scope_complete,
        )
    reference = matches[0]
    return EntityResolution(
        status=Status.ok,
        entity=reference,
        warnings=_archive_warnings(reference),
        scope_complete=scope_complete,
    )


def _clarification(
    reason: ClarificationReason,
    options: list[EntityRef],
    *,
    scope_complete: bool,
) -> EntityResolution:
    return EntityResolution(
        status=Status.needs_clarification,
        reason=reason,
        options=options[:MAX_OPTIONS],
        scope_complete=scope_complete,
    )


def _archive_warnings(reference: EntityRef) -> list[Warning]:
    if not reference.company_archived:
        return []
    return [
        make_warning(
            WarningCode.company_archived,
            entity_id=reference.id,
        )
    ]


def _not_found(
    *,
    scope_complete: bool,
    suggestions: list[EntityRef],
) -> EntityResolution:
    if scope_complete:
        return EntityResolution(
            status=Status.not_found,
            options=suggestions[:MAX_OPTIONS],
            scope_complete=True,
        )
    return EntityResolution(
        status=Status.partial,
        options=suggestions[:MAX_OPTIONS],
        warnings=[make_warning(WarningCode.scope_partial)],
        scope_complete=False,
    )
