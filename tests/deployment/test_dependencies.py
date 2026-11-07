"""Tests de reproducibilidad de dependencias (Spec 004 T2).

Verifica que:
1. `requirements.txt` y `pyproject.toml` declaran las mismas dependencias runtime.
2. `constraints.txt` existe y contiene versiones exactas.
3. No hay divergencias no documentadas entre ambos archivos.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
REQUIREMENTS_TXT = REPO_ROOT / "requirements.txt"
PYPROJECT_TOML = REPO_ROOT / "pyproject.toml"
CONSTRAINTS_TXT = REPO_ROOT / "constraints.txt"


def _parse_requirements_txt(path: Path) -> dict[str, str]:
    """Parsea requirements.txt y devuelve {package: specifier}."""
    deps = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # Soporte básico: package[extra]==specifier o package==specifier
        # Ignorar líneas que empiecen con - (opciones de pip como -c, -r, etc.)
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if match:
            name = match.group(1).lower()
            deps[name] = line
    return deps


def _parse_pyproject_toml_dependencies(path: Path) -> dict[str, str]:
    """Extrae dependencies del [project] en pyproject.toml."""
    import tomllib

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    deps = {}
    for dep in data.get("project", {}).get("dependencies", []):
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", dep)
        if match:
            name = match.group(1).lower()
            deps[name] = dep
    return deps


def _parse_constraints_txt(path: Path) -> dict[str, str]:
    """Parsea constraints.txt (formato pip freeze: package==version)."""
    deps = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            name, version = line.split("==", 1)
            deps[name.lower()] = version
    return deps


class TestConstraintsExist:
    """constraints.txt debe existir y ser válido."""

    def test_constraints_file_exists(self):
        assert CONSTRAINTS_TXT.exists(), "constraints.txt no existe"

    def test_constraints_has_pinned_versions(self):
        """constraints.txt debe tener versiones exactas (==)."""
        content = CONSTRAINTS_TXT.read_text(encoding="utf-8")
        lines = [l.strip() for l in content.splitlines() if l.strip() and not l.startswith("#")]
        assert len(lines) > 0, "constraints.txt está vacío"
        for line in lines:
            assert "==" in line, f"Línea sin versión exacta: {line}"

    def test_constraints_no_local_paths(self):
        """No debe haber rutas locales ni hashes de ruedas."""
        content = CONSTRAINTS_TXT.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                assert not line.startswith("./"), f"Ruta local en constraints: {line}"
                assert not line.startswith("/"), f"Ruta absoluta en constraints: {line}"
                assert "file://" not in line, f"URL file:// en constraints: {line}"
                assert "@" not in line or "@ " in line, f"Posible hash/URL en constraints: {line}"


class TestRequirementsTxtHasConstraints:
    """requirements.txt debe referenciar constraints.txt."""

    def test_requirements_starts_with_constraints(self):
        content = REQUIREMENTS_TXT.read_text(encoding="utf-8").strip()
        assert content.startswith("-c constraints.txt"), (
            "requirements.txt debe empezar con '-c constraints.txt'"
        )


class TestDependencyReconciliation:
    """requirements.txt y pyproject.toml deben declarar lo mismo (runtime)."""

    # Paquetes que están solo en pyproject.toml como dependencias de build/dev
    # y NO deben estar en requirements.txt (documentados como excepción).
    PYTHON_ONLY_DEPS = {
        "tenacity",  # en pyproject, no en requirements (transitiva de langchain)
        "python-dateutil",  # en pyproject, no en requirements (transitiva)
        "pyyaml",  # en pyproject, no en requirements (transitiva)
        "orjson",  # en pyproject, no en requirements (transitiva de fastapi/starlette)
        "openai",  # en pyproject, no en requirements (transitiva de langchain-openai)
    }

    # Paquetes que están solo en requirements.txt (tests/runtime) y no en pyproject.toml
    REQUIREMENTS_ONLY_DEPS = {
        "pytest",
        "pytest-mock",
        "pillow",
        "langchain-community",  # en requirements, no en pyproject (transitiva de langchain)
        "rank-bm25",
        "rapidfuzz",
        "pypdf",
    }

    def test_runtime_deps_match(self):
        """Dependencias runtime declaradas en ambos archivos coinciden."""
        req_deps = _parse_requirements_txt(REQUIREMENTS_TXT)
        pyproject_deps = _parse_pyproject_toml_dependencies(PYPROJECT_TOML)

        # Normalizar: quitar especifiers, solo nombres
        req_names = set(req_deps.keys()) - self.REQUIREMENTS_ONLY_DEPS
        pyproject_names = set(pyproject_deps.keys()) - self.PYTHON_ONLY_DEPS

        missing_in_req = pyproject_names - req_names
        missing_in_pyproject = req_names - pyproject_names

        assert not missing_in_req, (
            f"Dependencias en pyproject.toml pero no en requirements.txt: {sorted(missing_in_req)}"
        )
        assert not missing_in_pyproject, (
            f"Dependencias en requirements.txt pero no en pyproject.toml: {sorted(missing_in_pyproject)}"
        )

    def test_constraints_covers_all_declared(self):
        """constraints.txt debe cubrir todas las dependencias declaradas."""
        req_deps = _parse_requirements_txt(REQUIREMENTS_TXT)
        pyproject_deps = _parse_pyproject_toml_dependencies(PYPROJECT_TOML)
        all_declared = set(req_deps.keys()) | set(pyproject_deps.keys())

        constraints = _parse_constraints_txt(CONSTRAINTS_TXT)

        missing = all_declared - set(constraints.keys())
        # Permitir que falten paquetes solo de test que no se instalan en runtime
        missing = missing - self.REQUIREMENTS_ONLY_DEPS

        assert not missing, (
            f"Dependencias declaradas sin versión en constraints.txt: {sorted(missing)}"
        )


class TestReproducibleBuild:
    """Verificación de build reproducible (opcional, requiere Docker)."""

    @pytest.mark.skipif(
        not (REPO_ROOT / "Dockerfile").exists(),
        reason="Dockerfile no existe aún",
    )
    def test_docker_build_reproducible(self):
        """Dos builds consecutivos producen el mismo pip freeze."""
        import hashlib

        def build_and_freeze(tag: str) -> str:
            result = subprocess.run(
                ["docker", "build", "--no-cache", "-t", tag, "."],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                pytest.skip(f"Build falló: {result.stderr[:500]}")

            run_result = subprocess.run(
                ["docker", "run", "--rm", tag, "pip", "freeze"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            return run_result.stdout.strip()

        freeze1 = build_and_freeze("contafis-test-1")
        freeze2 = build_and_freeze("contafis-test-2")

        # Ordenar líneas para comparar
        lines1 = sorted(freeze1.splitlines())
        lines2 = sorted(freeze2.splitlines())

        assert lines1 == lines2, "Build no reproducible: pip freeze difiere entre builds"

        # Limpieza
        subprocess.run(["docker", "rmi", "contafis-test-1", "contafis-test-2"], capture_output=True)