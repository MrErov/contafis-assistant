from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from contafis_assistant.domain.enums import CorpusStatus
from contafis_assistant.rag.bm25 import Bm25Document, Bm25Index
from contafis_assistant.rag.corpus import CorpusManager
from contafis_assistant.rag.ingest import IngestError, IngestPage, ingest_corpus

PDF_BYTES = b"%PDF-1.4 contenido de prueba"


class FakeCollection:
    def __init__(self, count: int):
        self._count = count
        self.calls = []

    def count(self):
        self.calls.append("count")
        return self._count

    def add(self, *_args, **_kwargs):
        raise AssertionError("el runtime no debe escribir en Chroma")

    def upsert(self, *_args, **_kwargs):
        raise AssertionError("el runtime no debe escribir en Chroma")

    def delete(self, *_args, **_kwargs):
        raise AssertionError("el runtime no debe escribir en Chroma")


def _index(*, version: int = 1) -> Bm25Index:
    return Bm25Index.build(
        [
            Bm25Document(
                chunk_id="c1",
                document_id="doc-1",
                text="iva impuesto al valor agregado",
                metadata={"document": "Norma IVA", "page": 1, "collection": "col"},
            )
        ],
        version=version,
        collection="col",
    )


def _manifest(tmp_path, index: Bm25Index, *, version: int | None = None, bm25_sha: str | None = None):
    pdf_path = tmp_path / "doc-1.pdf"
    pdf_path.write_bytes(PDF_BYTES)
    payload = {
        "bm25_index_version": version if version is not None else index.version,
        "bm25_index_sha256": bm25_sha if bm25_sha is not None else index.sha256,
        "documents": [
            {
                "id": "doc-1",
                "title": "Norma IVA",
                "version": "2024.1",
                "file": pdf_path.name,
                "sha256": sha256(PDF_BYTES).hexdigest(),
                "pages": 1,
                "published_at": "2024-01-01",
                "language": "es",
                "origin": "Gaceta Oficial",
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _save_index(tmp_path, index: Bm25Index):
    path = tmp_path / "bm25_index.json"
    index.save(path)
    return path


def _manager(
    tmp_path,
    *,
    manifest_path,
    bm25_path,
    expected_version: int = 1,
    collection=None,
    opener=None,
):
    if opener is None:
        opener = lambda _path, _name: collection
    return CorpusManager(
        manifest_path=manifest_path,
        bm25_path=bm25_path,
        expected_version=expected_version,
        chroma_path=str(tmp_path / "chroma"),
        collection_name="col",
        chroma_opener=opener,
    )


def _snapshot(root):
    return {
        str(path.relative_to(root)): (
            path.stat().st_mtime_ns,
            sha256(path.read_bytes()).hexdigest(),
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def test_valid_corpus_is_ok_and_exposes_version(tmp_path):
    index = _index()
    manifest = _manifest(tmp_path, index)
    bm25_path = _save_index(tmp_path, index)
    manager = _manager(
        tmp_path,
        manifest_path=manifest,
        bm25_path=bm25_path,
        collection=FakeCollection(2),
    )

    state = manager.open()

    assert state.status == CorpusStatus.ok
    assert state.version == 1
    assert manager.allowed_document_ids == {"doc-1"}
    assert manager.manifest_titles == {"doc-1": "Norma IVA"}


def test_missing_bm25_file_is_missing_without_exception(tmp_path):
    index = _index()
    manifest = _manifest(tmp_path, index)
    manager = _manager(
        tmp_path,
        manifest_path=manifest,
        bm25_path=tmp_path / "bm25_index.json",
        collection=FakeCollection(2),
    )

    state = manager.open()

    assert state.bm25_status == CorpusStatus.missing
    assert state.status == CorpusStatus.missing


def test_corrupt_bm25_file_is_mismatch(tmp_path):
    index = _index()
    manifest = _manifest(tmp_path, index)
    bm25_path = tmp_path / "bm25_index.json"
    bm25_path.write_text("{corrupto", encoding="utf-8")
    manager = _manager(
        tmp_path,
        manifest_path=manifest,
        bm25_path=bm25_path,
        collection=FakeCollection(2),
    )

    state = manager.open()

    assert state.bm25_status == CorpusStatus.mismatch
    assert state.status == CorpusStatus.mismatch


def test_bm25_version_mismatch_is_mismatch(tmp_path):
    index = _index(version=2)
    manifest = _manifest(tmp_path, index, version=1)
    bm25_path = _save_index(tmp_path, index)
    manager = _manager(
        tmp_path,
        manifest_path=manifest,
        bm25_path=bm25_path,
        expected_version=1,
        collection=FakeCollection(2),
    )

    state = manager.open()

    assert state.bm25_status == CorpusStatus.mismatch
    assert state.status == CorpusStatus.mismatch


def test_bm25_hash_mismatch_is_mismatch(tmp_path):
    index = _index()
    manifest = _manifest(tmp_path, index, bm25_sha="c" * 64)
    bm25_path = _save_index(tmp_path, index)
    manager = _manager(
        tmp_path,
        manifest_path=manifest,
        bm25_path=bm25_path,
        collection=FakeCollection(2),
    )

    state = manager.open()

    assert state.bm25_status == CorpusStatus.mismatch
    assert state.status == CorpusStatus.mismatch


def test_missing_chroma_collection_is_missing(tmp_path):
    index = _index()
    manifest = _manifest(tmp_path, index)
    bm25_path = _save_index(tmp_path, index)
    manager = _manager(
        tmp_path,
        manifest_path=manifest,
        bm25_path=bm25_path,
        opener=lambda _path, _name: None,
    )

    state = manager.open()

    assert state.chroma_status == CorpusStatus.missing
    assert state.status == CorpusStatus.missing


def test_empty_chroma_collection_is_empty(tmp_path):
    index = _index()
    manifest = _manifest(tmp_path, index)
    bm25_path = _save_index(tmp_path, index)
    manager = _manager(
        tmp_path,
        manifest_path=manifest,
        bm25_path=bm25_path,
        collection=FakeCollection(0),
    )

    state = manager.open()

    assert state.chroma_status == CorpusStatus.empty
    assert state.status == CorpusStatus.empty


@pytest.mark.parametrize(
    "scenario",
    [
        "missing_manifest",
        "corrupt_manifest",
        "missing_bm25",
        "corrupt_bm25",
        "missing_chroma",
    ],
)
def test_degraded_startup_never_raises(tmp_path, scenario):
    index = _index()
    manifest = _manifest(tmp_path, index)
    bm25_path = _save_index(tmp_path, index)
    collection = FakeCollection(2)

    if scenario == "missing_manifest":
        manifest = tmp_path / "no-existe.json"
    elif scenario == "corrupt_manifest":
        manifest.write_text("{corrupto", encoding="utf-8")
    elif scenario == "missing_bm25":
        bm25_path = tmp_path / "no-existe-bm25.json"
    elif scenario == "corrupt_bm25":
        bm25_path.write_text("{corrupto", encoding="utf-8")
    elif scenario == "missing_chroma":
        collection = None

    manager = _manager(
        tmp_path,
        manifest_path=manifest,
        bm25_path=bm25_path,
        collection=collection,
    )

    state = manager.open()

    assert state.status in {
        CorpusStatus.missing,
        CorpusStatus.empty,
        CorpusStatus.mismatch,
    }


def test_open_is_read_only_and_does_not_write_files(tmp_path):
    index = _index()
    manifest = _manifest(tmp_path, index)
    bm25_path = _save_index(tmp_path, index)
    collection = FakeCollection(2)
    manager = _manager(
        tmp_path,
        manifest_path=manifest,
        bm25_path=bm25_path,
        collection=collection,
    )
    before = _snapshot(tmp_path)

    manager.open()
    manager.open()

    assert _snapshot(tmp_path) == before
    assert collection.calls == ["count"]


class FakeIngestCollection:
    def __init__(self):
        self.added = []

    def add(self, **kwargs):
        self.added.append(kwargs)


def test_ingest_is_offline_and_does_not_modify_sources(tmp_path):
    index = _index()
    manifest = _manifest(tmp_path, index)
    pdf = tmp_path / "doc-1.pdf"
    (tmp_path / "fuera-del-manifiesto.pdf").write_bytes(b"no debe indexarse")
    before = {
        path.name: (path.stat().st_mtime_ns, sha256(path.read_bytes()).hexdigest())
        for path in (manifest, pdf)
    }
    seen = []

    def extractor(path):
        seen.append(Path(path).name)
        return [IngestPage(page=1, text="iva impuesto al valor agregado " * 80)]

    collection = FakeIngestCollection()
    result = ingest_corpus(
        manifest,
        bm25_output_path=tmp_path / "bm25_ingest.json",
        extractor=extractor,
        embedder=lambda texts: [[0.1, 0.2] for _ in texts],
        collection=collection,
        collection_name="col",
        version=1,
    )

    assert seen == ["doc-1.pdf"]
    assert result.document_ids == ["doc-1"]
    assert result.chunks >= 1
    assert result.bm25_index_version == 1
    assert result.bm25_index_sha256
    assert collection.added
    assert (tmp_path / "bm25_ingest.json").is_file()

    after = {
        path.name: (path.stat().st_mtime_ns, sha256(path.read_bytes()).hexdigest())
        for path in (manifest, pdf)
    }
    assert after == before


def test_ingest_rejects_invalid_manifest_with_clear_error(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{corrupto", encoding="utf-8")

    with pytest.raises(IngestError):
        ingest_corpus(manifest)


def test_manifest_path_is_derived_from_bm25_path(tmp_path):
    from contafis_assistant.rag.manifest import manifest_path_for_bm25

    derived = manifest_path_for_bm25(tmp_path / "corpus" / "bm25_index.json")

    assert derived == tmp_path / "corpus" / "manifest.json"


def test_real_ingestion_without_chromadb_fails_clearly(tmp_path, monkeypatch):
    import builtins

    from contafis_assistant.rag.ingest import create_chroma_collection

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "chromadb":
            raise ImportError("chromadb no instalado")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="chromadb"):
        create_chroma_collection(tmp_path / "chroma", "col")
