# Runbook: Despliegue y Operaciones (Staging + Producción)

> **Servicio:** `contafis-assistant` (backend FastAPI + LangGraph)  
> **Plataforma:** Render (primario), Railway (alternativa)  
> **Health check plataforma:** `GET /livez` (sin auth)  
> **Auto-deploy:** Deshabilitado (manual via deploy hook en push a `main`)  
> **Versión de la spec:** Spec 004 — Dockerización y Deployment  

---

## 1. Resumen rápido (copy-paste para on-call)

```bash
# 1. VERIFICAR ESTADO
curl -s https://contafis-assistant-staging.onrender.com/livez   # → {"status":"alive"}
curl -s https://contafis-assistant-staging.onrender.com/readyz  # → {"status":"ready",...}

# 2. DEPLOY STAGING (manual)
# Render Dashboard → servicio contafis-assistant-staging → Manual Deploy → Deploy latest commit

# 3. DEPLOY PRODUCCIÓN (manual, tras validar staging)
# Render Dashboard → servicio contafis-assistant-prod → Manual Deploy → Deploy latest commit

# 4. ROLLBACK (cualquier entorno)
# Render Dashboard → servicio → Deploys → commit anterior → Deploy
# O: git revert HEAD && git push && Manual Deploy en Render

# 5. LOGS CLAVE
# Render Logs → buscar: "ChromaDB initialized", "LangSmith tracing enabled", "ScopeStartupError", "ODOO_READONLY_ATTESTED"
# LangSmith → proyecto "contafis-assistant" → última traza
```

---

## 2. Despliegue a Staging

### 2.1 Prerrequisitos

