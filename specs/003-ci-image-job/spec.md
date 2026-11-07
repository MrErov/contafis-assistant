# Spec: GitHub Actions `image` Job (T3)

**Proyecto:** ContaFis AI Assistant
**Versión:** 1.0
**Fecha:** 2026-11-06
**Estado:** Borrador

---

## Contexto

- T1 (Dockerfile multi-stage) ✅ implementado
- T2 (constraints.txt + requirements.txt con `-c constraints.txt`) ✅ implementado — 779 tests pasan
- Tests Docker existen en `tests/docker/test_container_health.py` con marker `docker`
- `.dockerignore` existe y excluye `.env`, `.venv`, `data/`, `tests/`, etc.
- El job `image` se añade a `.github/workflows/ci.yml` (archivo nuevo o existente)

---

## Requisitos (EARS)

### RF-01: Job `image` existe y depende de `test`
**DADO** un workflow de CI  
**CUANDO** se ejecuta el pipeline  
**ENTONCES** debe existir un job llamado `image` con `needs: [test]`

### RF-02: Configuración básica del job
**DADO** el job `image`  
**CUANDO** se define  
**ENTONCES** debe tener:
- `timeout-minutes: 25`
- `permissions: { contents: read }`
- `runs-on: ubuntu-latest`

### RF-03: Caché de Docker (pip + layers)
**DADO** el paso de build  
**CUANDO** se ejecuta `docker build`  
**ENTONCES** debe usar GitHub Actions cache para:
- Capas de Docker (`/tmp/.buildx-cache`)
- Caché de pip (`~/.cache/pip`) mediante `--mount=type=cache` en el Dockerfile (ya presente: `PIP_NO_CACHE_DIR=1` en builder, pero el cache de GitHub Actions acelera rebuilds)

### RF-04: Build de la imagen
**DADO** el job `image`  
**CUANDO** se ejecuta el step de build  
**ENTONCES** debe correr:
```bash
docker build -t contafis-assistant:test .
```

### RF-05: Smoke tests del contenedor
**DADO** la imagen construida  
**CUANDO** se ejecutan los tests Docker  
**ENTONCES** debe correr:
```bash
RUN_DOCKER_TESTS=1 pytest tests/docker -m docker
```
**NOTA:** Requiere Docker en el runner (disponible en `ubuntu-latest`).

### RF-06: Verificación de usuario no root
**DADO** la imagen construida  
**CUANDO** se verifica el usuario  
**ENTONCES** debe correr:
```bash
docker run --rm contafis-assistant:test id -u
```
y validar que la salida **no es `0`** (UID 10001 esperado).

### RF-07: Verificación de `.dockerignore` (ausencia de `.env`)
**DADO** la imagen construida  
**CUANDO** se verifica la ausencia de `.env`  
**ENTONCES** debe correr:
```bash
docker run --rm contafis-assistant:test sh -c 'test ! -e /app/.env'
```
y validar exit code `0`.

### RF-08: Gate de secretos en capas de imagen
**DADO** la imagen construida  
**CUANDO** se escanean las capas  
**ENTONCES** debe correr:
```bash
docker history --no-trunc contafis-assistant:test | grep -iE '(api[_-]?key|secret)=[A-Za-z0-9]'
```
y **fallar (exit code != 0)** si encuentra coincidencias (es decir, no debe haber secretos en capas).

### RF-09: Verificación de tamaño de imagen
**DADO** la imagen construida  
**CUANDO** se inspecciona el tamaño  
**ENTONCES** debe correr:
```bash
docker image inspect contafis-assistant:test --format '{{.Size}}'
```
y validar que el tamaño en bytes **≤ 1_800_000_000** (1.8 GB).

---

## Variables de Entorno Necesarias

| Variable | Valor | Propósito |
|----------|-------|-----------|
| `DOCKER_BUILDKIT` | `1` | Habilita BuildKit para caché y `--mount=type=cache` |
| `BUILDKIT_PROGRESS` | `plain` | Logs legibles en CI |
| `RUN_DOCKER_TESTS` | `1` | Activa los tests marcados con `@pytest.mark.docker` |

**NO se requieren secretos** para este job:
- El build usa solo archivos del repo (Dockerfile, requirements.txt, constraints.txt, src/, scripts/)
- Los tests Docker usan env degradado con valores dummy (ver `CONTAINER_ENV` en `tests/docker/test_container_health.py`)
- No hay credenciales reales de Odoo/OpenAI/LangSmith en el build ni en los tests de smoke

---

