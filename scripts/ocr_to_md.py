#!/usr/bin/env python3
"""OCR con Tesseract para PDFs escaneados e imágenes de recibos/facturas.

`pdf-inspector` omite los PDFs escaneados, y los comprobantes físicos suelen
llegar como imagen (JPG/PNG). Este módulo los convierte a Markdown para que
entren al mismo pipeline de ingesta que los PDF nativos.

Diseño (mínimas dependencias de sistema):
- pypdf extrae las imágenes incrustadas de un PDF escaneado, sin poppler.
- Pillow normaliza cada página (escala de grises + aumento si es pequeña).
- pytesseract hace el OCR sobre cada imagen.

Si Tesseract no está instalado, las funciones lo reportan con un error claro en
lugar de fallar con un traceback opaco.

Uso:
    python scripts/ocr_to_md.py --input data/corpus/gaceta-43.484.pdf
    python scripts/ocr_to_md.py --input recibos/factura.jpg --lang spa
"""

from __future__ import annotations

import argparse
import io
import logging
import shutil
import sys
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".gif",
}

DEFAULT_LANG = "spa"
DEFAULT_DPI = 300
_MIN_WIDTH_UPSCALE = 1200


class OCRUnavailableError(RuntimeError):
    """Tesseract o sus bindings no están disponibles."""


def _normalize(text: str) -> str:
    """Minúsculas sin acentos ni espacios sobrantes (para comparar en tests)."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.casefold().split())


def tesseract_available() -> bool:
    """True si el binario de Tesseract es alcanzable y ``pytesseract`` responde."""
    if shutil.which("tesseract") is None:
        return False
    try:
        import pytesseract  # import diferido: no es dependencia dura del servicio

        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


def _require_tesseract() -> None:
    if tesseract_available():
        return
    if shutil.which("tesseract") is None:
        raise OCRUnavailableError(
            "Tesseract no está en el PATH. Instálelo (Windows: instalador oficial "
            "de UB Mannheim) y verifique con `tesseract --version`."
        )
    raise OCRUnavailableError(
        "Tesseract está instalado pero pytesseract no puede invocarlo. Instale "
        "el paquete con `pip install pytesseract`."
    )


def _prepare_image(image):
    """Normaliza una imagen de Pillow para mejorar el OCR."""
    grayscale = image.convert("L")
    width, _height = grayscale.size
    if width < _MIN_WIDTH_UPSCALE:
        scale = _MIN_WIDTH_UPSCALE / float(width)
        grayscale = grayscale.resize(
            (int(width * scale), int(grayscale.size[1] * scale))
        )
    return grayscale


def _open_image_from_bytes(data: bytes):
    from PIL import Image

    return Image.open(io.BytesIO(data))


def images_from_pdf(pdf_path: Path, *, dpi: int = DEFAULT_DPI) -> list:
    """Extrae las páginas de un PDF escaneado como imágenes de Pillow.

    Ruta primaria: pypdf lee las imágenes incrustadas (sin poppler). Si el PDF no
    expone imágenes incrustadas, se intenta ``pdf2image`` (requiere poppler).
    """
    import pypdf

    pages: list = []
    reader = pypdf.PdfReader(str(pdf_path))
    for index, page in enumerate(reader.pages):
        embedded = list(getattr(page, "images", None) or [])
        for blob in embedded:
            image = None
            try:
                data = getattr(blob, "data", None)
                if data:
                    image = _open_image_from_bytes(data)
            except Exception:  # pragma: no cover - formatos exóticos
                image = None
            if image is None:
                candidate = getattr(blob, "image", None)
                if candidate is not None:
                    image = candidate
            if image is not None:
                pages.append((index + 1, image))

    if pages:
        return [image for _page_number, image in pages]

    pages = _pages_via_pdf2image(pdf_path, dpi=dpi)
    if pages:
        return pages

    raise OCRUnavailableError(
        f"No se pudieron extraer imágenes de {pdf_path.name}: el PDF no expone "
        "imágenes incrustadas y pdf2image/poppler no está disponible."
    )


def _pages_via_pdf2image(pdf_path: Path, *, dpi: int) -> list:
    """Convierte el PDF a imágenes con pdf2image (requiere poppler en el PATH)."""
    if shutil.which("pdftoppm") is None:
        return []
    try:
        from pdf2image import convert_from_path
    except ImportError:
        return []
    try:
        return list(convert_from_path(str(pdf_path), dpi=dpi))
    except Exception as exc:  # pragma: no cover - depende del entorno
        logger.warning("pdf2image falló sobre %s: %s", pdf_path.name, exc)
        return []


def _ocr_with_lang_fallback(image, lang: str) -> str:
    import pytesseract

    for candidate in (lang, DEFAULT_LANG, "eng"):
        try:
            return pytesseract.image_to_string(image, lang=candidate)
        except Exception as exc:
            message = str(exc).lower()
            if "traineddata" in message or "lang" in message or "Failed loading" in message:
                continue
            raise
    raise OCRUnavailableError(
        f"No hay datos de idioma de Tesseract para {lang!r}. Instale el paquete "
        "de idioma (spa) o use --lang eng."
    )


def ocr_image(image, *, lang: str = DEFAULT_LANG) -> str:
    """Reconoce texto de una imagen de Pillow ya preparada."""
    _require_tesseract()
    return _ocr_with_lang_fallback(_prepare_image(image), lang)


def load_image(path: Path):
    """Abre una imagen desde disco como objeto de Pillow."""
    from PIL import Image

    with Image.open(path) as opened:
        return opened.convert("RGB")


def ocr_file(path: Path, *, lang: str = DEFAULT_LANG, dpi: int = DEFAULT_DPI) -> str:
    """OCR de un PDF escaneado o de una imagen. Devuelve el texto concatenado."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No existe el archivo: {path}")

    if path.suffix.lower() == ".pdf":
        pages = images_from_pdf(path, dpi=dpi)
        if not pages:
            return ""
        rendered = []
        for position, image in enumerate(pages, start=1):
            _require_tesseract()
            text = _ocr_with_lang_fallback(_prepare_image(image), lang)
            rendered.append(f"## Página {position}\n\n{text.strip()}\n")
        return "\n".join(rendered)

    return ocr_image(load_image(path), lang=lang)


