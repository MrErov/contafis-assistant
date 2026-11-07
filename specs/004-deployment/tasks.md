# Tasks — Spec 004: Dockerización y despliegue (Render, Railway alternativa)

> Checklist ejecutable. Marcar `[ ]` → `[x]` al completar.
> **T1 está acotada por diseño**: Dockerfile + compose + `.dockerignore` + test TDD de
> contenedor. Todo lo demás (CI/CD real, `render.yaml`, corpus, hardening) va después.
>
> Reglas de ejecución:
> - TDD: el test se escribe primero y se ve fallar por la razón correcta.
> - Sin archivos temporales (`fix_*.py`, `check_*.py`, `debug.py`).
> - Sin `.env` en git; sin secretos en YAML, imágenes ni logs.
> - Comando de tests del repo: `pytest -q` (`AGENTS.md`, `.github/workflows/ci.yml:42`).
> - Cada tarea termina con commit Conventional Commits si la suite sigue verde.
> - No se declara «hecho» sin `git status` limpio de archivos inesperados.

---

## Resumen y orden

| Tarea | Alcance | Est. | Depende de |
|---|---|---|---|
| T1 | Dockerfile + compose + `.dockerignore` + smoke test de `/health` | 2–3 h | — |
| T2 | Reproducibilidad de dependencias (`constraints.txt`) | 2 h | T1 |
| T3 | Job `image` en CI (build + smoke + escaneo de secretos) | 1–2 h | T1, T2 |
| T4 | `GET /livez` sin auth + allowlist del middleware | 1–2 h | T1 |
| T5 | `render.yaml` + runbook de staging | 2 h | T4 |
| T6 | Deploy hook en CI, solo `main` | 1 h | T3, T5 |
| T7 | Corpus persistente + ingesta one-off idempotente | 2–3 h | T5 |
| T8 | Promoción a producción y checklist de atestación | 1–2 h | T5, T7 |
| T9 | Alternativa Railway (opcional) | 1–2 h | T4 |
| T10 | Runbook final, rollback, observabilidad y docs | 1–2 h | T6, T8 |

---

## F1: Contenedor local

- [x] **T1 — Dockerfile + `docker-compose.yml` + `.dockerignore` + smoke test de `/health`**

  **Alcance (una sola cosa):** que la API arranque en un contenedor y responda
  `GET /health`.

  **Archivos:**
  - `Dockerfile` (nuevo): multi-stage `python:3.12-slim`.
    - `builder`: `build-essential`, `pip wheel --wheel-dir /wheels -r requirements.txt`.
    - `runtime`: usuario `appuser` (UID 10001), `pip install --no-index --find-links=/wheels -r requirements.txt`,
      `COPY src /app/src`, `COPY scripts /app/scripts`.
    - `ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1`.
    - `WORKDIR /app`, `EXPOSE 8000`, `USER appuser`.
    - `HEALTHCHECK` contra `/health` con `X-API-Key: $API_SHARED_SECRET`.
    - `CMD` con `sh -c`: `uvicorn contafis_assistant.main:app --app-dir src --host 0.0.0.0 --port ${PORT:-8000} --workers 1`.
  - `docker-compose.yml` (nuevo): `build: .`, `ports: ["8000:8000"]`,
    `env_file: [.env]`, `environment: API_HOST=0.0.0.0`,
    `volumes: ["./data:/app/data"]`, `restart: unless-stopped`.
  - `.dockerignore` (nuevo): `.git`, `.github`, `.venv`, `venv`, `env`,
    `__pycache__`, `*.py[cod]`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`,
    `htmlcov`, `.coverage`, `tests`, `notebooks`, `specs`, `docs`, `data`,
    `.env`, `.env.*`, `pytest-of-*`, `pip-*`, `*.log`.
  - `tests/docker/test_container_health.py` (nuevo) con `pytestmark = pytest.mark.docker`
    y skip si `RUN_DOCKER_TESTS != "1"`.
  - `pyproject.toml`: registrar `markers = ["docker: ..."]` (obligatorio por
    `--strict-markers`, `pyproject.toml:45-50`).

  **Tests (primero, en rojo):**
  - `test_image_builds`
  - `test_container_health_responds` — `/health` 200 con `X-API-Key`;
    `status in {ok, degraded}`, `corpus_status == "missing"`, `worker_count == 1`.
  - `test_container_requires_api_key_when_secret_set` — 401 sin header.
  - `test_container_runs_as_non_root` — `id -u != 0`.
  - `test_image_has_no_env_file` — `test ! -e /app/.env`.
  - `test_port_variable_is_honored` — `PORT=18001`.
  - `test_healthcheck_is_defined`.

  **Hecho cuando:**
  - `docker build -t contafis-assistant:dev .` termina en 0.
  - `RUN_DOCKER_TESTS=1 pytest tests/docker/test_container_health.py -v` → todos verdes.
  - `docker run --rm contafis-assistant:dev id -u` devuelve un UID ≠ `0`.
  - `docker run --rm contafis-assistant:dev sh -c 'test ! -e /app/.env'` → exit 0.
  - `docker compose config` es válido y no imprime secretos.
  - `pytest -q` sigue en 0 failed con conteo ≥ 686.
  - `git status` no muestra `.env`, `data/` ni artefactos.

  **Notas:** el contenedor de prueba usa `APP_ENV=development`,
  `ODOO_URL=http://127.0.0.1:9` y `API_SHARED_SECRET=test-container-secret`.
  Si el `.env` local no define `API_SHARED_SECRET`, compose fallará al validar
  `Settings` (`config.py:112-115`): documentarlo en el README de T5, no commitear `.env`.

