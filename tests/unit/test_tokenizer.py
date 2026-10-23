from __future__ import annotations

import pytest

from contafis_assistant.rag.tokenizer import normalize_text, tokenize


def test_lowercases_removes_accents_and_collapses_whitespace():
    assert normalize_text("  Régimen   ESPECIAL\tde IVA ") == "regimen especial de iva"
    assert tokenize("  RÉGIMEN   especial ") == ["regimen", "especial"]


def test_acronyms_are_preserved_as_single_tokens():
    assert tokenize("iva islr igtf rif") == ["IVA", "ISLR", "IGTF", "RIF"]
    assert tokenize("I.V.A.") == ["i", "v", "a"]


@pytest.mark.parametrize(
    ("acronym", "phrase"),
    [
        ("ISLR", "Impuesto sobre la Renta"),
        ("IVA", "Impuesto al Valor Agregado"),
        ("IGTF", "Impuesto a las Grandes Transacciones Financieras"),
        ("RIF", "Registro de Información Fiscal"),
    ],
)
def test_alias_map_is_bidirectional_for_curated_acronyms(acronym, phrase):
    acronym_tokens = set(tokenize(acronym))
    phrase_tokens = set(tokenize(phrase))

    assert acronym in acronym_tokens
    assert acronym in phrase_tokens
    assert acronym_tokens & phrase_tokens == {acronym}


def test_index_and_query_use_the_same_tokenizer():
    document_text = "  Impuesto   sobre la RENTA "
    query_text = "impuesto sobre la renta"

    assert tokenize(document_text) == tokenize(query_text)
    assert "ISLR" in tokenize(document_text)


def test_alias_map_is_curated_and_does_not_expand_other_acronyms():
    assert tokenize("SENIAT") == ["seniat"]
    assert tokenize("SUNDDE") == ["sundde"]
    assert "ISLR" not in tokenize("SENIAT")


def test_punctuation_and_accents_do_not_change_terms():
    assert tokenize("¿IVA?") == ["IVA"]
    assert tokenize("Régimen; Especial.") == ["regimen", "especial"]


def test_empty_text_has_no_tokens():
    assert tokenize("   ") == []
    assert normalize_text("   ") == ""
