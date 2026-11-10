"""Tests del pipeline OCR (Tesseract) para PDFs escaneados e imágenes."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import ocr_to_md

try:
    import pytesseract
except ImportError:  # pragma: no cover - dependencia opcional
    pytesseract = None  # type: ignore[assignment]


def _sample_image(directory: Path, name: str = "recibo.png") -> Path:
    image = Image.new("RGB", (640, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.text((12, 20), "FACTURA 001", fill="black")
    draw.text((12, 60), "PROVEEDOR ACME", fill="black")
    draw.text((12, 100), "TOTAL 119.00", fill="black")
    path = directory / name
    image.save(path)
    return path


def _normalized(value: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.casefold().split())


needs_tesseract = pytest.mark.skipif(
    not ocr_to_md.tesseract_available(),
    reason="Tesseract no está instalado o no responde (pip install pytesseract + binario)",
)


def test_tesseract_available_returns_boolean_without_raising():
    assert isinstance(ocr_to_md.tesseract_available(), bool)


def test_ocr_to_markdown_rejects_unsupported_extension(tmp_path):
    target = tmp_path / "documento.txt"
    target.write_text("no es imagen", encoding="utf-8")

    result = ocr_to_md.ocr_to_markdown(target)

    assert result["success"] is False
    assert "no soportada" in result["error"]


def test_ocr_to_markdown_reports_missing_input(tmp_path):
    result = ocr_to_md.ocr_to_markdown(tmp_path / "inexistente.png")

    assert result["success"] is False
    assert "No existe" in result["error"]


def test_ocr_to_markdown_writes_header_and_text(tmp_path, monkeypatch):
    image_path = _sample_image(tmp_path)
    monkeypatch.setattr(ocr_to_md, "ocr_file", lambda *a, **k: "FACTURA 001\nTOTAL 119.00")

    result = ocr_to_md.ocr_to_markdown(image_path)

    assert result["success"] is True
    output = Path(result["output_path"])
    assert output.name == "recibo.md"
    content = output.read_text(encoding="utf-8")
    assert content.startswith("# recibo")
    assert "OCR: tesseract" in content
    assert "FACTURA 001" in content


def test_ocr_to_markdown_skips_existing_output_without_force(tmp_path, monkeypatch):
    image_path = _sample_image(tmp_path)
    monkeypatch.setattr(ocr_to_md, "ocr_file", lambda *a, **k: "texto")
    assert ocr_to_md.ocr_to_markdown(image_path)["success"] is True

    second = ocr_to_md.ocr_to_markdown(image_path)

    assert second["success"] is False
    assert second["skipped"] is True
    assert "ya existe" in second["error"]

    forced = ocr_to_md.ocr_to_markdown(image_path, force=True)

    assert forced["success"] is True


def test_ocr_to_markdown_reports_empty_extraction(tmp_path, monkeypatch):
    image_path = _sample_image(tmp_path)
    monkeypatch.setattr(ocr_to_md, "ocr_file", lambda *a, **k: "   ")

    result = ocr_to_md.ocr_to_markdown(image_path)

    assert result["success"] is False
    assert "no extrajo texto" in result["error"]


@needs_tesseract
def test_ocr_image_reads_hola_mundo():
    image = Image.new("RGB", (520, 90), "white")
    draw = ImageDraw.Draw(image)
    draw.text((16, 28), "Hola mundo", fill="black")

    extracted = ocr_to_md.ocr_image(image, lang="spa")

    assert "hola" in _normalized(extracted)


@needs_tesseract
def test_ocr_file_on_receipt_image(tmp_path):
    image_path = _sample_image(tmp_path, "recibo_real.png")

    text = ocr_to_md.ocr_file(image_path, lang="spa")

    assert text.strip()
    assert "factura" in _normalized(text)


def test_pdf_to_md_routes_scanned_pdfs_to_ocr(tmp_path, monkeypatch):
    import pdf_to_md

    scanned = tmp_path / "escaneado.pdf"
    scanned.write_bytes(b"%PDF-1.4 escaneado")

    monkeypatch.setattr(
        pdf_to_md,
        "classify_pdf",
        lambda path: {"is_scanned": True, "page_count": 3, "metadata": {}, "needs_ocr": True},
    )
    # El import de ocr_to_markdown es local a process_corpus: se parchea el
    # módulo de origen, no el espacio de nombres de pdf_to_md.
    monkeypatch.setattr(
        ocr_to_md,
        "ocr_to_markdown",
        lambda path, out: {
            "success": True,
            "output_path": str(Path(out) / "escaneado.md"),
            "markdown": "# escaneado",
            "error": None,
            "skipped": False,
        },
    )
    calls: list[tuple[Path, Path]] = []
    original = ocr_to_md.ocr_to_markdown
    monkeypatch.setattr(
        ocr_to_md,
        "ocr_to_markdown",
        lambda path, out: (calls.append((Path(path), Path(out))), original(path, out))[1],
    )

    processed = pdf_to_md.process_corpus(tmp_path)

    assert processed == 1
    assert calls == [(scanned, tmp_path)]


def test_pdf_to_md_no_ocr_skips_scanned_pdfs(tmp_path, monkeypatch):
    import pdf_to_md

    scanned = tmp_path / "escaneado.pdf"
    scanned.write_bytes(b"%PDF-1.4 escaneado")

    monkeypatch.setattr(
        pdf_to_md,
        "classify_pdf",
        lambda path: {"is_scanned": True, "page_count": 3, "metadata": {}, "needs_ocr": True},
    )
    monkeypatch.setattr(
        ocr_to_md,
        "ocr_to_markdown",
        lambda *a, **k: pytest.fail("no debería invocarse con --no-ocr"),
    )

    processed = pdf_to_md.process_corpus(tmp_path, no_ocr=True)

    assert processed == 0
    assert not (tmp_path / "escaneado.md").exists()