## Estructura YAML Exacta del Job `image`

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    # ... job existente de tests unitarios (T2) ...
    # Debe completar antes de que 'image' empiece

  image:
    name: Docker Image Build & Smoke Tests
    needs: [test]
    timeout-minutes: 25
    permissions:
      contents: read
    runs-on: ubuntu-latest
    env:
      DOCKER_BUILDKIT: 1
      BUILDKIT_PROGRESS: plain
      RUN_DOCKER_TESTS: 1
    steps:
      # --- Cache de capas Docker (GitHub Actions cache) ---
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Cache Docker layers
        uses: actions/cache@v4
        with:
          path: /tmp/.buildx-cache
          key: ${{ runner.os }}-buildx-${{ github.sha }}
          restore-keys: |
            ${{ runner.os }}-buildx-

      # --- Checkout del código ---
      - name: Checkout repository
        uses: actions/checkout@v4

      # --- Build de la imagen ---
      - name: Build Docker image
        run: |
          docker build -t contafis-assistant:test .

      # --- Smoke tests del contenedor (pytest -m docker) ---
      - name: Run container smoke tests
        run: |
          RUN_DOCKER_TESTS=1 pytest tests/docker -m docker -v

      # --- Verificación: usuario no root (UID != 0) ---
      - name: Verify non-root user
        run: |
          UID=$(docker run --rm contafis-assistant:test id -u)
          echo "Container UID: $UID"
          if [ "$UID" = "0" ]; then
            echo "ERROR: Container runs as root (UID 0)"
            exit 1
          fi
          if [ "$UID" != "10001" ]; then
            echo "WARNING: Expected UID 10001, got $UID"
          fi

      # --- Verificación: .dockerignore excluye .env ---
      - name: Verify .dockerignore excludes .env
        run: |
          docker run --rm contafis-assistant:test sh -c 'test ! -e /app/.env'

      # --- Gate: sin secretos en capas (api_key, secret, etc.) ---
      - name: Scan image layers for secrets
        run: |
          if docker history --no-trunc contafis-assistant:test | grep -iE '(api[_-]?key|secret)=[A-Za-z0-9]'; then
            echo "ERROR: Potential secrets found in image layers"
            exit 1
          fi
          echo "No secrets detected in image layers"

      # --- Verificación: tamaño de imagen ≤ 1.8 GB ---
      - name: Verify image size budget
        run: |
          SIZE=$(docker image inspect contafis-assistant:test --format '{{.Size}}')
          echo "Image size: $SIZE bytes"
          MAX_SIZE=1800000000
          if [ "$SIZE" -gt "$MAX_SIZE" ]; then
            echo "ERROR: Image size $SIZE bytes exceeds budget of $MAX_SIZE bytes (1.8 GB)"
            exit 1
          fi
          echo "Image size within budget"
```

---

## Criterios de Aceptación (AC)

| ID | Descripción |
|----|-------------|
| AC-01 | Job `image` aparece en Actions y se ejecuta tras `test` |
| AC-02 | Build completa en < 25 min (timeout) |
| AC-03 | `pytest tests/docker -m docker` pasa (smoke tests verdes) |
| AC-04 | `docker run --rm contafis-assistant:test id -u` devuelve `10001` (no `0`) |
| AC-05 | `docker run --rm contafis-assistant:test sh -c 'test ! -e /app/.env'` exit code 0 |
| AC-06 | `docker history --no-trunc \|\| grep ...` no encuentra secretos (exit code 1 del grep = éxito) |
| AC-07 | `docker image inspect --format '{{.Size}}'` ≤ 1_800_000_000 bytes |
| AC-08 | No se usan secrets de GitHub en este job (solo `contents: read`) |
| AC-09 | Caché de Docker layers funciona (rebuilds posteriores más rápidos) |

---

## Notas de Implementación

1. **Orden de steps:** Los steps de verificación (non-root, .env, secretos, tamaño) pueden correr en paralelo teóricamente, pero en YAML secuencial es más simple y legible. El tiempo total sigue dentro de 25 min.

2. **Cache de pip:** El Dockerfile ya usa `PIP_NO_CACHE_DIR=1` en la etapa `builder` y wheels precompilados. El cache de GitHub Actions (`actions/cache@v4` en `/tmp/.buildx-cache`) cachea las capas de BuildKit, lo que incluye las wheels. No se necesita cache separada de `~/.cache/pip`.

3. **BuildKit:** `DOCKER_BUILDKIT=1` y `docker/setup-buildx-action@v3` son necesarios para que `--mount=type=cache` funcione en el Dockerfile (aunque el Dockerfile actual no lo use explícitamente, Buildx acelera el build multi-stage).

4. **Permisos mínimos:** `permissions: { contents: read }` es suficiente porque:
   - `actions/checkout@v4` necesita leer el repo
   - No se hace push a registry, no se usan secrets, no se escribe en el repo

5. **Variables dummy en tests Docker:** Los tests en `tests/docker/test_container_health.py` definen su propio `CONTAINER_ENV` con valores dummy (`ODOO_URL: http://127.0.0.1:9`, `OPENAI_API_KEY: dummy`, etc.). No requieren configuración adicional en CI.

---

## Referencias

- Dockerfile: `Dockerfile` (multi-stage, UID 10001, HEALTHCHECK, no .env)
- Tests Docker: `tests/docker/test_container_health.py` (marker `docker`, `RUN_DOCKER_TESTS=1`)
- .dockerignore: `.dockerignore` (excluye .env, .venv, data/, tests/, etc.)
- Constraints: `constraints.txt` + `requirements.txt` con `-c constraints.txt`