---

## F2: Reproducibilidad

- [x] **T2 — Fijar dependencias y reconciliar `requirements.txt` con `pyproject.toml`**

  **Alcance:** que dos builds del mismo commit instalen exactamente las mismas versiones.

  **Archivos:**
  - `constraints.txt` (nuevo): versiones exactas de todo el árbol (generado con
    `pip freeze` en un entorno limpio; incluye transitivas).
  - `requirements.txt`: añadir `-c constraints.txt` al inicio.
  - `Dockerfile`: `pip wheel -r requirements.txt` y `pip install --no-index --find-links`
    respetan el constraints.
  - `pyproject.toml`: alinear dependencias declaradas (o documentar por qué
    `requirements.txt` es la fuente de verdad del runtime).
  - `tests/deployment/test_dependencies.py` (nuevo): compara el set de paquetes de
    `requirements.txt` contra `pyproject.toml` y falla ante divergencia no documentada.

  **Hecho cuando:**
  - Dos `docker build --no-cache` consecutivos producen el mismo
    `pip freeze` (ordernado y comparado).
  - `pytest -q` verde (incluido `tests/deployment/test_dependencies.py`).
  - `constraints.txt` no contiene hashes de ruedas locales ni rutas absolutas.
  - El Dockerfile instala sin resolver versiones en la etapa `runtime`.

  **Notas:** no se usa `pip-tools` si no está ya en el entorno; `pip freeze` es
  suficiente y no añade dependencias. `pillow` queda como dependencia de test si se
  confirma que `api/digitize.py` no lo importa en runtime.

---

## F3: CI de imagen

- [x] **T3 — Job `image` en GitHub Actions (build + smoke + secretos + tamaño)**

  **Alcance:** que un PR no pueda mergear si la imagen no construye, no arranca o filtra
  secretos.

  **Archivos:**
  - `.github/workflows/ci.yml`: nuevo job `image` con `needs: test`,
    `timeout-minutes: 25`.
  - Pasos: `docker build`, `RUN_DOCKER_TESTS=1 pytest tests/docker -m docker`,
    `docker run --rm image id -u`, `test ! -e /app/.env`,
    `docker history --no-trunc | grep -iE '(api[_-]?key|secret)=[A-Za-z0-9]'` debe fallar
    el job si hay coincidencias, y verificación de tamaño con
    `docker image inspect`.
  - `permissions: contents: read`.

  **Hecho cuando:**
  - El workflow pasa en un PR de prueba y en `main`.
  - El job `image` dura ≤ 25 min (objetivo ≤ 10 min con caché).
  - Un `.dockerignore` roto (sin `.env`) hace fallar el job (probado localmente, sin
    commitear el roto).
  - No se imprime ningún valor de secreto en los logs.
  - `pytest -q` del job `test` sigue verde.

