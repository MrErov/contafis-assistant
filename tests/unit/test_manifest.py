from __future__ import annotations

import json
from hashlib import sha256

import pytest

from contafis_assistant.domain.enums import CorpusStatus, WarningCode
from contafis_assistant.rag.manifest import (
    allowed_document_ids,
    load_manifest,
    manifest_titles,
)

BM25_SHA = "a" * 64


def _write_file(tmp_path, name: str, content: bytes = b"contenido") -> dict:
    path = tmp_path / name
    path.write_bytes(content)
    return {"path": path, "sha256": sha256(content).hexdigest()}


def _document(tmp_path, doc_id: str, *, name: str | None = None) -> dict:
    info = _write_file(tmp_path, name or f"{doc_id}.pdf")
    return {
        "id": doc_id,
        "title": f"Documento {doc_id}",
        "version": "2024.1",
        "file": info["path"].name,
        "sha256": info["sha256"],
        "pages": 10,
        "published_at": "2024-01-01",
        "language": "es",
        "origin": "Gaceta Oficial",
    }


def _write_manifest(
    tmp_path,
    documents: list[dict],
    *,
    version: int = 1,
    bm25_sha: str = BM25_SHA,
    name: str = "manifest.json",
):
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "bm25_index_version": version,
                "bm25_index_sha256": bm25_sha,
                "documents": documents,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_valid_manifest_is_ok_and_exposes_version(tmp_path):
    documents = [_document(tmp_path, "doc-a"), _document(tmp_path, "doc-b")]
    path = _write_manifest(tmp_path, documents, version=3)

    state = load_manifest(path)

    assert state.status == CorpusStatus.ok
    assert state.version == 3
    assert state.sha256 == BM25_SHA
    assert allowed_document_ids(state) == {"doc-a", "doc-b"}
    assert manifest_titles(state)["doc-a"] == "Documento doc-a"
    assert state.manifest is not None
    assert state.manifest.documents[0].pages == 10


def test_missing_manifest_is_missing(tmp_path):
    state = load_manifest(tmp_path / "manifest.json")

    assert state.status == CorpusStatus.missing
    assert state.version is None
    assert allowed_document_ids(state) == set()
    assert state.manifest is None


def test_empty_manifest_is_empty(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_bytes(b"")

    state = load_manifest(path)

    assert state.status == CorpusStatus.empty


def test_manifest_without_documents_is_empty(tmp_path):
    path = _write_manifest(tmp_path, [])

    state = load_manifest(path)

    assert state.status == CorpusStatus.empty


def test_manifest_with_invalid_json_is_mismatch(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{no es json", encoding="utf-8")

    state = load_manifest(path)

    assert state.status == CorpusStatus.mismatch
    assert WarningCode.manifest_mismatch in {w.code for w in state.warnings}


def test_wrong_document_hash_is_mismatch(tmp_path):
    document = _document(tmp_path, "doc-a")
    document["sha256"] = "0" * 64
    path = _write_manifest(tmp_path, [document])

    state = load_manifest(path)

    assert state.status == CorpusStatus.mismatch
    assert allowed_document_ids(state) == set()


def test_missing_document_file_is_mismatch(tmp_path):
    document = _document(tmp_path, "doc-a")
    (tmp_path / document["file"]).unlink()
    path = _write_manifest(tmp_path, [document])

    state = load_manifest(path)

    assert state.status == CorpusStatus.mismatch


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"bm25_version": 2}, CorpusStatus.mismatch),
        ({"bm25_sha256": "b" * 64}, CorpusStatus.mismatch),
        ({"bm25_version": 1, "bm25_sha256": BM25_SHA}, CorpusStatus.ok),
    ],
)
def test_bm25_version_or_hash_must_match_manifest(tmp_path, kwargs, status):
    path = _write_manifest(tmp_path, [_document(tmp_path, "doc-a")], version=1)

    state = load_manifest(path, **kwargs)

    assert state.status == status


def test_only_manifest_documents_are_allowed(tmp_path):
    path = _write_manifest(tmp_path, [_document(tmp_path, "doc-a")])

    state = load_manifest(path)

    allowed = allowed_document_ids(state)
    assert "doc-a" in allowed
    assert "fuera-del-manifiesto" not in allowed
    assert "fuera-del-manifiesto" not in manifest_titles(state)


def test_mismatch_state_has_no_documents_for_citation(tmp_path):
    document = _document(tmp_path, "doc-a")
    document["sha256"] = "f" * 64
    path = _write_manifest(tmp_path, [document])

    state = load_manifest(path)

    assert state.status == CorpusStatus.mismatch
    assert state.manifest is None
    assert manifest_titles(state) == {}
