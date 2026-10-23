from __future__ import annotations

import json
from datetime import date
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contafis_assistant.domain.contracts import Warning
from contafis_assistant.domain.enums import CorpusStatus, WarningCode, WarningSource

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ManifestDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    version: str = Field(min_length=1)
    file: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    pages: int = Field(ge=1)
    published_at: date
    language: str = Field(min_length=1)
    origin: str = Field(min_length=1)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bm25_index_version: int = Field(ge=1)
    bm25_index_sha256: str = Field(pattern=SHA256_PATTERN)
    documents: list[ManifestDocument] = Field(default_factory=list)


class ManifestState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CorpusStatus
    manifest: Manifest | None = None
    version: int | None = None
    sha256: str | None = None
    error: str | None = None
    warnings: list[Warning] = Field(default_factory=list)


def manifest_path_for_bm25(bm25_index_path: str | Path) -> Path:
    return Path(bm25_index_path).parent / "manifest.json"


def _warning(code: WarningCode, message: str) -> Warning:
    return Warning(code=code, message=message, source=WarningSource.rag)


def _state(
    status: CorpusStatus,
    *,
    manifest: Manifest | None = None,
    error: str | None = None,
    warnings: list[Warning] | None = None,
) -> ManifestState:
    return ManifestState(
        status=status,
        manifest=manifest,
        version=manifest.bm25_index_version if manifest else None,
        sha256=manifest.bm25_index_sha256 if manifest else None,
        error=error,
        warnings=warnings or [],
    )


def _file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load_manifest(
    manifest_path: str | Path,
    *,
    bm25_version: int | None = None,
    bm25_sha256: str | None = None,
) -> ManifestState:
    path = Path(manifest_path)

    if not path.is_file():
        return _state(
            CorpusStatus.missing,
            error="manifest.json no encontrado",
            warnings=[_warning(WarningCode.corpus_unavailable, "No se encontró el manifiesto del corpus.")],
        )

    try:
        if path.stat().st_size == 0:
            return _state(
                CorpusStatus.empty,
                error="manifest.json vacío",
                warnings=[_warning(WarningCode.corpus_unavailable, "El manifiesto del corpus está vacío.")],
            )
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _state(CorpusStatus.mismatch, error=str(exc), warnings=[_warning(WarningCode.manifest_mismatch, "No se pudo leer el manifiesto del corpus.")])

    if not raw.strip():
        return _state(
            CorpusStatus.empty,
            error="manifest.json vacío",
            warnings=[_warning(WarningCode.corpus_unavailable, "El manifiesto del corpus está vacío.")],
        )

    try:
        payload = json.loads(raw)
        manifest = Manifest.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        return _state(
            CorpusStatus.mismatch,
            error=str(exc),
            warnings=[_warning(WarningCode.manifest_mismatch, "El manifiesto del corpus no es válido.")],
        )

    if not manifest.documents:
        return _state(
            CorpusStatus.empty,
            error="manifest.json sin documentos",
            warnings=[_warning(WarningCode.corpus_unavailable, "El manifiesto no declara documentos.")],
        )

    for document in manifest.documents:
        document_path = path.parent / document.file
        if not document_path.is_file():
            return _state(
                CorpusStatus.mismatch,
                error=f"documento ausente: {document.file}",
                warnings=[_warning(WarningCode.manifest_mismatch, "El manifiesto referencia un documento ausente.")],
            )
        if _file_digest(document_path) != document.sha256:
            return _state(
                CorpusStatus.mismatch,
                error=f"hash inválido: {document.file}",
                warnings=[_warning(WarningCode.manifest_mismatch, "El hash de un documento no coincide con el manifiesto.")],
            )

    if bm25_version is not None and bm25_version != manifest.bm25_index_version:
        return _state(
            CorpusStatus.mismatch,
            error="versión BM25 distinta",
            warnings=[_warning(WarningCode.manifest_mismatch, "La versión del índice BM25 no coincide con el manifiesto.")],
        )

    if bm25_sha256 is not None and bm25_sha256 != manifest.bm25_index_sha256:
        return _state(
            CorpusStatus.mismatch,
            error="hash BM25 distinto",
            warnings=[_warning(WarningCode.manifest_mismatch, "El hash del índice BM25 no coincide con el manifiesto.")],
        )

    return _state(CorpusStatus.ok, manifest=manifest)


def allowed_document_ids(state: ManifestState) -> set[str]:
    if state.manifest is None:
        return set()
    return {document.id for document in state.manifest.documents}


def manifest_titles(state: ManifestState) -> dict[str, str]:
    if state.manifest is None:
        return {}
    return {document.id: document.title for document in state.manifest.documents}