---

## F4: Liveness (desbloquea Render)

- [x] **T4 — `GET /livez` sin autenticación y allowlist exacta del middleware**

  **Alcance:** endpoint de liveness que Render pueda consultar sin headers, sin relajar
  `/health`.

  **Archivos:**
  - `src/contafis_assistant/api/routes.py`: ruta `GET /livez` que devuelve
    `{"status": "alive", "app_version": __version__}` sin tocar `runtime`.
  - `src/contafis_assistant/main.py`: en `_api_key_middleware`, permitir únicamente
    `request.method == "GET"` y `request.url.path == "/livez"` antes de `guard.check`.
  - `tests/api/test_liveness.py` (nuevo): 200 sin header con secreto configurado;
    200 antes de `runtime.startup()`; `POST /livez` → 405; payload sin datos sensibles;
    `/health` sigue 401 sin header.
  - `Dockerfile`: `HEALTHCHECK` migrado de `/health` a `/livez` (sin header).

  **Hecho cuando:**
  - `pytest tests/api/test_liveness.py -q` → verde.
  - `tests/api/test_health.py` y `tests/api/test_chat_api.py:163-196` sin cambios y verdes.
  - `curl -i http://127.0.0.1:8000/livez` → 200 sin `X-API-Key` con
    `API_SHARED_SECRET` definido.
  - `curl -i http://127.0.0.1:8000/health` → 401 sin `X-API-Key`.
  - El payload de `/livez` no contiene `scope`, `corpus`, rutas ni claves.

  **Notas de seguridad:** la allowlist es por método + ruta exacta; nada de prefijos ni
  regex. `/livez` no debe consultar Odoo, Chroma ni el `ThreadStore`.

---

## F5: Blueprint de Render

- [ ] **T5 — `render.yaml` + `docs/DEPLOY.md` para staging**

  **Alcance:** desplegar staging en Render con el contrato de env correcto y health
  check funcional.

  **Archivos:**
  - `render.yaml` (nuevo): servicio `web` Docker, `dockerfilePath: ./Dockerfile`,
    `healthCheckPath: /livez`, `numInstances: 1`, `autoDeploy: false`,
    `envVars` con `sync: false` para secretos y valores literales solo para las no
    secretas, `disk: {name: corpus-data, mountPath: /var/data, sizeGB: 1}`.
  - `docs/DEPLOY.md` (nuevo): tabla de variables de §11.2 del spec, diferencias
    staging/producción, cómo cargar secretos en el dashboard, cómo verificar `/livez`
    y `/health` con `X-API-Key`.
  - `tests/deployment/test_env_contract.py` (nuevo): todas las claves de `render.yaml`
    existen en `Settings.model_fields` (o son `sync`); ningún `NVIDIA_API_KEY` como
    variable de app; la tabla de `docs/DEPLOY.md` cubre todos los campos de `Settings`.

  **Hecho cuando:**
  - `render.yaml` es YAML válido y no contiene valores secretos.
  - Un deploy manual de staging responde `GET /livez` 200 sin header.
  - `GET /health` con `X-API-Key` responde 200 `degraded` con `APP_ENV=development` y
    Odoo inalcanzable (`odoo_available: false`, `corpus_status: "missing"`).
  - `pytest tests/deployment -q` → verde.
  - `docs/DEPLOY.md` permite a otra persona desplegar staging sin leer el código.

  **Decisión registrada:** staging = `APP_ENV=development` + `API_SHARED_SECRET`
  (no existe `staging` en el enum, `config.py:39`).

---

## F6: Deploy hook

