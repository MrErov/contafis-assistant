"""Tests de reproducibilidad de dependencias (Spec 004, T2).

Garantías, sin red y sin Docker por defecto:

1. `constraints.txt` existe, es UTF-8/ASCII limpio y contiene solo pins exactos
   (`nombre==versión`), sin rutas, URLs, hashes ni duplicados.
2. `requirements.txt` declara `-c constraints.txt` primero y todo lo que instala
   está pineado por el constraints.
3. `pyproject.toml` no declara dependencias runtime que falten en
   `requirements.txt` (salvo las documentadas en `PYPROJECT_ONLY`).
4. Las allowlists de divergencia están disjuntas y vigentes: si alguien alinea
   un paquete y olvida actualizar la allowlist, el test falla.
5. El Dockerfile copia `constraints.txt` antes de cada `RUN ... pip ... -r
   requirements.txt` (builder y runtime).

El único test que usa Docker (`test_image_freeze_matches_constraints`) está
marcado `docker` y solo corre con `RUN_DOCKER_TESTS=1` (mismo contrato que
`tests/docker/`); compara el `pip freeze` de la imagen contra `constraints.txt`.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REQUIREMENTS_TXT = REPO_ROOT / "requirements.txt"
PYPROJECT_TOML = REPO_ROOT / "pyproject.toml"
CONSTRAINTS_TXT = REPO_ROOT / "constraints.txt"
DOCKERFILE = REPO_ROOT / "Dockerfile"

IMAGE_TAG = "contafis-assistant:dev"

# Paquetes de herramienta que pip instala consigo mismo y no forman parte del
# árbol de dependencias de la aplicación.
_TOOLING = {"pip", "setuptools", "wheel"}

# Divergencias documentadas (nombre canónico PEP 503 → motivo).
PYPROJECT_ONLY: dict[str, str] = {
    "langchain-core": "transitiva de langchain/langgraph; sin imports directos en src/",
    "openai": "transitiva de langchain-openai; src/ usa ChatOpenAI",
    "tenacity": "transitiva; sin imports directos en src/",
    "python-dateutil": "transitiva; sin imports directos en src/",
    "pyyaml": "transitiva; sin imports directos en src/",
    "orjson": "transitiva; sin imports directos en src/",
}
REQUIREMENTS_ONLY: dict[str, str] = {
    "langchain-community": "declarada; sin imports en src/ (candidata a poda en T10)",
    "rank-bm25": "BM25 propio en rag/bm25.py; sin imports",
    "pytest": "solo test",
    "pytest-mock": "solo test",
    "pillow": "solo test (tests/api/conftest.py, tests/unit/test_digitize.py)",
}

# Pin exacto: `nombre==versión` (o `===`), con marker opcional.
_PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(==|===)[^\s;]+(;.*)?$")


def canon(name: str) -> str:
    """Normaliza un nombre de paquete según PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(line: str) -> str | None:
    """Nombre canónico de un requisito PEP 508; corta extras ``[..]`` y markers ``;``."""
    line = line.split(";", 1)[0]
    line = line.split("[", 1)[0]
    match = re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*", line.strip())
    return canon(match.group(0)) if match else None


def _parse_requirements(path: Path) -> set[str]:
    """Nombres canónicos declarados en requirements.txt.

    Ignora comentarios y opciones de pip (líneas que empiezan con ``-``,
    como ``-c constraints.txt``).
    """
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = _requirement_name(line)
        if name:
            names.add(name)
    return names


def _parse_constraints(path: Path) -> dict[str, str]:
    """Mapa {nombre canónico: versión} de constraints.txt (formato pip freeze)."""
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(===|==)([^\s;]+)", line)
        assert match, f"constraints.txt: línea sin pin exacto: {line!r}"
        pins[canon(match.group(1))] = match.group(3)
    return pins


