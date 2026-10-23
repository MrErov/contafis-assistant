from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.contracts import Source, Warning
from contafis_assistant.domain.enums import WarningCode, WarningSource
from contafis_assistant.rag.bm25 import Bm25Hit


class DenseHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    distance: float
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class FusionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[Source] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
    has_signal: bool = False
    dense_candidates: int = 0
    bm25_candidates: int = 0


def cosine_similarity(distance: float) -> float:
    return 1.0 - distance


def _warning(code: WarningCode, message: str) -> Warning:
    return Warning(code=code, message=message, source=WarningSource.rag)


def _page(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed >= 1 else None
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def fuse_results(
    dense_hits: Sequence[DenseHit],
    bm25_hits: Sequence[Bm25Hit],
    *,
    dense_min_score: float = 0.30,
    bm25_min_score: float = 1.0,
    rrf_k: int = 60,
    final_top_k: int = 5,
    collection: str = "contafis_normativa_v1",
    allowed_document_ids: set[str] | None = None,
    document_titles: Mapping[str, str] | None = None,
) -> FusionResult:
    def _allowed(document_id: str) -> bool:
        return allowed_document_ids is None or document_id in allowed_document_ids

    dense_signal = [
        hit
        for hit in dense_hits
        if _allowed(hit.document_id)
        and cosine_similarity(hit.distance) >= dense_min_score
    ]
    bm25_signal = [
        hit
        for hit in bm25_hits
        if _allowed(hit.document_id) and hit.score >= bm25_min_score
    ]

    if not dense_signal and not bm25_signal:
        return FusionResult(
            sources=[],
            warnings=[_warning(WarningCode.retrieval_low_score, "No se encontraron fuentes normativas con señal suficiente.")],
            has_signal=False,
            dense_candidates=0,
            bm25_candidates=0,
        )

    scores: dict[str, float] = {}
    entries: dict[str, DenseHit | Bm25Hit] = {}

    for rank, hit in enumerate(dense_signal, start=1):
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
        entries[hit.chunk_id] = hit

    for rank, hit in enumerate(bm25_signal, start=1):
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
        if hit.chunk_id not in entries:
            entries[hit.chunk_id] = hit

    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:final_top_k]

    sources: list[Source] = []
    page_missing = False
    titles = document_titles or {}

    for chunk_id, score in ordered:
        entry = entries[chunk_id]
        metadata = dict(entry.metadata)
        document_id = entry.document_id
        page = _page(metadata.get("page"))
        if page is None:
            page_missing = True
        collection_name = _text(metadata.get("collection")) or collection
        sources.append(
            Source(
                document_id=document_id,
                document=titles.get(document_id) or _text(metadata.get("document")) or document_id,
                page=page,
                section=_text(metadata.get("section")),
                locator=_text(metadata.get("locator")),
                chunk_id=chunk_id,
                score=score,
                collection=collection_name,
            )
        )

    warnings: list[Warning] = []
    if page_missing:
        warnings.append(
            _warning(WarningCode.page_metadata_missing, "Faltan metadatos de página en algunas fuentes.")
        )

    return FusionResult(
        sources=sources,
        warnings=warnings,
        has_signal=True,
        dense_candidates=len(dense_signal),
        bm25_candidates=len(bm25_signal),
    )
