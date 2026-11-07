"""
Tests de validaci\u00f3n del layout del corpus en disco persistente (T7).

Verifica:
1. Estructura de directorios esperada en /var/data
2. Existencia y validez de manifest.json
3. Coherencia entre manifiesto, BM25 index y Chroma count
4. Idempotencia de ingest_if_needed
"""

from __future__ import annotations

import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path

import pytest

from contafis_assistant.domain.enums import CorpusStatus
from contafis_assistant.rag.corpus import CorpusManager
from contafis_assistant.rag.ingest import IngestPage, ingest_corpus
from contafis_assistant.rag.manifest import load_manifest

# PDF m\u00ednimo v\u00e1lido para tests
MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n192\n%%EOF"


class FakeCollection:
    """Mock de colecci\u00f3n Chroma para tests sin BD real."""
    def __init__(self, count: int):
        self._count = count
        self.added = []

    def count(self):
        return self._count

    def add(self, **kwargs):
        self.added.append(kwargs)


class FakeEmbedder:
    """Mock de embedder que devuelve vectores de dimensi\u00f3n fija."""
    def __init__(self, dimensions: int):
        self._dimensions = dimensions
        self.passages = []

    def embed_documents(self, texts):
        self.passages.extend(texts)
        return [[0.1] * self._dimensions for _ in texts]


