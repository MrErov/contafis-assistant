# Imagen del Asistente Fiscal ContaFis (Spec 004, T1).
# Multi-stage: el toolchain (gcc/make) vive solo en `builder`; el `runtime`
# final no contiene compiladores, cachés de pip ni secretos (RF-DEP-01..03).

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Toolchain solo en esta etapa: algunas dependencias pueden no publicar wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Usuario sin privilegios con UID fijo (RF-DEP-02); sin sudo ni docker.sock.
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Instalación offline desde wheels precompiladas, sin resolver red ni compilar.
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Copias explícitas con dueño appuser; jamás `COPY . .` (H9: sin .env en capas).
COPY --chown=appuser:appuser src /app/src
COPY --chown=appuser:appuser scripts /app/scripts

EXPOSE 8000

USER appuser

# Healthcheck contra /health con la API key del entorno (H1: /health va
# protegido por X-API-Key). python:3.12-slim no trae curl/wget: se usa
# urllib de la stdlib. Respete el PORT inyectado; falla cerrado ante 401/503.
# T4 migrará este chequeo a /livez (sin header).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request; port = os.environ.get('PORT', '8000'); key = os.environ.get('API_SHARED_SECRET', ''); req = urllib.request.Request('http://127.0.0.1:%s/health' % port, headers={'X-API-Key': key}); sys.exit(0 if urllib.request.urlopen(req, timeout=4).status == 200 else 1)"]

# 1 worker es obligatorio (H3): ThreadStore vive en la memoria del proceso.
# El puerto sale siempre de PORT con fallback 8000 (RF-DEP-05, §11.3).
CMD ["sh", "-c", "uvicorn contafis_assistant.main:app --app-dir src --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