def ocr_to_markdown(
    path: Path,
    output_dir: Path | None = None,
    *,
    lang: str = DEFAULT_LANG,
    dpi: int = DEFAULT_DPI,
    force: bool = False,
) -> dict:
    """Convierte un PDF escaneado o una imagen a ``.md`` junto al original.

    Returns:
        dict con claves: success, output_path, markdown, error, skipped.
    """
    path = Path(path)
    if output_dir is None:
        output_dir = path.parent
    output_path = Path(output_dir) / f"{path.stem}.md"

    failure = {
        "success": False,
        "output_path": str(output_path),
        "markdown": "",
        "error": None,
        "skipped": False,
    }

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return {
            **failure,
            "error": f"Extensión no soportada {path.suffix!r}; use {sorted(SUPPORTED_EXTENSIONS)}.",
        }

    if not path.is_file():
        return {**failure, "error": f"No existe el archivo: {path}"}

    if output_path.exists() and not force:
        return {**failure, "error": f"{output_path.name} ya existe (use --force).", "skipped": True}

    try:
        text = ocr_file(path, lang=lang, dpi=dpi)
    except OCRUnavailableError as exc:
        return {**failure, "error": str(exc)}
    except FileNotFoundError as exc:
        return {**failure, "error": str(exc)}

    if not text.strip():
        return {**failure, "error": f"El OCR no extrajo texto de {path.name}."}

    header = [
        f"# {path.stem}",
        "",
        f"<!-- Fuente: {path.name} | OCR: tesseract | idioma: {lang} -->",
        "",
    ]
    markdown = "\n".join([*header, text])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")

    return {
        "success": True,
        "output_path": str(output_path),
        "markdown": markdown,
        "error": None,
        "skipped": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OCR con Tesseract para PDFs escaneados o imágenes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/ocr_to_md.py --input data/corpus/gaceta-43.484.pdf
  python scripts/ocr_to_md.py --input recibos/factura.jpg --lang spa --force
        """,
    )
    parser.add_argument("--input", type=Path, required=True, help="PDF escaneado o imagen")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directorio de salida")
    parser.add_argument("--lang", default=DEFAULT_LANG, help="Idioma de Tesseract (default: spa)")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help="DPI para pdf2image")
    parser.add_argument("--force", action="store_true", help="Sobrescribe el .md existente")
    args = parser.parse_args()

    result = ocr_to_markdown(
        args.input,
        args.output_dir,
        lang=args.lang,
        dpi=args.dpi,
        force=args.force,
    )

    if result["skipped"]:
        print(f"[SKIP] {result['error']}")
        return 0
    if not result["success"]:
        print(f"[ERROR] {result['error']}", file=sys.stderr)
        return 1

    print(f"[OK] {args.input.name} -> {Path(result['output_path']).name} ({len(result['markdown'])} caracteres)")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "OCRUnavailableError",
    "DEFAULT_LANG",
    "SUPPORTED_EXTENSIONS",
    "images_from_pdf",
    "load_image",
    "ocr_file",
    "ocr_image",
    "ocr_to_markdown",
    "tesseract_available",
]
