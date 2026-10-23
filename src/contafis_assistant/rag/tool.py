from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.contracts import Source, ToolResult, Warning
from contafis_assistant.domain.enums import (
    CorpusStatus,
    Intent,
    Status,
    WarningCode,
    WarningSource,
)


class NormativeToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status
    sources: list[Source] = Field(default_factory=list)
    data: list[ToolResult] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=lambda: ["query_normativa"])


def _warning(code: WarningCode, message: str) -> Warning:
    return Warning(code=code, message=message, source=WarningSource.rag)


def _ensure(warnings: list[Warning], code: WarningCode, message: str) -> None:
    if any(warning.code == code for warning in warnings):
        return
    warnings.append(_warning(code, message))


def query_normativa(
    question: str,
    *,
    retriever: Any,
    intent: Intent = Intent.normativa,
    erp_data: Sequence[ToolResult] | None = None,
) -> NormativeToolResult:
    data = list(erp_data or [])
    rag = retriever.retrieve(question)
    warnings = list(rag.warnings)
    status = rag.corpus_status
    mixed_with_erp = intent == Intent.mixta and bool(data)

    if status != CorpusStatus.ok:
        if mixed_with_erp:
            _ensure(warnings, WarningCode.source_unavailable, "La fuente normativa no está disponible.")
            if status == CorpusStatus.mismatch:
                _ensure(warnings, WarningCode.manifest_mismatch, "El corpus no coincide con el manifiesto.")
            final_status = Status.partial
        else:
            _ensure(warnings, WarningCode.corpus_unavailable, "El corpus normativo no está disponible.")
            if status == CorpusStatus.mismatch:
                _ensure(warnings, WarningCode.manifest_mismatch, "El corpus no coincide con el manifiesto.")
            final_status = Status.upstream_error
        return NormativeToolResult(
            status=final_status,
            sources=list(rag.sources),
            data=data,
            warnings=warnings,
        )

    if not rag.sources:
        _ensure(warnings, WarningCode.retrieval_low_score, "No hubo señal suficiente en el corpus normativo.")
        final_status = Status.partial if mixed_with_erp else Status.insufficient_context
        return NormativeToolResult(
            status=final_status,
            sources=[],
            data=data,
            warnings=warnings,
        )

    return NormativeToolResult(
        status=Status.ok,
        sources=list(rag.sources),
        data=data,
        warnings=warnings,
    )
