from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.contracts import Source, Warning
from contafis_assistant.domain.enums import CorpusStatus, WarningCode, WarningSource
from contafis_assistant.rag.bm25 import Bm25Hit
from contafis_assistant.rag.fusion import DenseHit, fuse_results


class RagResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[Source] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
    corpus_status: CorpusStatus


def _warning(code: WarningCode, message: str) -> Warning:
    return Warning(code=code, message=message, source=WarningSource.rag)


def _dedupe(warnings: Sequence[Warning]) -> list[Warning]:
    result: list[Warning] = []
    seen: set[WarningCode] = set()
    for warning in warnings:
        if warning.code in seen:
            continue
        seen.add(warning.code)
        result.append(warning)
    return result


def _first(value: Any) -> list[Any]:
    if isinstance(value, list) and value and isinstance(value[0], list):
        return value[0]
    if isinstance(value, list):
        return value
    return []


class RagRetriever:
    def __init__(
        self,
        corpus: Any,
        *,
        dense_search: Callable[[str, int], list[DenseHit]] | None = None,
        embedder: Callable[[list[str]], list[list[float]]] | None = None,
        dense_top_k: int = 10,
        bm25_top_k: int = 10,
        final_top_k: int = 5,
        rrf_k: int = 60,
        dense_min_score: float = 0.30,
        bm25_min_score: float = 1.0,
    ) -> None:
        self.corpus = corpus
        self._dense_search = dense_search
        self._embedder = embedder
        self.dense_top_k = dense_top_k
        self.bm25_top_k = bm25_top_k
        self.final_top_k = final_top_k
        self.rrf_k = rrf_k
        self.dense_min_score = dense_min_score
        self.bm25_min_score = bm25_min_score

    def _corpus_status(self) -> CorpusStatus:
        return getattr(self.corpus, "status", CorpusStatus.missing)

    def _dense(self, query: str) -> tuple[list[DenseHit], list[Warning]]:
        if self._dense_search is not None:
            return list(self._dense_search(query, self.dense_top_k)), []

        collection = getattr(self.corpus, "collection", None)
        if collection is None or self._embedder is None:
            return [], []

        try:
            vectors = self._embedder([query])
            embedding = vectors[0]
            raw = collection.query(
                query_embeddings=[embedding],
                n_results=self.dense_top_k,
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            return [], [
                _warning(WarningCode.source_unavailable, "La búsqueda dense no está disponible.")
            ]

        ids = _first(raw.get("ids"))
        documents = _first(raw.get("documents"))
        metadatas = _first(raw.get("metadatas"))
        distances = _first(raw.get("distances"))

        hits: list[DenseHit] = []
        for index, chunk_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            metadata = metadata if isinstance(metadata, dict) else {}
            document_id = metadata.get("document_id")
            if not isinstance(document_id, str) or not document_id:
                continue
            distance = distances[index] if index < len(distances) else None
            if distance is None:
                continue
            text = documents[index] if index < len(documents) else ""
            hits.append(
                DenseHit(
                    chunk_id=str(chunk_id),
                    document_id=document_id,
                    distance=float(distance),
                    text=text or "",
                    metadata=metadata,
                )
            )
        return hits, []

    def _bm25(self, query: str) -> tuple[list[Bm25Hit], list[Warning]]:
        index = getattr(self.corpus, "bm25_index", None)
        if index is None:
            return [], []
        try:
            return list(index.search(query, top_k=self.bm25_top_k)), []
        except Exception:
            return [], [
                _warning(WarningCode.source_unavailable, "La búsqueda BM25 no está disponible.")
            ]

    def retrieve(self, query: str) -> RagResult:
        status = self._corpus_status()
        if status != CorpusStatus.ok:
            warnings = [
                _warning(WarningCode.corpus_unavailable, "El corpus normativo no está disponible.")
            ]
            if status == CorpusStatus.mismatch:
                warnings.append(
                    _warning(WarningCode.manifest_mismatch, "El corpus no coincide con el manifiesto.")
                )
            return RagResult(sources=[], warnings=warnings, corpus_status=status)

        dense_hits, dense_warnings = self._dense(query)
        bm25_hits, bm25_warnings = self._bm25(query)

        fusion = fuse_results(
            dense_hits,
            bm25_hits,
            dense_min_score=self.dense_min_score,
            bm25_min_score=self.bm25_min_score,
            rrf_k=self.rrf_k,
            final_top_k=self.final_top_k,
            collection=getattr(self.corpus, "collection_name", "contafis_normativa_v1"),
            allowed_document_ids=getattr(self.corpus, "allowed_document_ids", None),
            document_titles=getattr(self.corpus, "manifest_titles", None),
        )

        return RagResult(
            sources=fusion.sources,
            warnings=_dedupe([*dense_warnings, *bm25_warnings, *fusion.warnings]),
            corpus_status=status,
        )
