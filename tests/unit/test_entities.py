"""Resolución de entidades: normalización, precedencia, fuzzy y clarificación (T03)."""

from __future__ import annotations

from copy import deepcopy

from contafis_assistant.domain.contracts import EntityRef
from contafis_assistant.domain.enums import EntityType, Status, WarningCode
from contafis_assistant.odoo.tools.entities import (
    ClarificationReason,
    non_entity_clarification,
    normalize_entity_name,
    rank_candidates,
    resolve_entity,
)

ALLOWED = [1, 2]


class FakeGateway:
    """Doble in-process del gateway Odoo; registra RPC y no usa red."""

    def __init__(self, companies=None, clients=None):
        self.companies = list(companies or [])
        self.clients = list(clients or [])
        self.calls = []

    def execute(self, call, **_kwargs):
        self.calls.append(call)
        if call.model == "res.company":
            return deepcopy(self.companies)
        if call.model == "l10n.ve.compliance.client":
            return deepcopy(self.clients)
        raise AssertionError(f"modelo inesperado: {call.model}")


def company(record_id, name, *, active=True):
    return {"id": record_id, "name": name, "active": active}


def client(record_id, name, *, company_id=1, company_name="Compañía Uno", rif=None):
    return {
        "id": record_id,
        "name": name,
        "rif": rif,
        "company_id": company_id,
        "company_name": company_name,
    }


def resolve(gateway, mention, *, scope_complete=True):
    return resolve_entity(
        gateway,
        mention,
        allowed_company_ids=ALLOWED,
        scope_complete=scope_complete,
    )


# ---------------------------------------------------------------------------
# Normalización y puntuación
# ---------------------------------------------------------------------------


def test_normalization_handles_accents_case_and_spaces():
    assert normalize_entity_name("  DISTRIBUIDORA   El   Sol  ") == "distribuidora el sol"
    assert normalize_entity_name("Compañía Ñandú") == "compania nandu"
    assert normalize_entity_name("COMERCIAL\tEL SOL") == "comercial el sol"


def test_rank_candidates_is_score_desc_then_id_and_thresholded():
    candidates = [
        EntityRef(id=5, name="Distribuidora El Sol", type=EntityType.company),
        EntityRef(id=2, name="Distribuidora del Norte", type=EntityType.company),
        EntityRef(id=9, name="XYZ", type=EntityType.company),
    ]

    ranked = rank_candidates("distribuidora del sol", candidates, fuzzy_min_score=85)

    assert [candidate.id for candidate, _score in ranked] == [5, 2]
    assert ranked[0][1] >= ranked[1][1] >= 85
    assert all(score >= 85 for _candidate, score in ranked)


# ---------------------------------------------------------------------------
# Precedencia exacta y fuzzy
# ---------------------------------------------------------------------------


def test_company_exact_beats_client_exact():
    gateway = FakeGateway(
        companies=[company(1, "El Sol")],
        clients=[client(10, "El Sol")],
    )

    resolution = resolve(gateway, "  el   SOL ")

    assert resolution.status is Status.ok
    assert resolution.entity is not None
    assert resolution.entity.type is EntityType.company
    assert resolution.entity.id == 1


def test_client_exact_beats_fuzzy():
    gateway = FakeGateway(
        companies=[company(1, "El Sol Express")],
        clients=[client(10, "El Sol")],
    )

    resolution = resolve(gateway, "el sol")

    assert resolution.status is Status.ok
    assert resolution.entity is not None
    assert resolution.entity.type is EntityType.client
    assert resolution.entity.id == 10
    assert resolution.entity.company_id == 1
    assert resolution.entity.company_name == "El Sol Express"


def test_fuzzy_match_uses_threshold_and_emits_warning():
    gateway = FakeGateway(
        companies=[company(1, "Distribuidora El Sol")],
        clients=[],
    )

    resolution = resolve(gateway, "comercial sol nrt")

    # La mención no coincide de forma exacta con ningún nombre.
    assert resolution.status is Status.not_found

    gateway = FakeGateway(
        companies=[company(1, "Comercial Sol Norte")],
        clients=[],
    )
    resolution = resolve(gateway, "comercial sol nrt")

    assert resolution.status is Status.ok
    assert resolution.entity is not None
    assert resolution.entity.id == 1
    assert WarningCode.fuzzy_match in {warning.code for warning in resolution.warnings}


def test_fuzzy_tie_requires_clarification_with_options_ordered_by_id():
    gateway = FakeGateway(
        companies=[
            company(9, "Comercial Sol Sur"),
            company(3, "Comercial Sol Norte"),
        ],
        clients=[],
    )

    resolution = resolve(gateway, "comercial sol")

    assert resolution.status is Status.needs_clarification
    assert resolution.reason is ClarificationReason.tie
    assert [option.id for option in resolution.options] == [3, 9]
    assert all(isinstance(option, EntityRef) for option in resolution.options)