- [ ] **T6 — Job `deploy` por hook, solo en `main`**

  **Alcance:** que el deploy se dispare automáticamente solo tras CI verde en `main`.

  **Archivos:**
  - `.github/workflows/ci.yml`: job `deploy` con `needs: [test, image]`,
    `if: github.event_name == 'push' && github.ref == 'refs/heads/main'`,
    `environment: production`, `concurrency: {group: deploy-main, cancel-in-progress: false}`.
  - Paso único: `curl -fsS --retry 3 --retry-delay 5 -o /dev/null -X POST "$RENDER_DEPLOY_HOOK_URL"`.
  - `docs/DEPLOY.md`: cómo crear el secret en GitHub Environments y cómo rotarlo.

  **Hecho cuando:**
  - Un push a `main` con `test` e `image` verdes ejecuta `deploy` exactamente una vez.
  - Un PR muestra `deploy` como `skipped`.
  - Los logs no contienen la URL del hook (revisado manualmente con `grep`).
  - `RENDER_DEPLOY_HOOK_URL` no aparece en ningún archivo del repo (`git grep`).
  - El deploy de Render termina y `/livez` responde 200.

---

## F7: Corpus persistente

- [ ] **T7 — Disco `/var/data`, manifiesto y ingesta one-off idempotente**

  **Alcance:** que el corpus llegue a `corpus_status: ok` sin versionar PDFs ni
  re-embeder en cada deploy.

  **Archivos:**
  - `data/manifest.example.json` (nuevo): plantilla válida según
    `rag/manifest.py:16-36` con `dimensions`, `bm25_index_version`,
    `bm25_index_sha256` y documentos con `file: "corpus/..."`.
  - `scripts/ingest_if_needed.py` (nuevo, sin secretos): compara versión/hash del
    manifiesto y el `count()` de la colección; si coinciden, sale con 0 sin embeber;
    si no, invoca `contafis_assistant.rag.ingest_cli.main`.
  - `render.yaml`: `preDeployCommand` idempotente **o** definición de un Job one-off
    (elegir tras verificar el soporte vigente de discos en jobs; OQ-2).
  - `docs/DEPLOY.md`: layout de `/var/data`, cómo obtener PDFs, cómo calcular SHA-256 y
    páginas, cómo copiar `bm25_index_sha256` al manifiesto y cómo reingresar al cambiar
    `EMBEDDING_*`.
  - `tests/deployment/test_corpus_layout.py` (nuevo): manifiesto con PDF ausente →
    `mismatch`; sin manifiesto → `missing`; layout completo → `ok` (con `tmp_path`,
    sin red).

  **Hecho cuando:**
  - En staging con volumen: `/health` → 200 `ok` (o `degraded` solo por Odoo) y
    `corpus_status: "ok"`, `chroma_available: true`, `bm25_available: true`.
  - Sin volumen: `/health` → 200 `degraded`, `corpus_status: "missing"`.
  - Con manifiesto y un PDF borrado: `/health` → 200 `degraded`,
    `corpus_status: "mismatch"` (confirma H6).
  - Ejecutar la ingesta dos veces seguidas: la segunda no llama al embedder y el hash
    del índice no cambia.
  - `tests/deployment/test_corpus_layout.py` verde; suite completa ≥ 686 sin fallos.
  - Los PDFs no están en git (`git status` limpio).

  **Bloqueante conocido:** sin un canal para depositar PDFs en el disco (bucket privado,
  repo privado o descarga firmada), `corpus_status: ok` no es alcanzable. Decidir OQ-2
  antes de cerrar esta tarea.

---

## F8: Producción