def _parse_pyproject(path: Path) -> set[str]:
    """Nombres canónicos de ``[project].dependencies`` en pyproject.toml."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    deps = data.get("project", {}).get("dependencies", [])
    names: set[str] = set()
    for dep in deps:
        name = _requirement_name(dep)
        if name:
            names.add(name)
    return names


def _constraint_lines(path: Path) -> list[str]:
    """Líneas no vacías de constraints.txt (sin comentarios)."""
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _dockerfile_stages(text: str) -> list[list[str]]:
    """Parte el Dockerfile por ``^FROM``; devuelve las líneas de cada etapa."""
    stages: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if re.match(r"^FROM\b", line, re.IGNORECASE):
            current = []
            stages.append(current)
            continue
        if current is not None:
            current.append(line)
    return stages


class TestConstraintsTxt:
    """constraints.txt: existencia, formato y limpieza."""

    def test_constraints_file_exists_and_is_utf8(self):
        assert CONSTRAINTS_TXT.exists(), "constraints.txt no existe"
        content = CONSTRAINTS_TXT.read_text(encoding="utf-8")  # falla si no es UTF-8
        assert content.strip(), "constraints.txt está vacío"
        assert "\x00" not in content
        # read_text normaliza CRLF por nuevas líneas universales: el CR se
        # verifica sobre los bytes crudos.
        assert b"\r" not in CONSTRAINTS_TXT.read_bytes(), (
            "constraints.txt tiene finales de línea CRLF; debe ser LF"
        )

    def test_every_constraint_is_exactly_pinned(self):
        lines = _constraint_lines(CONSTRAINTS_TXT)
        assert lines, "constraints.txt está vacío"
        for line in lines:
            assert _PIN_RE.match(line), (
                f"Línea sin pin exacto 'nombre==versión': {line!r}"
            )
            assert ">=" not in line and "~=" not in line and "*" not in line, (
                f"Specifier no exacto en constraints: {line!r}"
            )

    def test_constraints_has_no_paths_urls_or_hashes(self):
        forbidden = [" @ ", "file:", "://", "/", "\\", "--hash", "-e "]
        for line in _constraint_lines(CONSTRAINTS_TXT):
            for token in forbidden:
                assert token not in line, (
                    f"constraints.txt no debe contener {token!r}: {line!r}"
                )
            assert not re.match(r"^[A-Za-z]:", line), (
                f"Ruta de Windows en constraints: {line!r}"
            )

    def test_constraints_has_no_duplicate_packages(self):
        pins = _parse_constraints(CONSTRAINTS_TXT)
        lines = _constraint_lines(CONSTRAINTS_TXT)
        assert len(lines) == len(pins), (
            "constraints.txt tiene paquetes duplicados (comparación canónica "
            f"PEP 503): {len(lines)} líneas vs {len(pins)} paquetes únicos"
        )


class TestRequirementsAndPyproject:
    """Coherencia requirements.txt ↔ constraints.txt ↔ pyproject.toml."""

    def test_requirements_declares_constraints_first(self):
        assert CONSTRAINTS_TXT.exists(), "constraints.txt no existe"
        for line in REQUIREMENTS_TXT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                assert line.strip() == "-c constraints.txt", (
                    "La primera línea no vacía de requirements.txt debe ser "
                    f"'-c constraints.txt'; es: {line.strip()!r}"
                )
                return
        pytest.fail("requirements.txt está vacío")

    def test_every_requirements_package_is_pinned(self):
        req = _parse_requirements(REQUIREMENTS_TXT)
        constraints = set(_parse_constraints(CONSTRAINTS_TXT))
        unpinned = req - constraints
        assert unpinned == set(), (
            "Paquetes de requirements.txt sin pin en constraints.txt: "
            f"{sorted(unpinned)}"
        )

    def test_pyproject_runtime_deps_are_declared_in_requirements(self):
        pyproject = _parse_pyproject(PYPROJECT_TOML)
        req = _parse_requirements(REQUIREMENTS_TXT)
        missing = pyproject - set(PYPROJECT_ONLY) - req
        assert missing == set(), (
            "Dependencias runtime de pyproject.toml ausentes en "
            f"requirements.txt (y no documentadas en PYPROJECT_ONLY): "
            f"{sorted(missing)}"
        )

    def test_divergence_allowlists_are_disjoint_and_not_stale(self):
        req = _parse_requirements(REQUIREMENTS_TXT)
        pyproject = _parse_pyproject(PYPROJECT_TOML)

        overlap = set(PYPROJECT_ONLY) & set(REQUIREMENTS_ONLY)
        assert not overlap, f"Las allowlists se solapan: {sorted(overlap)}"

        stale_pyproject = sorted(
            name for name in PYPROJECT_ONLY
            if name in req or name not in pyproject
        )
        assert not stale_pyproject, (
            "PYPROJECT_ONLY desactualizada (ya no divergen; actualizá la "
            f"allowlist o los archivos): {stale_pyproject}"
        )

        stale_requirements = sorted(
            name for name in REQUIREMENTS_ONLY
            if name in pyproject or name not in req
        )
        assert not stale_requirements, (
            "REQUIREMENTS_ONLY desactualizada (ya no divergen; actualizá la "
            f"allowlist o los archivos): {stale_requirements}"
        )


class TestDockerfileAndImage:
    """El build de la imagen respeta constraints.txt en ambas etapas."""

    def test_dockerfile_installs_with_constraints_in_both_stages(self):
        stages = _dockerfile_stages(DOCKERFILE.read_text(encoding="utf-8"))
        assert len(stages) >= 2, "El Dockerfile debe tener builder y runtime"

        stages_with_pip = 0
        for stage in stages:
            for idx, line in enumerate(stage):
                if not (
                    re.match(r"^\s*RUN\b", line, re.IGNORECASE)
                    and "pip" in line
                    and "-r requirements.txt" in line
                ):
                    continue
                stages_with_pip += 1
                copy_before = [
                    prev
                    for prev in stage[:idx]
                    if re.match(r"^\s*COPY\b", prev, re.IGNORECASE)
                    and "constraints.txt" in prev
                ]
                assert copy_before, (
                    "Etapa con 'RUN ... pip ... -r requirements.txt' sin un "
                    f"COPY de constraints.txt antes:\n{chr(10).join(stage)}"
                )
        assert stages_with_pip >= 2, (
            "Se esperaban al menos 2 etapas que instalen con pip "
            f"(builder y runtime); encontradas: {stages_with_pip}"
        )

    @pytest.mark.docker
    @pytest.mark.skipif(
        os.environ.get("RUN_DOCKER_TESTS") != "1",
        reason="Requiere RUN_DOCKER_TESTS=1 y Docker disponible",
    )
    def test_image_freeze_matches_constraints(self):
        build = subprocess.run(
            ["docker", "build", "-t", IMAGE_TAG, "."],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=1200,
        )
        assert build.returncode == 0, (
            f"docker build falló (exit {build.returncode}):\n{build.stderr[-2000:]}"
        )

        freeze_run = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "python", IMAGE_TAG,
             "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert freeze_run.returncode == 0, (
            f"pip freeze en la imagen falló:\n{freeze_run.stderr[-2000:]}"
        )

        freeze: dict[str, str] = {}
        for line in freeze_run.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            assert " @ " not in line and not line.startswith("-e "), (
                f"pip freeze devolvió una línea no pineable: {line!r}"
            )
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(===|==)([^\s;]+)", line)
            assert match, f"Línea de pip freeze no parseable: {line!r}"
            name = canon(match.group(1))
            if name in _TOOLING:
                continue
            freeze[name] = match.group(3)

        constraints = _parse_constraints(CONSTRAINTS_TXT)
        assert freeze == constraints, (
            "El pip freeze de la imagen no coincide con constraints.txt. "
            f"En constraints pero no (o distinta versión) en la imagen: "
            f"{ {k: v for k, v in constraints.items() if freeze.get(k) != v} }. "
            f"En la imagen pero no (o distinta versión) en constraints: "
            f"{ {k: v for k, v in freeze.items() if constraints.get(k) != v} }."
        )
