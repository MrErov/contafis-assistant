from __future__ import annotations

import pytest

from contafis_assistant.domain.enums import Intent, Status, WarningCode
from contafis_assistant.graph.classify import (
    ClassificationDraft,
    ClassificationResult,
    Slots,
    classify,
)


class FakeLLM:
    def __init__(self, result):
        self.result = result
        self.messages: list[str] = []

    def classify(self, message: str):
        self.messages.append(message)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _codes(warnings) -> set[WarningCode]:
    return {warning.code for warning in warnings}


@pytest.mark.parametrize(
    "intent",
    [
        Intent.erp,
        Intent.normativa,
        Intent.mixta,
        Intent.conversacional,
        Intent.fuera_de_alcance,
    ],
)
def test_classifier_returns_each_supported_intent(intent):
    draft = ClassificationDraft(
        intent=intent,
        slots=Slots(topic="obligations"),
    )
    llm = FakeLLM(draft)

    result = classify("consulta", llm=llm)

    assert isinstance(result, ClassificationResult)
    assert result.status is None
    assert result.failed is False
    assert result.classification is not None
    assert result.classification.intent == intent
    assert result.out_of_scope_all is (intent is Intent.fuera_de_alcance)
    assert llm.messages == ["consulta"]


def test_out_of_scope_component_emits_warning_without_cancelling_query():
    draft = ClassificationDraft(
        intent=Intent.mixta,
        slots=Slots(topic="obligations"),
        out_of_scope_components=["liquidaciones", "xml_id"],
    )

    result = classify("IVA y liquidaciones", llm=FakeLLM(draft))

    assert result.status is None
    assert result.classification is not None
    assert WarningCode.out_of_scope_component in _codes(result.warnings)
    assert result.out_of_scope_all is False


def test_classifier_output_is_pydantic_and_json_serializable():
    draft = ClassificationDraft(
        intent=Intent.erp,
        slots=Slots(topic="clients", tax_type="iva", states=["overdue"]),
        entity_mentions=["Acme"],
    )

    result = classify("clientes vencidos de Acme", llm=FakeLLM(draft))

    assert isinstance(result.classification, ClassificationDraft)
    assert isinstance(result.classification.slots, Slots)
    payload = result.classification.model_dump(mode="json")
    assert payload["intent"] == "erp"
    assert payload["slots"]["topic"] == "clients"


def test_raw_dict_is_validated_into_pydantic_output():
    result = classify(
        "consulta",
        llm=FakeLLM(
            {
                "intent": "normativa",
                "slots": {"topic": None, "query": "alícuota de IVA"},
            }
        ),
    )

    assert result.status is None
    assert result.classification is not None
    assert result.classification.intent is Intent.normativa
    assert result.classification.slots.query == "alícuota de IVA"


def test_invalid_structured_output_is_upstream_error():
    result = classify(
        "consulta",
        llm=FakeLLM(
            {
                "intent": "erp",
                "slots": {
                    "topic": "obligations",
                    "domain": [["state", "=", "pending"]],
                },
            }
        ),
    )

    assert result.status == Status.upstream_error
    assert result.failed is True
    assert result.classification is None
    assert result.warnings


def test_llm_failure_is_upstream_error_without_tools():
    result = classify("consulta", llm=FakeLLM(RuntimeError("llm caído")))

    assert result.status == Status.upstream_error
    assert result.failed is True
    assert result.classification is None
    assert result.warnings