def test_more_than_five_matches_clarifies_with_five_options():
    companies = [company(index, f"Comercial Sol {index}") for index in range(1, 8)]
    gateway = FakeGateway(companies=companies, clients=[])

    resolution = resolve(gateway, "comercial sol")

    assert resolution.status is Status.needs_clarification
    assert resolution.reason is ClarificationReason.too_many
    assert len(resolution.options) == 5
    assert [option.id for option in resolution.options] == [1, 2, 3, 4, 5]


def test_two_clients_with_same_name_in_different_companies_clarify():
    gateway = FakeGateway(
        companies=[
            company(1, "Compañía Uno"),
            company(2, "Compañía Dos"),
        ],
        clients=[
            client(10, "El Sol", company_id=1, company_name="Compañía Uno"),
            client(20, "El Sol", company_id=2, company_name="Compañía Dos"),
        ],
    )

    resolution = resolve(gateway, "el sol")

    assert resolution.status is Status.needs_clarification
    assert resolution.reason is ClarificationReason.ambiguous
    assert len(resolution.options) == 2
    assert all(option.type is EntityType.client for option in resolution.options)
    assert {option.company_id for option in resolution.options} == {1, 2}


def test_id_collision_between_company_and_client_clarifies():
    gateway = FakeGateway(
        companies=[company(10, "Compañía Diez")],
        clients=[client(10, "Cliente Diez")],
    )

    resolution = resolve(gateway, "10")

    assert resolution.status is Status.needs_clarification
    assert resolution.reason is ClarificationReason.id_collision
    assert {option.type for option in resolution.options} == {
        EntityType.company,
        EntityType.client,
    }


def test_numeric_id_resolves_unique_company():
    gateway = FakeGateway(companies=[company(10, "Compañía Diez")], clients=[])

    resolution = resolve(gateway, "10")

    assert resolution.status is Status.ok
    assert resolution.entity is not None
    assert resolution.entity.id == 10
    assert resolution.entity.type is EntityType.company


# ---------------------------------------------------------------------------
# Compañías y clientes archivados
# ---------------------------------------------------------------------------


def test_exact_archived_company_is_ok_with_warning():
    gateway = FakeGateway(
        companies=[company(1, "Archivada C.A.", active=False)],
        clients=[],
    )

    resolution = resolve(gateway, "archivada c.a.")

    assert resolution.status is Status.ok
    assert resolution.entity is not None
    assert resolution.entity.company_archived is True
    assert WarningCode.company_archived in {
        warning.code for warning in resolution.warnings
    }


def test_client_of_archived_company_is_ok_with_warning():
    gateway = FakeGateway(
        companies=[company(1, "Archivada C.A.", active=False)],
        clients=[
            client(
                10,
                "Cliente Archivado",
                company_id=1,
                company_name="Archivada C.A.",
            )
        ],
    )

    resolution = resolve(gateway, "cliente archivado")

    assert resolution.status is Status.ok
    assert resolution.entity is not None
    assert resolution.entity.type is EntityType.client
    assert resolution.entity.company_archived is True
    assert WarningCode.company_archived in {
        warning.code for warning in resolution.warnings
    }


def test_archived_companies_are_excluded_from_fuzzy():
    gateway = FakeGateway(
        companies=[company(1, "Archivada C.A.", active=False)],
        clients=[],
    )

    resolution = resolve(gateway, "archivad c.a.")

    assert resolution.status is Status.not_found
    assert all(option.company_archived for option in resolution.options)


# ---------------------------------------------------------------------------
# Inexistente, alcance y opciones
# ---------------------------------------------------------------------------


def test_inexistent_with_complete_scope_is_not_found_with_suggestions():
    gateway = FakeGateway(
        companies=[company(1, "Distribuidora El Sol")],
        clients=[],
    )

    resolution = resolve(gateway, "distribuidora norte", scope_complete=True)

    assert resolution.status is Status.not_found
    assert len(resolution.options) <= 5
    assert all(isinstance(option, EntityRef) for option in resolution.options)
    assert resolution.scope_complete is True


def test_inexistent_with_incomplete_scope_is_partial_with_scope_warning():
    gateway = FakeGateway(companies=[], clients=[])

    resolution = resolve(gateway, "xyz", scope_complete=False)

    assert resolution.status is Status.partial
    assert WarningCode.scope_partial in {
        warning.code for warning in resolution.warnings
    }
    assert resolution.options == []


def test_clarification_options_are_only_entity_refs_without_payload_or_sources():
    gateway = FakeGateway(
        companies=[company(1, "El Sol"), company(2, "El Sol")],
        clients=[],
    )

    resolution = resolve(gateway, "el sol")

    assert resolution.status is Status.needs_clarification
    for option in resolution.options:
        assert isinstance(option, EntityRef)
        dumped = option.model_dump()
        assert "items" not in dumped
        assert "sources" not in dumped
        assert "data" not in dumped


def test_non_entity_clarification_has_no_options():
    resolution = non_entity_clarification()

    assert resolution.status is Status.needs_clarification
    assert resolution.reason is ClarificationReason.non_entity
    assert resolution.options == []
    assert resolution.entity is None