- Cuenta en [Render](https://render.com) con acceso al proyecto
- Imagen Docker construida y funcional (ver `Dockerfile`)
- Variables de entorno listadas abajo configuradas en el panel de Render
- Acceso a Odoo 19 (JSON/2) con API Key de solo lectura para staging (o inalcanzable → degraded)

### 2.2 Variables de Entorno (Panel Render → Environment)

> **Regla:** Todas las variables tienen `sync: false` en `render.yaml`.  
> Los **secretos** (claves API, passwords) **nunca** se versionan; se configuran solo en el panel.  
> Los **valores literales** también tienen `sync: false` para evitar sobrescrituras accidentales.

#### 2.2.1 Secretos (configurar en panel → "Add Environment Variable" → "Secret")

| Variable | Descripción | Ejemplo / Fuente |
|----------|-------------|------------------|
| `NVIDIA_API_KEY` | API Key de NVIDIA (para embeddings y LLM vía OpenAI-compatible) | Panel NVIDIA NGC |
| `LANGSMITH_API_KEY` | API Key de LangSmith (observabilidad) | Panel LangSmith |
| `ODOO_URL` | URL base del servidor Odoo (ej. `https://mi-empresa.odoo.com`) | Admin Odoo |
| `ODOO_DB` | Nombre de la base de datos Odoo | `contea` |
| `ODOO_API_KEY` | API Key de Odoo (usuario técnico, scope readonly) | Admin Odoo → Users → API Keys |
| `ODOO_PASSWORD` | Password del usuario técnico Odoo (fallback si no hay API Key) | Admin Odoo |
| `API_SHARED_SECRET` | Shared secret para autenticación API interna (generar: `openssl rand -hex 32`) | Generar nuevo |
| `OPENAI_API_KEY` | **Inyectar igual que `NVIDIA_API_KEY`** (la app usa cliente OpenAI-compatible contra NVIDIA) | Copiar de `NVIDIA_API_KEY` |

> ⚠️ **Importante:** `NVIDIA_API_KEY` y `OPENAI_API_KEY` deben tener **el mismo valor**. La app usa `OPENAI_API_KEY` internamente con `OPENAI_BASE_URL` apuntando a NVIDIA.

#### 2.2.2 Valores Literales (configurar en panel → "Add Environment Variable" → valor plano)

| Variable | Valor | Notas |
|----------|-------|-------|
| `LANGSMITH_PROJECT` | `contafis-assistant` | Proyecto LangSmith |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | Endpoint oficial |
| `LANGSMITH_TRACING` | `true` | Habilita tracing en staging |
| `LANGSMITH_HIDE_CONTENT` | `false` | Mostrar contenido en traces (útil para debug) |
| `ODOO_PROTOCOL` | `json2` | **Solo** JSON/2 soportado |
| `EMBEDDING_MODEL` | `nvidia/nemotron-3-embed-1b` | Modelo de embeddings NVIDIA |
| `EMBEDDING_DIMENSIONS` | `2048` | Dimensiones del vector |
| `APP_ENV` | `development` | Entorno de staging (D3: staging usa `development`) |
| `OPENAI_BASE_URL` | `https://integrate.api.nvidia.com/v1` | Endpoint NVIDIA OpenAI-compatible |
| `LLM_MODEL` | `meta/llama-3.2-90b-vision-instruct` | Modelo LLM principal |
| `CHROMA_PATH` | `./data/chroma` | Path interno del contenedor (sin disco persistente en Free tier) |
| `ODOO_READONLY_ATTESTED` | `false` | Staging no requiere atestación |
| `ODOO_TIMEOUT_SECONDS` | `15` | Timeout requests Odoo |
| `SCOPE_CHECK_TTL_SECONDS` | `300` | Cache de verificación de scope |
| `MAX_MESSAGE_CHARS` | `4000` | Límite chars por mensaje |
| `MAX_TOOL_CALLS` | `8` | Límite tool calls por turno |
| `RECURSION_LIMIT` | `20` | Límite recursión LangGraph |

### 2.3 Primer Despliegue (Manual)

1. **Conectar repositorio** en Render → New → Web Service → Connect GitHub/GitLab
2. **Configurar:**
   - Name: `contafis-assistant-staging`
   - Runtime: `Docker`
   - Dockerfile Path: `./Dockerfile`
   - Plan: `Free`
   - Auto-Deploy: **Off**
3. **Añadir variables de entorno** (sección 2.2)
4. **Crear servicio** → Render hará el primer build y deploy
5. **Verificar health check:** `GET https://contafis-assistant-staging.onrender.com/livez` → `200 OK`
6. **Verificar readiness:** `GET https://contafis-assistant-staging.onrender.com/readyz` → `200 OK` (corpus cargado)

### 2.4 Despliegues Subsecuentes (Manual)

> Auto-deploy está **deshabilitado**. Cada deploy requiere acción manual.

#### Opción A: Render Dashboard
1. Ir al servicio → **Manual Deploy** → **Deploy latest commit**
2. Esperar build + health check
3. Verificar logs de arranque (ver sección 6)

#### Opción B: `render` CLI (si configurado)
```bash
render deploy contafis-assistant-staging --confirm
```

### 2.5 Verificación Post-Despliegue (Smoke Tests)

Ejecutar tras cada deploy exitoso:

```bash
# 1. Health check (liveness)
curl -s https://contafis-assistant-staging.onrender.com/livez
# → {"status":"alive"}

# 2. Readiness (corpus cargado)
curl -s https://contafis-assistant-staging.onrender.com/readyz
# → {"status":"ready","corpus_version":"v1.2.3","chroma_count":1250}

# 3. Consulta simple (scope válido)
curl -s -X POST https://contafis-assistant-staging.onrender.com/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_SHARED_SECRET" \
  -d '{"message": "¿Qué es el IVA?", "session_id": "smoke-test-1"}'
# → Respuesta JSON con answer, citations, confidence

# 4. Verificar tracing en LangSmith
# Abrir proyecto "contafis-assistant" → última traza → revisar latencia, tokens, errores
```

**Criterios de éxito:**
- `/livez` y `/readyz` responden 200 en < 5s
- Chat responde en < 30s (primera request fría puede tardar más)
- No hay errores 5xx en logs
- Traza aparece en LangSmith con `LANGSMITH_PROJECT=contafis-assistant`

---

## 3. Despliegue a Producción

### 3.1 Prerrequisitos (Checklist de Promoción)

- [ ] Plan Render **Starter o superior** (requerido para disco persistente)
- [ ] Disco persistente creado y montado en `/var/data` (configurado en `render.yaml` → `disk`)
- [ ] `CHROMA_PATH=/var/data/chroma` y `BM25_INDEX_PATH=/var/data/bm25_index.json` en variables de entorno
- [ ] `preDeployCommand: "python -m scripts.ingest_if_needed"` habilitado en `render.yaml`
- [ ] PDFs subidos a `/var/data/corpus/` (ver §7)
- [ ] `/var/data/manifest.json` creado con hashes y páginas reales
- [ ] Odoo 19 **públicamente accesible desde Render** (TLS, allowlist IP egress de Render) — **OQ-4**
- [ ] API Key Odoo **dedicada y read-only** auditada y atestada
- [ ] `ODOO_READONLY_ATTESTED=true` (obligatorio en producción)

### 3.2 Variables de Entorno Producción (Diferencias vs Staging)

| Variable | Staging | Producción |
|----------|---------|------------|
| `APP_ENV` | `development` | `production` |
| `ODOO_READONLY_ATTESTED` | `false` | `true` |
| `CHROMA_PATH` | `./data/chroma` | `/var/data/chroma` |
| `BM25_INDEX_PATH` | (no configurado) | `/var/data/bm25_index.json` |
| `LANGSMITH_HIDE_CONTENT` | `false` | `true` |
| `LANGSMITH_TRACING` | `true` | `false` o `true` (según autorización OQ-7) |
| `numInstances` | `1` | `2+` (HA) |
| `autoDeploy` | `false` | `true` (opcional, tras validar) |

### 3.3 Primer Despliegue a Producción

1. **Crear nuevo servicio** en Render: `contafis-assistant-prod`
   - Plan: `Starter` (o superior)
   - Runtime: `Docker`
   - Dockerfile Path: `./Dockerfile`
   - Auto-Deploy: `false` (inicialmente)
   - Disk: Add Disk → Name: `corpus-data`, Mount Path: `/var/data`, Size: `1 GB` (o más)
2. **Configurar variables de entorno** (sección 3.2 + secretos de producción)
3. **Subir PDFs y manifiesto al disco** (ver §7)
4. **Habilitar `preDeployCommand`** en `render.yaml` y redplegar, o ejecutar ingesta manual:
   ```bash
   # En Render Shell (SSH) del servicio
   python -m scripts.ingest_if_needed --manifest /var/data/manifest.json --chroma-path /var/data/chroma
   ```
5. **Verificar readiness:**
   ```bash
   curl -s https://contafis-assistant-prod.onrender.com/readyz
   # → {"status":"ready","corpus_version":1,"chroma_count":314,"corpus_status":"ok"}
   ```
6. **Smoke test chat** con API Key de producción
7. **Habilitar autoDeploy** en `render.yaml` solo tras validar estabilidad

---

## 4. Corpus Normativo — Layout en `/var/data` y Ingesta

> **Nota:** En Render **Free tier** NO hay disco persistente. El corpus se reconstruye en cada deploy desde `data/manifest.json` y `data/corpus/` incluidos en la imagen Docker. `GET /readyz` devolverá `corpus_status: missing` o `empty` hasta que se ejecute la ingesta. Esto es **esperado y correcto** para staging.

> En planes **paid (Starter+)**, Render provee un **Persistent Disk** montado en `/var/data`. El flujo de producción usa `preDeployCommand` en `render.yaml` para ejecutar `scripts/ingest_if_needed.py` de forma idempotente antes de arrancar el servicio.

### 4.1 Layout esperado en disco persistente (`/var/data`)

```
/var/data/
├── manifest.json          # Copia del manifiesto (versión + hashes)
├── bm25_index.json        # Índice BM25 generado por la ingesta
└── chroma/                # Base de datos ChromaDB (vector store)
    ├── chroma.sqlite3
    └── <collection_uuid>/
```

**Variables de entorno en producción (paid):**

| Variable | Valor |
|---|---|
| `CHROMA_PATH` | `/var/data/chroma` |
| `BM25_INDEX_PATH` | `/var/data/bm25_index.json` |

El manifiesto fuente sigue versionado en el repo como `data/manifest.json` (plantilla en `data/manifest.example.json`).  
Los PDFs **no** se versionan en git; se colocan manualmente en `/var/data/corpus/` en el disco persistente.

### 4.2 Obtener y colocar los PDFs en producción

1. **Descargar** los PDFs oficiales desde:
   - Gaceta Oficial de Venezuela: `gacetaoficial.gob.ve`
   - Asamblea Nacional: `asambleanacionaldevenezuela.org`
   - SENIAT: `seniat.gob.ve` → Normativa Legal

2. **Subir al disco persistente** (una sola vez, o tras borrar el disco):
   - Opción A: SSH al servicio Render (paid plans) → `scp` o `rsync`
   - Opción B: `render` CLI con `render disks upload` (si disponible)
   - Opción C: Script one-off que descargue de bucket privado (fuera de scope T7)

3. **Estructura en disco:**
   ```
   /var/data/corpus/
   ├── ley-islr.pdf
   └── providencia-seniat.pdf
   ```

### 4.3 Calcular SHA-256 y páginas para el manifiesto

```powershell
# SHA-256 de cada PDF (copiar el valor en minúsculas al manifest)
Get-FileHash -Algorithm SHA256 /var/data/corpus/ley-islr.pdf | Select-Object Hash
```

```python
# Número de páginas y verificación de texto extraíble
from pypdf import PdfReader
r = PdfReader("/var/data/corpus/ley-islr.pdf")
print(f"Páginas: {len(r.pages)}")
# Verificar que extrae texto (no PDF escaneado sin OCR)
sample = r.pages[0].extract_text()
print(f"Primeros 200 chars: {sample[:200]}")
```

### 4.4 Actualizar `manifest.json` en disco persistente

1. Copiar `data/manifest.example.json` → `/var/data/manifest.json`
2. Rellenar `sha256` reales de cada PDF (calculados arriba)
3. Rellenar `pages` reales
4. En `bm25_index_sha256` poner `0` * 64 (placeholder; la ingesta lo sobrescribe)

### 4.5 Ejecutar la ingesta (idempotente)

```bash
# En el contenedor (via SSH o render shell)
python -m scripts.ingest_if_needed
# O con rutas explícitas:
python -m scripts.ingest_if_needed --manifest /var/data/manifest.json --chroma-path /var/data/chroma
```

**Salida esperada (primera vez):**
```
[ingest_if_needed] manifest=/var/data/manifest.json
[ingest_if_needed] chroma_path=/var/data/chroma
[ingest_if_needed] collection=contafis_normativa_v1
[ingest_if_needed] Estado: chroma: missing (count=0)
[ingest_if_needed] Ejecutando ingesta...
documentos=2 chunks=314 bm25_version=1 sha256=a1b2c3d4...
```

**Salida esperada (deploy subsiguiente sin cambios):**
```
[ingest_if_needed] Estado: ok: version=1, chroma_count=314
[ingest_if_needed] Corpus sincronizado → saltando ingesta (idempotente)
```

### 4.6 Verificar que el corpus quedó cargado

```bash
curl -s https://contafis-assistant-prod.onrender.com/readyz
# → {"status":"ready","corpus_version":1,"chroma_count":314,"corpus_status":"ok"}
```

### 4.7 Rotar modelo de embeddings (reingesta obligatoria)

Si cambias `EMBEDDING_MODEL` o `EMBEDDING_DIMENSIONS`:

1. Actualizar variables de entorno en Render
2. Actualizar `dimensions` en `/var/data/manifest.json`
3. Borrar colección Chroma vieja: `rm -rf /var/data/chroma`
4. Ejecutar ingesta: `python -m scripts.ingest_if_needed --force`
5. Copiar el nuevo `sha256` del BM25 al `manifest.json`
5. Reiniciar servicio (auto-deploy o manual)

---

## 5. Verificación Post-Deploy (Staging y Producción)

### 5.1 Endpoints de verificación

| Endpoint | Auth | Qué valida | SLA |
|---|---|---|---|
| `GET /livez` | **Ninguna** | Proceso vivo, HTTP responde | < 2s |
| `GET /readyz` | `X-API-Key` | Corpus cargado, app lista | < 5s |
| `GET /health` | `X-API-Key` | Estado completo (Odoo, scope, corpus, workers) | < 10s |
| `POST /v1/chat` | `X-API-Key` + `X-User-Token` | Flujo completo end-to-end | < 30s |

### 5.2 Smoke test completo (copiar/pegar)

```bash
BASE=https://contafis-assistant-staging.onrender.com  # o -prod
SECRET=<API_SHARED_SECRET_DEL_PANEL>
TOKEN=<ODOO_USER_TOKEN_PARA_TEST>

# 1. Liveness
curl -s "$BASE/livez" | jq .

# 2. Readiness
curl -s -H "X-API-Key: $SECRET" "$BASE/readyz" | jq .

# 2. Health completo
curl -s -H "X-API-Key: $SECRET" "$BASE/health" | jq .

# 3. Chat (requiere token de usuario Odoo válido)
curl -s -X POST "$BASE/v1/chat" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SECRET" \
  -H "X-User-Token: $TOKEN" \
  -d '{"message": "¿Cuál es la tasa de IVA general?", "session_id": "smoke-'$(date +%s)'"}' | jq .
```

### 5.3 Criterios de éxito

- `/livez` → 200 `{"status":"alive"}` en < 2s
- `/readyz` → 200 con `corpus_status` en `{ok, missing, empty, mismatch}` en < 5s
- `/health` → 200 con `status` en `{ok, degraded}` (nunca `unhealthy` salvo producción con scope unsafe)
- Chat → respuesta coherente, `citations` presente, `confidence` > 0.5
- LangSmith → traza visible con latencia < 10s, sin errores rojos

---

## 6. Logs, Métricas y Observabilidad

### 6.1 Logs de arranque esperados (orden típico)

```
[+] Building dependencies...
[+] Loading corpus from manifest...
[+] ChromaDB initialized at /var/data/chroma
[+] BM25 index loaded (v1, 314 docs)
[+] Odoo client configured (json2, timeout=15s)
[+] LLM client configured (NVIDIA, model=meta/llama-3.2-90b-vision-instruct)
[+] Embedding client configured (NVIDIA, model=nvidia/nemotron-3-embed-1b)
[+] LangSmith tracing enabled (project=contafis-assistant)
[+] API server starting on 0.0.0.0:8000
[+] Health check endpoint: /livez
[+] Readiness endpoint: /readyz
```

### 6.2 Qué buscar en Render Logs

| Patrón | Significado | Acción |
|---|---|---|
| `ScopeStartupError` | Arranque abortado en producción por scope unsafe | Verificar Odoo accesible, key read-only, `ODOO_READONLY_ATTESTED=true` |
| `ODOO_READONLY_ATTESTED` no definido / `false` en prod | Validación de Settings falla | Establecer `true` en panel Render |
| `corpus_status: missing` | Sin manifiesto en disco | Subir `manifest.json` + PDFs, ejecutar ingesta (§4.5) |
| `corpus_status: mismatch` | PDF falta o hash incorrecto | Verificar PDFs en `/var/data/corpus/`, recalcular hashes, actualizar manifiesto |
| `ChromaDB` permission denied | UID sin escritura en disco | Verificar permisos mount; job ingesta con UID correcto |
| `RECURSION_LIMIT` validation error | `MAX_TOOL_CALLS` muy alto vs `RECURSION_LIMIT` | Ajustar: `RECURSION_LIMIT >= 2*MAX_TOOL_CALLS+4` |
| `OPENAI_API_KEY` / `NVIDIA_API_KEY` mismatch | Valores distintos en panel | Ambos deben ser **idénticos**; copiar valor de NVIDIA a ambos |

### 6.3 Métricas en LangSmith

- **Proyecto:** `contafis-assistant`
- **Filtros útiles:** `error: true`, `latency > 10s`, `token_usage > 50k`
- **Alertas recomendadas:**
  - Tasa de error > 5% en 5 min → PagerDuty
  - Latencia p95 > 30s → Slack #contafis-assistant
  - `corpus_status != ok` en `/health` → Slack #contafis-assistant

---

## 7. Incidentes Comunes y Soluciones

| Incidente | Síntoma | Causa Raíz | Solución |
|---|---|---|---|
| **401 Unauthorized en `/health` / `/chat`** | `{"detail":"Unauthorized"}` | Falta o error en `X-API-Key` | Verificar `API_SHARED_SECRET` en panel Render = header enviado |
| **`corpus_status: missing`** | `/health` degraded, RAG no responde | No hay `manifest.json` en `/var/data` | Subir manifiesto + PDFs, ejecutar ingesta (§4.5) |
| **`corpus_status: mismatch`** | `/health` degraded, warning `corpus_unavailable` | PDF declarado no existe o SHA-256 no coincide | Verificar PDFs en `/var/data/corpus/`, recalcular hashes, actualizar manifiesto |
| **Crash loop: contenedor sale código ≠ 0** | Render reinicia sin parar | `ScopeStartupError` en producción (H2) | Verificar: Odoo público accesible, key read-only válida, `ODOO_READONLY_ATTESTED=true` |
| **`ODOO_URL` connection timeout** | `/health` `odoo_available: false` | Red / firewall / allowlist IP | Verificar que Render puede alcanzar Odoo (IP egress de Render en allowlist Odoo) |
| **`OPENAI_API_KEY` / `NVIDIA_API_KEY` mismatch** | Arranque falla por `openai_api_key` faltante | Valores distintos en panel | Ambos deben ser **idénticos**; copiar valor de NVIDIA a ambos |
| **ChromaDB permission denied** | Ingesta falla, logs `PermissionError` | UID 10001 sin escritura en `/var/data` | Verificar permisos del mount; en Render paid el disco pertenece al usuario del contenedor |
| **Build > 10 min / Imagen > 1.8 GB** | CI timeout / costos altos | Dependencias pesadas, sin cache | Verificar `constraints.txt`, cache de wheels en CI, multi-stage correcto |

---

## 8. Rollback

### 8.1 Procedimiento (Render Dashboard)

1. Ir al servicio en Render Dashboard (`contafis-assistant-staging` o `contafis-assistant-prod`)
2. Pestaña **Deploys**
3. Identificar el deploy anterior estable (commit SHA visible)
4. Clic en **Deploy** junto a ese commit
5. Esperar build + health check (`/livez` 200)
6. Verificar `/readyz` y smoke test chat

### 8.2 Procedimiento (Git revert + push)

```bash
git revert HEAD
git push
# Luego Manual Deploy en Render Dashboard
```

### 8.3 Qué pasa con el disco persistente

- **El disco `/var/data` SE CONSERVA** entre redeploys y rollbacks
- El corpus (ChromaDB, BM25, manifiesto, PDFs) **no se borra** al hacer rollback de la imagen
- Si el rollback es por un cambio de modelo de embeddings que rompió el corpus:
  - El disco tiene la versión anterior del corpus (compatible con la imagen anterior)
  - **No hace falta reingestar** tras rollback de imagen
- Si el rollback es por corrupción del corpus:
  - Ejecutar `rm -rf /var/data/chroma` y reingestar (§4.5) tras el rollback

### 8.4 Rollback de variables de entorno

Si el incidente es por una variable mal configurada:
1. Corregir la variable en el panel Render
2. **No hace falta redeploy**: Render reinicia el servicio automáticamente al cambiar env vars
3. Verificar `/livez` y `/health`

---

## 9. Runbook de Promoción Staging → Producción (Copy-Paste)

```markdown
# Checklist Promoción a Producción

## Pre-requisitos
- [ ] Staging verde: `/livez`, `/readyz`, smoke test chat OK
- [ ] Odoo prod públicamente accesible desde Render (TLS, allowlist IP egress) — **OQ-4**
- [ ] API Key Odoo prod: dedicada, read-only, auditada
- [ ] Plan Render Starter+ con disco 1 GB+ montado en `/var/data`
- [ ] PDFs normativos en `/var/data/corpus/`
- [ ] `manifest.json` en `/var/data/` con hashes y páginas reales
- [ ] `ODOO_READONLY_ATTESTED=true` confirmado

## Variables a cambiar en panel Render (prod)
- [ ] `APP_ENV=production`
- [ ] `ODOO_READONLY_ATTESTED=true`
- [ ] `CHROMA_PATH=/var/data/chroma`
- [ ] `BM25_INDEX_PATH=/var/data/bm25_index.json`
- [ ] `LANGSMITH_HIDE_CONTENT=true`
- [ ] `LANGSMITH_TRACING=false` (o según OQ-7)
- [ ] `numInstances: 2` en render.yaml / panel
- [ ] `preDeployCommand: "python -m scripts.ingest_if_needed"` habilitado

## Ejecución
- [ ] Crear servicio `contafis-assistant-prod` en Render (o usar blueprint)
- [ ] Configurar variables arriba
- [ ] Ejecutar ingesta: `python -m scripts.ingest_if_needed` (Render Shell)
- [ ] Verificar `/readyz` → `corpus_status: ok`
- [ ] Smoke test chat con token usuario prod
- [ ] Habilitar `autoDeploy: true` en render.yaml (opcional, tras 24h estables)

## Post-promoción
- [ ] Monitorear LangSmith 1h
- [ ] Verificar alertas silenciosas
- [ ] Documentar commit SHA desplegado en CHANGELOG
```

---

## 10. Contactos y Escalación

| Rol | Contacto | Canal |
|-----|----------|-------|
| DevOps / Infra | @devops-team | #infra-render (Slack) |
| Backend / App | @backend-team | #contafis-assistant (Slack) |
| Odoo Admin | @odoo-admin | #odoo-integraciones (Slack) |
| On-call | Rota semanal | PagerDuty / Opsgenie |

---

## 11. Referencias

- **Spec 004:** `specs/004-deployment/spec.md` (requisitos EARS, decisiones, AC)
- **Tasks:** `specs/004-deployment/tasks.md` (T1–T10 completadas)
- **Dockerfile:** `Dockerfile` (multi-stage, usuario no-root, HEALTHCHECK en `/livez`)
- **docker-compose.yml:** `docker-compose.yml` (solo desarrollo local)
- **render.yaml:** `render.yaml` (Blueprint staging + prod comentado)
- **CI/CD:** `.github/workflows/ci.yml` (jobs test, image, deploy)
- **Ingesta:** `scripts/ingest_if_needed.py`, `scripts/ingest_corpus.py`, `data/corpus/README.md`
- **Constitución:** `docs/constitution.md` (6 principios innegociables)
- **AGENTS.md:** `AGENTS.md` (reglas TDD, tipado, observabilidad)