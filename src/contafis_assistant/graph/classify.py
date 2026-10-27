"""Clasificación tipada con LLM inyectable y fallo degradado controlado."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.enums import Intent, Status, WarningCode, WarningSource
from contafis_assistant.domain.contracts import Warning
from contafis_assistant.graph.state import Slots


class ClassifierLLM(Protocol):
    def classify(self, message: str) -> Any: ...


class ClassificationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    slots: Slots = Field(default_factory=Slots)
    entity_mentions: list[str] = Field(default_factory=list)
    out_of_scope_components: list[str] = Field(default_factory=list)


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: ClassificationDraft | None = None
    status: Status | None = None
    warnings: list[Warning] = Field(default_factory=list)
    out_of_scope_all: bool = False
    failed: bool = False


def _warning(code: WarningCode, message: str) -> Warning:
    return Warning(code=code, message=message, source=WarningSource.system)


def _invoke(llm: Any, message: str) -> Any:
    if callable(llm):
        return llm(message)
    if hasattr(llm, "classify"):
        return llm.classify(message)
    if hasattr(llm, "invoke"):
        return llm.invoke(message)
    raise TypeError("el clasificador no expone classify ni invoke")


def _failure(message: str) -> ClassificationResult:
    return ClassificationResult(
        status=Status.upstream_error,
        warnings=[
            _warning(
                WarningCode.source_unavailable,
                f"No se pudo clasificar la consulta: {message}",
            )
        ],
        failed=True,
    )


def classify(message: str, *, llm: Any) -> ClassificationResult:
    try:
        raw = _invoke(llm, message)
        draft = (
            raw
            if isinstance(raw, ClassificationDraft)
            else ClassificationDraft.model_validate(raw)
        )
    except Exception as exc:
        # Log the actual error for debugging
        import logging
        logging.getLogger(__name__).error("Classifier LLM call failed: %s", exc, exc_info=True)
        return _failure(str(exc))

    warnings: list[Warning] = []
    out_of_scope_all = draft.intent is Intent.fuera_de_alcance

    if draft.out_of_scope_components and not out_of_scope_all:
        warnings.append(
            _warning(
                WarningCode.out_of_scope_component,
                "Parte de la consulta está fuera del alcance del MVP.",
            )
        )

    return ClassificationResult(
        classification=draft,
        warnings=warnings,
        out_of_scope_all=out_of_scope_all,
    )


__all__ = [
    "ClassificationDraft",
    "ClassificationResult",
    "ClassifierLLM",
    "Slots",
    "classify",
]