- [ ] **T8 — Promoción a producción con Odoo público y atestación**

  **Alcance:** documentar y verificar las condiciones que hacen que producción arranque
  (y que fallen cerrado cuando no se cumplen).

  **Archivos:**
  - `docs/DEPLOY.md`: checklist de promoción (Odoo URL pública/TLS, key read-only
    dedicada, `ODOO_DB`, `ODOO_READONLY_ATTESTED=true`,
    `APP_ENV=production`, `API_SHARED_SECRET`, corpus en disco).
  - `tests/deployment/test_production_gates.py` (nuevo): `Settings` rechaza
    `APP_ENV=production` sin atestación; `ScopeService.startup()` lanza
    `ScopeStartupError` con snapshot `scope_unsafe` en producción; en desarrollo el
    mismo snapshot arranca.
  - `render.yaml`: variables de producción con `sync: false` para secretos.

  **Hecho cuando:**
  - `pytest tests/deployment/test_production_gates.py -q` → verde.
  - En producción con key read-only, Odoo accesible y attested:
    `/health` → 200 `ok`, `scope_state: "scope_ok"`, `worker_count: 1`.
  - Prueba negativa documentada: `APP_ENV=production` + Odoo inalcanzable → el
    contenedor sale ≠ 0 (crash loop esperado, no se «arregla» bajando a development).
  - Prueba negativa: `APP_ENV=production` sin `ODOO_READONLY_ATTESTED` → error de
    validación al arrancar.
  - El checklist está firmado/revisado por una segunda persona.

---

## F9: Alternativa Railway

- [ ] **T9 — Deploy de staging en Railway con la misma imagen**

  **Alcance (opcional):** demostrar portabilidad sin cambios de código.

  **Archivos:**
  - `docs/DEPLOY.md`: sección Railway (build desde `Dockerfile`, `PORT` inyectado,
    volumen `/var/data`, variables equivalentes, secretos en el panel).

  **Hecho cuando:**
  - Railway construye desde el mismo `Dockerfile` (mismo commit).
  - `GET /livez` → 200 y `GET /health` con `X-API-Key` → 200 `degraded`.
  - No se añadieron archivos de configuración específicos de Railway al repo (o se
    justifica uno mínimo).
  - La sección de Railway no duplica la tabla de variables: la referencia.

---

## F10: Cierre operativo

- [ ] **T10 — Runbook final, rollback, observabilidad y sincronización de docs**

  **Alcance:** dejar operable el despliegue y la documentación consistente.

  **Archivos:**
  - `docs/DEPLOY.md`: runbook final (deploy, verificación, logs, métricas, incidentes
    comunes: 401 por key, `corpus_status` distinto de `ok`, crash loop por Odoo).
  - `docs/DEPLOY.md`: rollback (redeploy del commit/imagen anterior; qué pasa con el
    disco, que se conserva).
  - `README.md`: reemplazar el badge «Dockerizado: listo para desplegar»
    (`README.md:42`) por el procedimiento real o un enlace a `docs/DEPLOY.md`.
  - `SPEC.md`: corregir la numeración (OQ-5) y enlazar esta spec.
  - `docs/memory.md`: registrar decisiones (D3 staging=development, D5 `/livez`,
    H6 PDFs en disco, `constraints.txt`) y pendientes.

  **Hecho cuando:**
  - El runbook permite a alguien sin contexto hacer deploy, verificar y hacer rollback.
  - Se ejecuta un rollback de prueba en staging y `/livez` vuelve a 200 con la versión
    anterior.
  - `SPEC.md`, `README.md` y `docs/memory.md` no se contradicen con esta spec.
  - `git status` limpio; sin archivos temporales; sin `.env` versionado.
  - Suite completa en verde con el mismo conteo o superior al inicio de la spec.

---

## Verificación final de la spec (Definition of Done)

- [ ] AC-01 … AC-25 del `spec.md` verificados o explícitamente diferidos con motivo.
- [ ] `pytest -q` → 0 failed, conteo ≥ 686.
- [ ] `RUN_DOCKER_TESTS=1 pytest -m docker -q` → verde.
- [ ] `docker build` reproducible con `constraints.txt`.
- [ ] `/livez` 200 sin auth; `/health` 401 sin `X-API-Key`.
- [ ] Deploy en PR = `skipped`; deploy en `main` = ejecutado una vez.
- [ ] `render.yaml` sin secretos; `git grep RENDER_DEPLOY_HOOK_URL` vacío.
- [ ] `docs/DEPLOY.md` cubre staging, producción, corpus y rollback.
- [ ] Sin `.env`, PDFs ni `data/chroma/` en git.
