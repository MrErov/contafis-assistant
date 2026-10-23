from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from contafis_assistant.domain.contracts import Warning
from contafis_assistant.domain.enums import CorpusStatus, WarningCode, WarningSource
from contafis_assistant.rag.bm25 import Bm25Index, Bm25LoadResult, load_bm25_index
from contafis_assistant.rag.manifest import (
    ManifestState,
    allowed_document_ids,
    load_manifest,
    manifest_path_for_bm25,
    manifest_titles,
)


class CorpusState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CorpusStatus
    version: int | None = None
    manifest_status: CorpusStatus = CorpusStatus.missing
    bm25_status: CorpusStatus = CorpusStatus.missing
    chroma_status: CorpusStatus = CorpusStatus.missing
    document_count: int = 0
    collection: str | None = None
    warnings: list[Warning] = Field(default_factory=list)


def _warning(code: WarningCode, message: str) -> Warning:
    return Warning(code=code, message=message, source=WarningSource.rag)


def _combined_status(statuses: list[CorpusStatus]) -> CorpusStatus:
    if CorpusStatus.mismatch in statuses:
        return CorpusStatus.mismatch
    if CorpusStatus.missing in statuses:
        return CorpusStatus.missing
    if CorpusStatus.empty in statuses:
        return CorpusStatus.empty
    return CorpusStatus.ok


def open_chroma_collection(path: str | Path, collection_name: str) -> Any:
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError(
            "chromadb no está disponible; instale la dependencia para abrir el corpus"
        ) from exc
    client = chromadb.PersistentClient(path=str(path))
    return client.get_collection(name=collection_name)


class CorpusManager:
    def __init__(
        self,
        *,
        settings: Any | None = None,
        manifest_path: str | Path | None = None,
        bm25_path: str | Path | None = None,
        chroma_path: str | Path | None = None,
        collection_name: str | None = None,
        expected_version: int | None = None,
        expected_sha256: str | None = None,
        chroma_opener: Callable[[str | Path, str], Any] | None = None,
    ) -> None:
        if settings is not None:
            bm25_path = bm25_path or settings.bm25_index_path
            chroma_path = chroma_path or settings.chroma_path
            collection_name = collection_name or settings.chroma_collection
            expected_version = expected_version if expected_version is not None else settings.bm25_index_version

        self._bm25_path = Path(bm25_path or "data/bm25_index.json")
        self._manifest_path = (
            Path(manifest_path)
            if manifest_path is not None
            else manifest_path_for_bm25(self._bm25_path)
        )
        self._chroma_path = Path(chroma_path) if chroma_path is not None else None
        self._collection_name = collection_name or "contafis_normativa_v1"
        self._expected_version = expected_version if expected_version is not None else 1
        self._expected_sha256 = expected_sha256
        self._chroma_opener = chroma_opener or open_chroma_collection

        self._opened = False
        self._state: CorpusState | None = None
        self._manifest_state: ManifestState | None = None
        self._bm25_result: Bm25LoadResult | None = None
        self._collection: Any | None = None

    @property
    def state(self) -> CorpusState:
        return self._state or CorpusState(status=CorpusStatus.missing)

    @property
    def status(self) -> CorpusStatus:
        return self.state.status

    @property
    def version(self) -> int | None:
        return self.state.version

    @property
    def collection(self) -> Any | None:
        return self._collection

    @property
    def collection_name(self) -> str:
        return self._collection_name

    @property
    def bm25_index(self) -> Bm25Index | None:
        if self._bm25_result is None:
            return None
        return self._bm25_result.index

    @property
    def allowed_document_ids(self) -> set[str]:
        if self._manifest_state is None:
            return set()
        return allowed_document_ids(self._manifest_state)

    @property
    def manifest_titles(self) -> dict[str, str]:
        if self._manifest_state is None:
            return {}
        return manifest_titles(self._manifest_state)

    def _open_chroma(self) -> tuple[CorpusStatus, Any | None, str | None]:
        if self._chroma_path is None:
            return CorpusStatus.missing, None, "ruta de Chroma no configurada"
        try:
            collection = self._chroma_opener(self._chroma_path, self._collection_name)
        except Exception as exc:
            return CorpusStatus.missing, None, str(exc)
        if collection is None:
            return CorpusStatus.missing, None, "colección Chroma ausente"
        try:
            count = int(collection.count())
        except Exception as exc:
            return CorpusStatus.missing, None, str(exc)
        if count <= 0:
            return CorpusStatus.empty, collection, "colección Chroma vacía"
        return CorpusStatus.ok, collection, None

    def open(self) -> CorpusState:
        if self._opened:
            return self.state
        self._opened = True

        self._manifest_state = load_manifest(self._manifest_path)
        manifest = self._manifest_state.manifest

        expected_version = (
            manifest.bm25_index_version if manifest is not None else self._expected_version
        )
        expected_sha256 = (
            manifest.bm25_index_sha256 if manifest is not None else self._expected_sha256
        )
        self._bm25_result = load_bm25_index(
            self._bm25_path,
            expected_version=expected_version,
            expected_sha256=expected_sha256,
        )

        chroma_status, collection, chroma_error = self._open_chroma()
        self._collection = collection

        statuses = [
            self._manifest_state.status,
            self._bm25_result.status,
            chroma_status,
        ]
        warnings: list[Warning] = list(self._manifest_state.warnings)
        if self._bm25_result.status != CorpusStatus.ok:
            warnings.append(_warning(WarningCode.corpus_unavailable, "El índice BM25 no está disponible."))
        if chroma_status != CorpusStatus.ok:
            warnings.append(
                _warning(WarningCode.corpus_unavailable, chroma_error or "La colección Chroma no está disponible.")
            )

        deduped: list[Warning] = []
        seen: set[WarningCode] = set()
        for warning in warnings:
            if warning.code in seen:
                continue
            seen.add(warning.code)
            deduped.append(warning)

        version = self._manifest_state.version
        if version is None and self._bm25_result is not None:
            version = self._bm25_result.version

        document_count = (
            len(manifest.documents)
            if manifest is not None
            else (self._bm25_result.index.n_docs if self._bm25_result.index is not None else 0)
        )

        self._state = CorpusState(
            status=_combined_status(statuses),
            version=version,
            manifest_status=self._manifest_state.status,
            bm25_status=self._bm25_result.status,
            chroma_status=chroma_status,
            document_count=document_count,
            collection=self._collection_name,
            warnings=deduped,
        )
        return self._state

    def close(self) -> None:
        self._collection = None
