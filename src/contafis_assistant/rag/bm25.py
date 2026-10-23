from __future__ import annotations

import json
import math
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contafis_assistant.domain.enums import CorpusStatus
from contafis_assistant.rag.tokenizer import tokenize


class Bm25Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Bm25Hit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    document_id: str
    score: float = Field(ge=0)
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class Bm25Index(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    k1: float = 1.5
    b: float = 0.75
    documents: list[Bm25Document] = Field(default_factory=list)
    df: dict[str, int] = Field(default_factory=dict)
    n_docs: int = 0
    avgdl: float = 0.0
    collection: str | None = None
    sha256: str | None = None

    @classmethod
    def build(
        cls,
        documents: list[Bm25Document],
        *,
        version: int,
        collection: str | None = None,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> "Bm25Index":
        frequencies = [Counter(tokenize(document.text)) for document in documents]
        document_frequency: Counter[str] = Counter()
        for frequency in frequencies:
            document_frequency.update(frequency.keys())
        lengths = [sum(frequency.values()) for frequency in frequencies]
        n_docs = len(documents)
        index = cls(
            version=version,
            k1=k1,
            b=b,
            documents=list(documents),
            df=dict(sorted(document_frequency.items())),
            n_docs=n_docs,
            avgdl=(sum(lengths) / n_docs) if n_docs else 0.0,
            collection=collection,
        )
        index.sha256 = index.compute_sha256()
        return index

    def _payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "k1": self.k1,
            "b": self.b,
            "n_docs": self.n_docs,
            "avgdl": self.avgdl,
            "collection": self.collection,
            "documents": [document.model_dump(mode="json") for document in self.documents],
            "df": dict(sorted(self.df.items())),
        }

    def compute_sha256(self) -> str:
        canonical = json.dumps(
            self._payload(),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        payload = self._payload()
        payload["sha256"] = self.compute_sha256()
        return payload

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_payload(), sort_keys=True, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def search(self, query: str, top_k: int = 10) -> list[Bm25Hit]:
        query_tokens = tokenize(query)
        if not query_tokens or top_k <= 0 or not self.documents:
            return []

        hits: list[Bm25Hit] = []
        for document in self.documents:
            frequency = Counter(tokenize(document.text))
            length = sum(frequency.values())
            score = 0.0
            for token in set(query_tokens):
                term_frequency = frequency.get(token, 0)
                if term_frequency == 0:
                    continue
                document_frequency = self.df.get(token, 0)
                if document_frequency == 0:
                    continue
                idf = math.log(
                    1 + (self.n_docs - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                denominator = term_frequency + self.k1 * (
                    1 - self.b + self.b * (length / self.avgdl if self.avgdl else 0.0)
                )
                score += idf * (term_frequency * (self.k1 + 1)) / denominator
            hits.append(
                Bm25Hit(
                    chunk_id=document.chunk_id,
                    document_id=document.document_id,
                    score=max(score, 0.0),
                    text=document.text,
                    metadata=dict(document.metadata),
                )
            )

        hits.sort(key=lambda hit: (-hit.score, hit.chunk_id))
        return hits[:top_k]


class Bm25LoadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CorpusStatus
    index: Bm25Index | None = None
    version: int | None = None
    sha256: str | None = None
    error: str | None = None


def load_bm25_index(
    path: str | Path,
    *,
    expected_version: int | None = None,
    expected_sha256: str | None = None,
) -> Bm25LoadResult:
    target = Path(path)

    if not target.is_file():
        return Bm25LoadResult(status=CorpusStatus.missing, error="índice BM25 ausente")

    try:
        if target.stat().st_size == 0:
            return Bm25LoadResult(status=CorpusStatus.empty, error="índice BM25 vacío")
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        return Bm25LoadResult(status=CorpusStatus.mismatch, error=str(exc))

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return Bm25LoadResult(status=CorpusStatus.mismatch, error=str(exc))

    if not isinstance(payload, dict):
        return Bm25LoadResult(status=CorpusStatus.mismatch, error="índice BM25 inválido")

    stored_sha = payload.pop("sha256", None)
    if not isinstance(stored_sha, str):
        return Bm25LoadResult(status=CorpusStatus.mismatch, error="índice BM25 sin hash")

    try:
        index = Bm25Index.model_validate(payload)
    except ValidationError as exc:
        return Bm25LoadResult(status=CorpusStatus.mismatch, error=str(exc))

    computed_sha = index.compute_sha256()
    if computed_sha != stored_sha:
        return Bm25LoadResult(status=CorpusStatus.mismatch, error="hash BM25 corrupto")
    index.sha256 = computed_sha

    if expected_version is not None and index.version != expected_version:
        return Bm25LoadResult(
            status=CorpusStatus.mismatch,
            version=index.version,
            sha256=computed_sha,
            error="versión BM25 distinta",
        )

    if expected_sha256 is not None and computed_sha != expected_sha256:
        return Bm25LoadResult(
            status=CorpusStatus.mismatch,
            version=index.version,
            sha256=computed_sha,
            error="hash BM25 distinto",
        )

    if not index.documents:
        return Bm25LoadResult(
            status=CorpusStatus.empty,
            version=index.version,
            sha256=computed_sha,
            error="índice BM25 sin documentos",
        )

    return Bm25LoadResult(
        status=CorpusStatus.ok,
        index=index,
        version=index.version,
        sha256=computed_sha,
    )
