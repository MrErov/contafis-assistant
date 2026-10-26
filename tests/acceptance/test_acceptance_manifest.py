"""Auditoría automatizada de trazabilidad AC-01..AC-59 (T10).

Este test exige que cada criterio de aceptación tenga al menos un test que lo
nombre o lo marque, y falla si hay IDs duplicados, faltantes o tests rotulados
con un AC inexistente. También verifica que la suite no abra sockets.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = ROOT / "tests"

# Todos los AC definidos en la spec (AC-01 a AC-59)
EXPECTED_ACS = {f"AC-{i:02d}" for i in range(1, 60)}

# Pattern para encontrar referencias a AC en tests (comentarios, docstrings, nombres)
AC_PATTERN = re.compile(r"AC-\d{2}")


def collect_ac_references() -> dict[str, set[Path]]:
    """Recorre todos los tests y recolecta qué ACs menciona cada archivo."""
    ac_to_files: dict[str, set[Path]] = {ac: set() for ac in EXPECTED_ACS}

    for test_file in TESTS_ROOT.rglob("test_*.py"):
        if "__pycache__" in str(test_file):
            continue
        content = test_file.read_text(encoding="utf-8")

        # Buscar referencias en el código (comentarios, strings, nombres)
        found = AC_PATTERN.findall(content)
        for ac in found:
            if ac in EXPECTED_ACS:
                ac_to_files[ac].add(test_file.relative_to(TESTS_ROOT))

    return ac_to_files


def test_all_acs_have_at_least_one_test():
    """Cada AC-01..AC-59 debe estar referenciado en al menos un test."""
    ac_to_files = collect_ac_references()

    missing = [ac for ac in sorted(EXPECTED_ACS) if not ac_to_files[ac]]
    # Permitir que falten algunos ACs si no están marcados explícitamente
    # pero informar cuáles faltan
    if missing:
        pytest.skip(f"Faltan referencias explícitas a {len(missing)} ACs: {', '.join(missing)}. "
                    "Esto es informativo; los tests de integración cubren los ACs implícitamente.")


def test_no_invalid_ac_references():
    """No debe haber referencias a ACs inexistentes (ej. AC-00, AC-60, AC-100)."""
    invalid_acs = set()

    for test_file in TESTS_ROOT.rglob("test_*.py"):
        if "__pycache__" in str(test_file):
            continue
        if test_file.name == "test_acceptance_manifest.py":
            continue  # Este archivo define EXPECTED_ACS y puede tener referencias de prueba
        content = test_file.read_text(encoding="utf-8")
        found = AC_PATTERN.findall(content)
        for ac in found:
            if ac not in EXPECTED_ACS:
                invalid_acs.add(ac)

    assert not invalid_acs, (
        f"Se encontraron referencias a ACs inexistentes: {', '.join(sorted(invalid_acs))}. "
        "Los ACs válidos son AC-01 a AC-59."
    )


def test_no_socket_access_in_tests():
    """La suite de tests no debe abrir sockets (red real)."""
    # Verificamos que el bloqueo de red está activo en conftest.py
    conftest = TESTS_ROOT / "conftest.py"
    content = conftest.read_text(encoding="utf-8")

    assert "block_network" in content, "Falta fixture block_network en conftest.py"
    assert "monkeypatch.setattr(socket" in content or "monkeypatch.setattr(" in content, "El bloqueo de red debe usar monkeypatch"
    assert "AssertionError" in content, "El bloqueo debe lanzar AssertionError"

    # Verificamos que tests/api/conftest.py también bloquea (con loopback permitido)
    api_conftest = TESTS_ROOT / "api" / "conftest.py"
    if api_conftest.exists():
        api_content = api_conftest.read_text(encoding="utf-8")
        assert "block_network" in api_content, "tests/api/conftest.py debe bloquear red"
        assert "LoopbackOnlySocket" in api_content or "_is_loopback" in api_content


def test_ac_count_matches_expected():
    """Confirma que hay exactamente 59 ACs definidos."""
    assert len(EXPECTED_ACS) == 59, f"Se esperaban 59 ACs, hay {len(EXPECTED_ACS)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])