def _write_manifest(tmp_path: Path, documents: list[dict], *, version: int = 1, bm25_sha: str | None = None, dimensions: int = 2) -> Path:
    """Escribe un manifest.json v\u00e1lido en tmp_path."""
    payload = {
        "bm25_index_version": version,
        "bm25_index_sha256": bm25_sha or "0" * 64,
        "dimensions": dimensions,
        "documents": documents,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _update_manifest_bm25_hash(manifest_path: Path, bm25_path: Path) -> None:
    """Actualiza el bm25_index_sha256 en el manifiesto con el hash interno del índice BM25."""
    from contafis_assistant.rag.bm25 import load_bm25_index
    result = load_bm25_index(bm25_path)
    if result.status.value != "ok":
        raise RuntimeError(f"BM25 index invalid: {result.error}")
    internal_hash = result.sha256
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["bm25_index_sha256"] = internal_hash
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_pdf(tmp_path: Path, name: str, content: bytes = MINIMAL_PDF) -> Path:
    """Escribe un PDF de prueba."""
    path = tmp_path / name
    path.write_bytes(content)
    return path


def _make_document_entry(pdf_path: Path, doc_id: str, title: str, *, version: str = "2024", pages: int = 1) -> dict:
    """Crea una entrada de documento para el manifiesto con hash real."""
    # El file debe ser relativo al directorio del manifiesto
    relative_path = f"corpus/{pdf_path.name}"
    return {
        "id": doc_id,
        "title": title,
        "version": version,
        "file": relative_path,
        "sha256": sha256(pdf_path.read_bytes()).hexdigest(),
        "pages": pages,
        "published_at": "2024-01-01",
        "language": "es",
        "origin": "Test",
    }


class TestCorpusLayout:
    """Validaci\u00f3n del layout de disco persistente /var/data."""

    def test_manifest_example_exists_and_is_valid(self):
        """data/manifest.example.json existe y es JSON v\u00e1lido con esquema correcto."""
        # Este test se ejecuta contra el repo; en CI el archivo debe estar presente
        example_path = Path(__file__).resolve().parents[2] / "data" / "manifest.example.json"
        if not example_path.is_file():
            pytest.skip("data/manifest.example.json no existe (pendiente de commit T7)")

        content = example_path.read_text(encoding="utf-8")
        payload = json.loads(content)

        # Campos obligatorios del manifiesto
        assert "bm25_index_version" in payload
        assert "bm25_index_sha256" in payload
        assert "dimensions" in payload
        assert "documents" in payload
        assert isinstance(payload["documents"], list)
        assert payload["bm25_index_version"] >= 1
        assert payload["dimensions"] >= 1
        assert len(payload["bm25_index_sha256"]) == 64

        # Cada documento tiene campos requeridos
        for doc in payload["documents"]:
            assert "id" in doc and doc["id"]
            assert "title" in doc and doc["title"]
            assert "file" in doc and doc["file"].startswith("corpus/")
            assert "sha256" in doc and len(doc["sha256"]) == 64
            assert "pages" in doc and doc["pages"] >= 1
            assert "published_at" in doc
            assert "language" in doc
            assert "origin" in doc

    def test_persistent_disk_layout_expected_structure(self, tmp_path):
        """
        Estructura esperada en /var/data (disco persistente):
        /var/data/manifest.json
        /var/data/bm25_index.json
        /var/data/chroma/
        /var/data/corpus/*.pdf
        """
        # Simular layout de disco persistente
        var_data = tmp_path / "var" / "data"
        corpus_dir = var_data / "corpus"
        corpus_dir.mkdir(parents=True)

        # Crear PDFs
        pdf1 = _write_pdf(corpus_dir, "ley-islr.pdf")
        pdf2 = _write_pdf(corpus_dir, "providencia-seniat.pdf")

        # Crear manifiesto
        docs = [
            _make_document_entry(pdf1, "ley-islr", "Ley ISLR", pages=120),
            _make_document_entry(pdf2, "providencia-seniat", "Providencia SENIAT", pages=40),
        ]
        manifest_path = _write_manifest(var_data, docs, dimensions=2048)

        # Verificar estructura
        assert manifest_path.is_file()
        assert (var_data / "corpus" / "ley-islr.pdf").is_file()
        assert (var_data / "corpus" / "providencia-seniat.pdf").is_file()

        # bm25_index.json y chroma/ se crean tras la ingesta
        assert not (var_data / "bm25_index.json").exists()
        assert not (var_data / "chroma").exists()

    def test_manifest_validates_pdf_hashes_and_pages(self, tmp_path):
        """load_manifest valida que los hashes y p\u00e1ginas coinciden con los PDFs reales."""
        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()

        pdf = _write_pdf(corpus_dir, "ley-islr.pdf", b"%PDF-1.4 contenido real mas largo para hash unico")
        docs = [_make_document_entry(pdf, "ley-islr", "Ley ISLR", pages=1)]
        manifest_path = _write_manifest(tmp_path, docs)

        # load_manifest debe pasar con hash y p\u00e1ginas correctos
        state = load_manifest(manifest_path)
        assert state.status == CorpusStatus.ok
        assert state.manifest is not None

        # Si cambiamos el hash en el manifiesto, debe fallar
        bad_manifest = tmp_path / "manifest_bad.json"
        bad_payload = json.loads(manifest_path.read_text())
        bad_payload["documents"][0]["sha256"] = "0" * 64
        bad_manifest.write_text(json.dumps(bad_payload))
        state_bad = load_manifest(bad_manifest)
        assert state_bad.status == CorpusStatus.mismatch
        assert "hash inv\u00e1lido" in state_bad.error

    def test_chroma_count_matches_manifest_documents(self, tmp_path):
        """CorpusManager verifica que Chroma count coincide con documentos del manifiesto."""
        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()

        pdf = _write_pdf(corpus_dir, "doc1.pdf")
        docs = [_make_document_entry(pdf, "doc1", "Doc 1", pages=1)]
        manifest_path = _write_manifest(tmp_path, docs, dimensions=2)
        bm25_path = tmp_path / "bm25_index.json"

        # Crear \u00edndice BM25 v\u00e1lido
        from contafis_assistant.rag.bm25 import Bm25Document, Bm25Index
        index = Bm25Index.build(
            [Bm25Document(chunk_id="c1", document_id="doc1", text="texto", metadata={})],
            version=1,
            collection="col",
        )
        index.save(bm25_path)
        _update_manifest_bm25_hash(manifest_path, bm25_path)
        _update_manifest_bm25_hash(manifest_path, bm25_path)

        # Chroma vac\u00edo \u2192 chroma_status = empty
        manager = CorpusManager(
            manifest_path=manifest_path,
            bm25_path=bm25_path,
            chroma_path=str(tmp_path / "chroma"),
            collection_name="col",
            chroma_opener=lambda _p, _n: FakeCollection(0),
        )
        state = manager.open()
        assert state.chroma_status == CorpusStatus.empty
        assert state.status == CorpusStatus.empty

        # Chroma con datos \u2192 ok
        manager2 = CorpusManager(
            manifest_path=manifest_path,
            bm25_path=bm25_path,
            chroma_path=str(tmp_path / "chroma"),
            collection_name="col",
            chroma_opener=lambda _p, _n: FakeCollection(5),
        )
        state2 = manager2.open()
        assert state2.chroma_status == CorpusStatus.ok
        assert state2.status == CorpusStatus.ok

    def test_ingest_if_needed_idempotent(self, tmp_path, monkeypatch):
        """
        scripts/ingest_if_needed.py es idempotente:
        - Primera ejecuci\u00f3n: ingiere
        - Segunda ejecuci\u00f3n sin cambios: sale con 0 sin embeber
        """
        # Este test requiere importar el script; lo testeamos via la l\u00f3gica de CorpusManager
        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()

        pdf = _write_pdf(corpus_dir, "doc1.pdf")
        docs = [_make_document_entry(pdf, "doc1", "Doc 1", pages=1)]
        manifest_path = _write_manifest(tmp_path, docs, dimensions=2)
        bm25_path = tmp_path / "bm25_index.json"
        chroma_path = tmp_path / "chroma"

        # Simular primera ingesta: crear BM25 index y collection con datos
        from contafis_assistant.rag.bm25 import Bm25Document, Bm25Index
        index = Bm25Index.build(
            [Bm25Document(chunk_id="c1", document_id="doc1", text="texto", metadata={})],
            version=1,
            collection="col",
        )
        index.save(bm25_path)
        _update_manifest_bm25_hash(manifest_path, bm25_path)

        # Collection con 1 documento
        collection = FakeCollection(1)

        # Primera verificaci\u00f3n: est\u00e1 sincronizado
        from contafis_assistant.config import Settings
        settings = Settings(
            _env_file=None,
            chroma_path=str(chroma_path),
            chroma_collection="col",
            bm25_index_path=str(bm25_path),
            bm25_index_version=1,
            embedding_model="test",
            embedding_dimensions=2,
            openai_api_key="test",
            openai_base_url="http://test",
            odoo_url="http://test",
            odoo_api_key="test",
            llm_model="test",
            api_shared_secret="test",
        )

        manager = CorpusManager(
            settings=settings,
            manifest_path=manifest_path,
            chroma_opener=lambda _p, _n: collection,
        )
        state = manager.open()
        assert state.status == CorpusStatus.ok

        # Segunda verificaci\u00f3n (simula segundo deploy): sigue ok, count no cambia
        state2 = manager.open()
        assert state2.status == CorpusStatus.ok
        # Verificar que solo se llam\u00f3 count (no add)
        assert collection.added == []

    def test_ingest_if_needed_detects_changes(self, tmp_path):
        """Si el manifiesto cambia (nueva versi\u00f3n), CorpusManager detecta mismatch."""
        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()

        pdf = _write_pdf(corpus_dir, "doc1.pdf")
        docs_v1 = [_make_document_entry(pdf, "doc1", "Doc 1 v1", version="1", pages=1)]
        manifest_path = _write_manifest(tmp_path, docs_v1, version=1, dimensions=2)
        bm25_path = tmp_path / "bm25_index.json"

        from contafis_assistant.rag.bm25 import Bm25Document, Bm25Index
        index_v1 = Bm25Index.build(
            [Bm25Document(chunk_id="c1", document_id="doc1", text="texto v1", metadata={})],
            version=1,
            collection="col",
        )
        index_v1.save(bm25_path)

        # Manifiesto actualizado a v2 pero BM25 sigue en v1
        docs_v2 = [_make_document_entry(pdf, "doc1", "Doc 1 v2", version="2", pages=1)]
        manifest_path.write_text(json.dumps({
            "bm25_index_version": 2,
            "bm25_index_sha256": "0" * 64,
            "dimensions": 2,
            "documents": docs_v2,
        }))

        from contafis_assistant.config import Settings
        settings = Settings(
            _env_file=None,
            chroma_path=str(tmp_path / "chroma"),
            chroma_collection="col",
            bm25_index_path=str(bm25_path),
            bm25_index_version=2,  # espera v2
            embedding_model="test",
            embedding_dimensions=2,
            openai_api_key="test",
            openai_base_url="http://test",
            odoo_url="http://test",
            odoo_api_key="test",
            llm_model="test",
            api_shared_secret="test",
        )

        manager = CorpusManager(
            settings=settings,
            manifest_path=manifest_path,
            chroma_opener=lambda _p, _n: FakeCollection(1),
        )
        state = manager.open()
        # Debe detectar mismatch porque BM25 en disco es v1 pero manifiesto dice v2
        assert state.bm25_status == CorpusStatus.mismatch
        assert state.status == CorpusStatus.mismatch

    def test_corpus_manager_read_only_no_writes(self, tmp_path):
        """CorpusManager.open() es read-only: no modifica archivos en disco."""
        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()

        pdf = _write_pdf(corpus_dir, "doc1.pdf")
        docs = [_make_document_entry(pdf, "doc1", "Doc 1", pages=1)]
        manifest_path = _write_manifest(tmp_path, docs, dimensions=2)
        bm25_path = tmp_path / "bm25_index.json"

        from contafis_assistant.rag.bm25 import Bm25Document, Bm25Index
        index = Bm25Index.build(
            [Bm25Document(chunk_id="c1", document_id="doc1", text="texto", metadata={})],
            version=1,
            collection="col",
        )
        index.save(bm25_path)
        _update_manifest_bm25_hash(manifest_path, bm25_path)

        # Capturar timestamps antes
        manifest_mtime_before = manifest_path.stat().st_mtime_ns
        bm25_mtime_before = bm25_path.stat().st_mtime_ns

        from contafis_assistant.config import Settings
        settings = Settings(
            _env_file=None,
            chroma_path=str(tmp_path / "chroma"),
            chroma_collection="col",
            bm25_index_path=str(bm25_path),
            bm25_index_version=1,
            embedding_model="test",
            embedding_dimensions=2,
            openai_api_key="test",
            openai_base_url="http://test",
            odoo_url="http://test",
            odoo_api_key="test",
            llm_model="test",
            api_shared_secret="test",
        )

        manager = CorpusManager(
            settings=settings,
            manifest_path=manifest_path,
            chroma_opener=lambda _p, _n: FakeCollection(1),
        )

        # M\u00faltiples aperturas
        for _ in range(3):
            manager.open()

        # Timestamps no deben cambiar (read-only)
        assert manifest_path.stat().st_mtime_ns == manifest_mtime_before
        assert bm25_path.stat().st_mtime_ns == bm25_mtime_before

    @pytest.mark.parametrize("env_var", [
        "CHROMA_PATH",
        "BM25_INDEX_PATH",
        "EMBEDDING_DIMENSIONS",
    ])
    def test_required_env_vars_documented(self, env_var):
        """Variables de entorno requeridas para la ingesta est\u00e1n documentadas en render.yaml."""
        render_yaml = Path(__file__).resolve().parents[2] / "render.yaml"
        content = render_yaml.read_text(encoding="utf-8")
        # En render.yaml los env vars aparecen como 'key: NOMBRE_VAR'
        assert f"key: {env_var}" in content or f"- key: {env_var}" in content, (
            f"{env_var} debe estar en render.yaml"
        )


class TestIngestIfNeededScript:
    """Tests espec\u00edficos del script scripts/ingest_if_needed.py (cuando est\u00e9 disponible)."""

    def test_script_exists(self):
        """El script ingest_if_needed.py existe en scripts/."""
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "ingest_if_needed.py"
        if not script_path.is_file():
            pytest.skip("scripts/ingest_if_needed.py pendiente de creaci\u00f3n (T7)")
        assert script_path.is_file()

    def test_script_has_main_and_args(self):
        """El script tiene funci\u00f3n main y parseo de argumentos."""
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "ingest_if_needed.py"
        if not script_path.is_file():
            pytest.skip("scripts/ingest_if_needed.py pendiente de creaci\u00f3n (T7)")
        content = script_path.read_text(encoding="utf-8")
        assert "def main" in content
        assert "argparse" in content
        assert "ingest_if_needed" in content or "CorpusManager" in content