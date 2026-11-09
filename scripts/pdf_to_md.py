#!/usr/bin/env python3
"""
Convert PDF files to Markdown using pdf-inspector.

This script:
1. Scans data/corpus/ for PDF files
2. Uses pdf-inspector to classify each PDF (native vs scanned)
3. For native PDFs, extracts text as Markdown
4. For scanned PDFs, marks them for OCR processing
5. Saves .md files to data/corpus/ with same base name

Usage:
    python scripts/pdf_to_md.py
    python scripts/pdf_to_md.py --input-dir data/corpus --output-dir data/corpus
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import pdf_inspector
except ImportError as exc:
    raise RuntimeError(
        "pdf-inspector no está disponible; instale con `pip install pdf-inspector`"
    ) from exc


def classify_pdf(pdf_path: Path) -> dict:
    """
    Clasifica un PDF usando pdf-inspector.
    
    Returns:
        dict con claves: 'is_scanned' (bool), 'page_count' (int), 'metadata' (dict)
    """
    try:
        result = pdf_inspector.classify_pdf(str(pdf_path))
    except Exception as e:
        return {
            "is_scanned": True,
            "page_count": 0,
            "metadata": {"error": str(e)},
            "needs_ocr": True
        }
    
    # pdf-inspector returns a PdfClassification object
    is_scanned = len(result.pages_needing_ocr) > 0
    page_count = result.page_count
    
    return {
        "is_scanned": is_scanned,
        "page_count": page_count,
        "metadata": {},
        "needs_ocr": is_scanned
    }


def pdf_to_markdown(pdf_path: Path, output_path: Path | None = None) -> dict:
    """
    Convierte un PDF a Markdown usando pdf-inspector.
    
    Returns:
        dict con claves: 'success' (bool), 'markdown' (str), 'error' (str|None)
    """
    if output_path is None:
        output_path = pdf_path.with_suffix(".md")
    
    try:
        result = pdf_inspector.extract_pages_markdown(str(pdf_path))
        
        # Combinar el markdown de todas las páginas
        markdown_parts = []
        for page in result.pages:
            if page.markdown:
                markdown_parts.append(page.markdown)
        
        markdown = "\n\n".join(markdown_parts)
        
        # Guardar el archivo markdown
        output_path.write_text(markdown, encoding="utf-8")
        
        return {
            "success": True,
            "markdown": markdown,
            "output_path": str(output_path),
            "error": None
        }
    except Exception as e:
        return {
            "success": False,
            "markdown": "",
            "output_path": str(output_path) if output_path else None,
            "error": str(e)
        }


def process_corpus(input_dir: Path, output_dir: Path | None = None, force: bool = False) -> int:
    """
    Procesa todos los PDFs en el directorio de entrada.
    
    Args:
        input_dir: Directorio con archivos PDF
        output_dir: Directorio de salida (por defecto, mismo que input)
        force: Si True, sobrescribe archivos .md existentes
    
    Returns:
        Número de archivos procesados exitosamente
    """
    if output_dir is None:
        output_dir = input_dir
    
    pdf_files = list(input_dir.glob("*.pdf"))
    
    if not pdf_files:
        print(f"No se encontraron archivos PDF en {input_dir}")
        return 0
    
    print(f"Encontrados {len(pdf_files)} archivo(s) PDF en {input_dir}")
    
    success_count = 0
    for pdf_path in pdf_files:
        output_path = output_dir / pdf_path.with_suffix(".md").name
        
        if output_path.exists() and not force:
            print(f"  [SKIP] {pdf_path.name} -> {output_path.name} (ya existe, use --force para sobrescribir)")
            continue
        
        print(f"  [ANALIZANDO] {pdf_path.name}...")
        
        # Clasificar el PDF
        classification = classify_pdf(pdf_path)
        
        if classification.get("needs_ocr", True):
            print(f"  [WARN] {pdf_path.name} parece ser un PDF escaneado (needs OCR)")
            print(f"     Clasificación: scanned={classification['is_scanned']}, pages={classification['page_count']}")
            if classification.get("metadata", {}).get("error"):
                print(f"     Error en clasificación: {classification['metadata']['error']}")
            print(f"     Se omite la conversión a Markdown (necesita OCR)")
            continue
        
        print(f"  [OK] PDF nativo detectado ({classification['page_count']} páginas)")
        
        # Convertir a Markdown
        result = pdf_to_markdown(pdf_path, output_dir / pdf_path.with_suffix(".md").name)
        
        if result["success"]:
            print(f"  [OK] Convertido a {output_path.name} ({len(result['markdown'])} caracteres)")
            success_count += 1
        else:
            print(f"  ❌ Error al convertir: {result['error']}")
    
    return success_count


def main():
    parser = argparse.ArgumentParser(
        description="Convertir PDFs a Markdown usando pdf-inspector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/pdf_to_md.py
  python scripts/pdf_to_md.py --input-dir data/corpus --output-dir data/corpus
  python scripts/pdf_to_md.py --force  # sobrescribe .md existentes
        """
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/corpus"),
        help="Directorio con archivos PDF (default: data/corpus)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directorio de salida para .md (default: mismo que input-dir)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Sobrescribir archivos .md existentes"
    )
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help="Solo clasificar PDFs, no convertir a Markdown"
    )
    
    args = parser.parse_args()
    
    if not args.input_dir.exists():
        print(f"Error: El directorio {args.input_dir} no existe", file=sys.stderr)
        return 1
    
    if args.classify_only:
        # Solo clasificar
        pdf_files = list(args.input_dir.glob("*.pdf"))
        for pdf_path in pdf_files:
            classification = classify_pdf(pdf_path)
            print(f"{pdf_path.name}: scanned={classification['is_scanned']}, "
                  f"pages={classification['page_count']}, needs_ocr={classification.get('needs_ocr', True)}")
        return 0
    
    success = process_corpus(args.input_dir, args.output_dir, args.force)
    
    print(f"\n[OK] Procesamiento completado: {success} archivo(s) convertido(s)")
    return 0 if success > 0 else 0


if __name__ == "__main__":
    sys.exit(main())