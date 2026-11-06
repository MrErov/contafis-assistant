"""Smoke test del contenedor Docker (Spec 004, T1).

Se ejecuta solo con ``RUN_DOCKER_TESTS=1`` (RNF-DEP-10): construye la imagen
una única vez por sesión, levanta un contenedor con env de desarrollo degradado
y verifica ``/health``, el usuario no root, la ausencia de ``.env`` en la
imagen, el gate de ``X-API-Key`` y el ``HEALTHCHECK``. Requiere Docker en
ejecución (Docker Desktop en Windows).

El env del contenedor apunta Odoo a ``http://127.0.0.1:9`` (fallo inmediato) y
no define corpus, por lo que ``/health`` debe responder 200 ``degraded`` con
``corpus_status: missing`` (RF-DEP-09, RF-DEP-13).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_TAG = "contafis-assistant:dev"

HOST_PORT = 18001
API_SECRET = "test-container-secret"
CONTAINER_NAME = "contafis-t1-smoke"
HEALTH_URL = f"http://127.0.0.1:{HOST_PORT}/health"

# Env mínimo para que Settings valide y la app arranque degradada.
# API_HOST=0.0.0.0 activa el gate de API_SHARED_SECRET (config.py, D11).
CONTAINER_ENV = {
    "APP_ENV": "development",
    "ODOO_URL": "http://127.0.0.1:9",
    "ODOO_API_KEY": "dummy",
    "OPENAI_API_KEY": "dummy",
    "OPENAI_BASE_URL": "https://integrate.api.nvidia.com/v1",
    "LLM_MODEL": "dummy",
    "EMBEDDING_MODEL": "dummy",
    "EMBEDDING_DIMENSIONS": "2048",
    "CHROMA_PATH": "/tmp/chroma",
    "BM25_INDEX_PATH": "/tmp/bm25_index.json",
    "API_HOST": "0.0.0.0",
    "API_SHARED_SECRET": API_SECRET,
    "LANGSMITH_TRACING": "false",
    "PORT": str(HOST_PORT),
}

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("RUN_DOCKER_TESTS") != "1",
        reason="Requiere RUN_DOCKER_TESTS=1 y Docker en ejecución",
    ),
]


def _docker(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=REPO_ROOT,
    )


def _wait_for_health(timeout_seconds: float = 120.0) -> httpx.Response:
    """Espera a que ``/health`` responda 200.

    El arranque importa ``chromadb`` y es lento: el timeout es generoso
    (60-120 s) antes de rendirse y volcar los logs del contenedor.
    """
    deadline = time.monotonic() + timeout_seconds
    last_error: object = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                HEALTH_URL,
                headers={"X-API-Key": API_SECRET},
                timeout=10.0,
            )
            if response.status_code == 200:
                return response
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = repr(exc)
        time.sleep(2.0)
    logs = _docker("logs", "--tail", "80", CONTAINER_NAME)
    pytest.fail(
        f"/health no respondió 200 en {timeout_seconds:.0f}s "
        f"(último error: {last_error})\n"
        f"--- docker logs (tail) ---\n{(logs.stdout + logs.stderr)[-3000:]}"
    )


@pytest.fixture(scope="session")
def image() -> str:
    """Construye la imagen UNA sola vez por sesión (RF-DEP-01)."""
    result = _docker("build", "-t", IMAGE_TAG, ".", timeout=1800.0)
    assert result.returncode == 0, (
        "docker build falló:\n"
        f"--- stdout (tail) ---\n{result.stdout[-3000:]}\n"
        f"--- stderr (tail) ---\n{result.stderr[-3000:]}"
    )
    return IMAGE_TAG


@pytest.fixture(scope="session")
def container(image: str) -> str:
    """Contenedor en ejecución con env de desarrollo degradado y PORT=18001."""
    _docker("rm", "-f", CONTAINER_NAME)
    run_args = [
        "docker",
        "run",
        "-d",
        "--name",
        CONTAINER_NAME,
        "-p",
        f"127.0.0.1:{HOST_PORT}:{HOST_PORT}",
    ]
    for key, value in CONTAINER_ENV.items():
        run_args += ["-e", f"{key}={value}"]
    run_args.append(image)
    proc = subprocess.run(run_args, capture_output=True, text=True, timeout=120.0)
    assert proc.returncode == 0, f"docker run falló: {proc.stderr}"
    container_id = proc.stdout.strip()
    try:
        _wait_for_health()  # gate de arranque para todos los tests HTTP
        yield container_id
    finally:
        _docker("rm", "-f", CONTAINER_NAME)


@pytest.fixture(scope="session")
def health_payload(container: str) -> dict:
    response = httpx.get(
        HEALTH_URL, headers={"X-API-Key": API_SECRET}, timeout=10.0
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_image_builds(image: str) -> None:
    """La imagen quedó etiquetada y es inspeccionable (AC-01)."""
    proc = _docker("image", "inspect", "--format", "{{.Id}}", image)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().startswith("sha256:")


def test_container_health_responds(health_payload: dict) -> None:
    """`/health` 200 degradado con Odoo caído y sin corpus (AC-08, RF-DEP-06/09/13)."""
    assert health_payload["status"] in {"ok", "degraded"}
    assert health_payload["corpus_status"] == "missing"
    assert health_payload["worker_count"] == 1
    assert health_payload["odoo_available"] is False


def test_container_requires_api_key_when_secret_set(container: str) -> None:
    """Con API_SHARED_SECRET definido, `/health` sin header responde 401 (AC-07)."""
    response = httpx.get(HEALTH_URL, timeout=10.0)
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


def test_container_runs_as_non_root(image: str) -> None:
    """El proceso corre como appuser (UID != 0) (AC-02, RF-DEP-02)."""
    proc = _docker("run", "--rm", image, "id", "-u")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() != "0"


def test_image_has_no_env_file(image: str) -> None:
    """La imagen no contiene ``/app/.env`` (AC-03, RF-DEP-03)."""
    proc = _docker("run", "--rm", image, "sh", "-c", "test ! -e /app/.env")
    assert proc.returncode == 0, proc.stderr


def test_dockerignore_excludes_sensitive_paths() -> None:
    """`.dockerignore` excluye secretos, datos y artefactos (H9, RF-DEP-03)."""
    content = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    required = [
        ".env",
        ".env.*",
        ".venv",
        ".git",
        "__pycache__",
        "tests",
        "specs",
        "docs",
        "notebooks",
        "data",
        "*.pdf",
        "pytest-of-*",
        "pip-*",
    ]
    missing = [pattern for pattern in required if pattern not in content]
    assert not missing, f".dockerignore debe excluir: {missing}"


def test_port_variable_is_honored(container: str) -> None:
    """Con PORT=18001 el proceso escucha en 18001 y NO en 8000 (AC-05, RF-DEP-05)."""
    listening = _listening_ports(container)
    assert HOST_PORT in listening, (
        f"el contenedor no escucha en {HOST_PORT}; puertos: {listening}"
    )
    assert 8000 not in listening, f"el proceso ignoró PORT y escucha en 8000: {listening}"
    response = httpx.get(
        HEALTH_URL, headers={"X-API-Key": API_SECRET}, timeout=10.0
    )
    assert response.status_code == 200


def _listening_ports(container_id: str) -> set[int]:
    """Puertos en estado LISTEN dentro del contenedor vía /proc/net/tcp(6)."""
    ports: set[int] = set()
    for proc_file in ("/proc/net/tcp", "/proc/net/tcp6"):
        proc = _docker("exec", container_id, "cat", proc_file)
        if proc.returncode != 0:
            continue
        for line in proc.stdout.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 4 or fields[3] != "0A":  # 0A = LISTEN
                continue
            hex_port = fields[1].rsplit(":", 1)[-1]
            ports.add(int(hex_port, 16))
    return ports


def test_healthcheck_is_defined(image: str) -> None:
    """La imagen declara HEALTHCHECK (RF-DEP-01; migrará a /livez en T4)."""
    proc = _docker(
        "image", "inspect", "--format", "{{json .Config.Healthcheck}}", image
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() not in {"", "null"}, "HEALTHCHECK no definido"
    assert "Test" in proc.stdout


def test_image_size_within_budget(image: str) -> None:
    """Tamaño sin comprimir <= 1.8 GB (RNF-DEP-05, AC-24)."""
    proc = _docker("image", "inspect", "--format", "{{.Size}}", image)
    assert proc.returncode == 0, proc.stderr
    size_bytes = int(proc.stdout.strip())
    assert size_bytes <= 1_800_000_000, (
        f"imagen {size_bytes} bytes supera el presupuesto de 1.8 GB"
    )
