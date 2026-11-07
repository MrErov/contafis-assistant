# Runbook: Despliegue a Staging (Render)

> **Servicio:** `contafis-assistant-staging`  
> **Runtime:** Docker (Free tier)  
> **Health check:** `GET /livez`  
> **Auto-deploy:** Deshabilitado (manual)

---

## 1. Prerrequisitos

- Cuenta en [Render](https://render.com) con acceso al proyecto
- Imagen Docker construida y funcional (ver `Dockerfile`)
- Variables de entorno listadas en `render.yaml` configuradas en el panel de Render
- Acceso a Odoo 19 (JSON/2) con API Key de solo lectura para staging

---

## 2. Variables de Entorno (Panel Render → Environment)

> **Regla:** Todas las variables tienen `sync: false` en `render.yaml`.  
> Los **secretos** (claves API, passwords) **nunca** se versionan; se configuran solo en el panel.  
> Los **valores literales** también tienen `sync: false` para evitar sobrescrituras accidentales.

### 2.1 Secretos (configurar en panel → "Add Environment Variable" → "Secret")

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

### 2.2 Valores Literales (configurar en panel → "Add Environment Variable" → valor plano)

| Variable | Valor | Notas |
|----------|-------|-------|
| `LANGSMITH_PROJECT` | `contafis-assistant` | Proyecto LangSmith |
| `LANGSMITH_ENDPOINT` | `https://api.smith.langchain.com` | Endpoint oficial |
| `LANGSMITH_TRACING` | `true` | Habilita tracing en staging |
| `LANGSMITH_HIDE_CONTENT` | `false` | Mostrar contenido en traces (útil para debug) |
| `ODOO_PROTOCOL` | `json2` | **Solo** JSON/2 soportado |
| `EMBEDDING_MODEL` | `nvidia/nemotron-3-embed-1b` | Modelo de embeddings NVIDIA |
| `EMBEDDING_DIMENSIONS` | `2048` | Dimensiones del vector |
| `APP_ENV` | `development` | Entorno de staging |
| `OPENAI_BASE_URL` | `https://integrate.api.nvidia.com/v1` | Endpoint NVIDIA OpenAI-compatible |
| `LLM_MODEL` | `meta/llama-3.2-90b-vision-instruct` | Modelo LLM principal |
| `CHROMA_PATH` | `./data/chroma` | Path interno del contenedor |
| `ODOO_READONLY_ATTESTED` | `false` | Staging no requiere atestación |
| `ODOO_TIMEOUT_SECONDS` | `15` | Timeout requests Odoo |
| `SCOPE_CHECK_TTL_SECONDS` | `300` | Cache de verificación de scope |
| `MAX_MESSAGE_CHARS` | `4000` | Límite chars por mensaje |
| `MAX_TOOL_CALLS` | `8` | Límite tool calls por turno |
| `RECURSION_LIMIT` | `20` | Límite recursión LangGraph |

---

## 3. Primer Despliegue (Manual)

1. **Conectar repositorio** en Render → New → Web Service → Connect GitHub/GitLab
2. **Configurar:**
   - Name: `contafis-assistant-staging`
   - Runtime: `Docker`
   - Dockerfile Path: `./Dockerfile`
   - Plan: `Free`
   - Auto-Deploy: **Off**
3. **Añadir variables de entorno** (sección 2)
4. **Crear servicio** → Render hará el primer build y deploy
5. **Verificar health check:** `GET https://contafis-assistant-staging.onrender.com/livez` → `200 OK`
6. **Verificar readiness:** `GET https://contafis-assistant-staging.onrender.com/readyz` → `200 OK` (corpus cargado)

---

## 4. Despliegues Subsecuentes (Manual)

> Auto-deploy está **deshabilitado**. Cada deploy requiere acción manual.

### Opción A: Render Dashboard
1. Ir al servicio → **Manual Deploy** → **Deploy latest commit**
2. Esperar build + health check
3. Verificar logs de arranque (ver sección 6)

### Opción B: `render` CLI (si configurado)
```bash
render deploy contafis-assistant-staging --confirm
```

---

## 5. Verificación Post-Despliegue (Smoke Tests)

Ejecutar tras cada deploy exitoso:

```bash
# 1. Health check
curl -s https://contafis-assistant-staging.onrender.com/livez
# → {"status":"ok"}

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

## 6. Logs y Debugging

### Logs de arranque esperados (orden típico)
```
[+] Building dependencies...
[+] Loading corpus from manifest...
[+] ChromaDB initialized at ./data/chroma
[+] BM25 index loaded (v1, 1250 docs)
[+] Odoo client configured (json2, timeout=15s)
[+] LLM client configured (NVIDIA, model=meta/llama-3.2-90b-vision-instruct)
[+] Embedding client configured (NVIDIA, model=nvidia/nemotron-3-embed-1b)
[+] LangSmith tracing enabled (project=contafis-assistant)
[+] API server starting on 0.0.0.0:8000
[+] Health check endpoint: /livez
[+] Readiness endpoint: /readyz
```

### Errores comunes y soluciones

| Error | Causa | Solución |
|-------|-------|----------|
| `ODOO_URL` connection timeout | Red / firewall | Verificar que Render puede alcanzar Odoo (IP allowlist) |
| `ODOO_API_KEY` invalid | Credenciales erróneas | Regenerar API Key en Odoo, actualizar en Render |
| `ChromaDB` permission denied | Volumen no persistente | Free tier no persiste; corpus se reconstruye cada deploy (OK para staging) |
| `OPENAI_API_KEY` / `NVIDIA_API_KEY` mismatch | Valores distintos | Ambos deben ser **idénticos** en panel Render |
| `RECURSION_LIMIT` validation error | `MAX_TOOL_CALLS` muy alto | `RECURSION_LIMIT >= 2*MAX_TOOL_CALLS+4` (actual: 20 >= 2*8+4=20 ✓) |
| `langsmith_tracing` None | `APP_ENV` no reconocido | Verificar `APP_ENV=development` en panel |

---

## 7. Rollback

Render Free tier no tiene rollback automático de imagen. Opciones:

1. **Re-deploy commit anterior:**
   - Dashboard → Deploys → commit anterior → **Deploy**
2. **Git revert + push + manual deploy:**
   ```bash
   git revert HEAD
   git push
   # Luego Manual Deploy en Render
   ```

> ⚠️ Los datos de ChromaDB se pierden en cada deploy (Free tier sin disco persistente). El corpus se reconstruye desde el manifest en `data/corpus/manifest.json`.

---

## 8. Migración a Producción (Referencia)

Cuando se pase a plan de pago con disco persistente:

- Cambiar `APP_ENV=production`
- Establecer `ODOO_READONLY_ATTESTED=true` (requerido en prod)
- Habilitar `autoDeploy: true` en `render.yaml` (opcional)
- Configurar `CHROMA_PATH` en disco persistente (Render Disk)
- Añadir `numInstances: 2+` para HA

---

## 9. Contactos y Escalación

| Rol | Contacto | Canal |
|-----|----------|-------|
| DevOps / Infra | @devops-team | #infra-render (Slack) |
| Backend / App | @backend-team | #contafis-assistant (Slack) |
| Odoo Admin | @odoo-admin | #odoo-integraciones (Slack) |
| On-call | Rota semanal | PagerDuty / Opsgenie |

---

## 10. Checklist de Deploy (Copiar/pegar en PR o ticket)

- [ ] Variables de entorno actualizadas en panel Render (sección 2)
- [ ] `docker build` local pasa sin errores
- [ ] Tests unitarios pasan (`pytest tests/ -x`)
- [ ] Deploy manual ejecutado en Render
- [ ] `/livez` → 200 OK
- [ ] `/readyz` → 200 OK + corpus cargado
- [ ] Smoke test chat → respuesta válida
- [ ] Traza visible en LangSmith
- [ ] Logs sin errores 5xx ni warnings críticos
- [ ] Rollback plan confirmado (commit anterior identificable)

---

## 11. Deploy Automático desde CI (GitHub Actions → Render)

El pipeline CI/CD dispara el deploy a Render **solo** cuando:
- Hay un `push` a la rama `main` (no en PRs)
- Los jobs `test` e `image` terminan exitosos (verdes)

El disparo se hace mediante **Deploy Hook** de Render: una URL secreta que acepta `POST` para iniciar un deploy manual.

### 11.1 Obtener el Deploy Hook URL en Render Dashboard

1. En Render Dashboard, selecciona el servicio `contafis-assistant-staging` (o producción)
2. Ve a **Settings** → **Deploy Hook**
3. Copia la URL completa (formato: `https://api.render.com/deploy/srv-XXXXXX?key=YYYYYY`)
4. **No** la compartas ni la comitees; es un secreto equivalente a una API key

> ⚠️ El hook no requiere `RENDER_API_KEY`; la URL **contiene** el token. Trátala como secreto.

### 11.2 Crear el secret `RENDER_DEPLOY_HOOK_URL` en GitHub

1. En el repo GitHub: **Settings** → **Secrets and variables** → **Actions**
2. Pestaña **Environments** → selecciona **production** (o créalo si no existe)
3. **Environment secrets** → **Add secret**
   - Name: `RENDER_DEPLOY_HOOK_URL`
   - Value: pega la URL copiada del paso 11.1
4. Save

> El workflow usa `environment: production`, por lo que el secret **debe** estar en el Environment `production`, no en "Repository secrets".

### 11.3 Rotar el Deploy Hook (regenerar + actualizar)

Si sospechas que la URL se filtró o por política de rotación periódica:

1. **En Render Dashboard** (servicio → Settings → Deploy Hook):
   - Clic en **Regenerate** → se genera una nueva URL, la anterior queda inválida inmediatamente
   - Copia la nueva URL
2. **En GitHub** (Settings → Environments → production → Environment secrets):
   - Edita el secret `RENDER_DEPLOY_HOOK_URL` → pega la nueva URL → Save
3. **Verifica**: haz un push trivial a `main` (ej. `git commit --allow-empty -m "chore: trigger deploy" && git push`) y confirma en Actions que el job `deploy` pasa y en Render que el deploy inicia

> No hay ventana de vulnerabilidad: la URL vieja deja de funcionar al instante al regenerar.

---

## 12. Corpus Normativo \u2014 Layout en `/var/data` y Ingesta (Producci\u00f3n)

> **Nota:** En Render **Free tier** NO hay disco persistente. El corpus se reconstruye
> en cada deploy desde `data/manifest.json` y `data/corpus/` incluidos en la imagen Docker.
> `GET /readyz` devolver\u00e1 `corpus_status: missing` o `empty` hasta que se ejecute la ingesta.
> Esto es **esperado y correcto** para staging.

> En planes **paid (Starter+)**, Render provee un **Persistent Disk** montado en `/var/data`.
> El flujo de producci\u00f3n usa `preDeployCommand` en `render.yaml` para ejecutar
> `scripts/ingest_if_needed.py` de forma idempotente antes de arrancar el servicio.

### 12.1 Layout esperado en disco persistente (`/var/data`)

```
/var/data/
├── manifest.json          # Copia del manifiesto (versi\u00f3n + hashes)
├── bm25_index.json        # \u00cdndice BM25 generado por la ingesta
└── chroma/                # Base de datos ChromaDB (vector store)
    ├── chroma.sqlite3
    └── <collection_uuid>/
```

**Variables de entorno en producci\u00f3n (paid):**
| Variable | Valor |
|---|---|
| `CHROMA_PATH` | `/var/data/chroma` |
| `BM25_INDEX_PATH` | `/var/data/bm25_index.json` |

El manifiesto fuente sigue versionado en el repo como `data/manifest.json` (plantilla en `data/manifest.example.json`).
Los PDFs **no** se versionan en git; se colocan manualmente en `/var/data/corpus/` en el disco persistente.

### 12.2 Obtener y colocar los PDFs en producci\u00f3n

1. **Descargar** los PDFs oficiales desde:
   - Gaceta Oficial de Venezuela: `gacetaoficial.gob.ve`
   - Asamblea Nacional: `asambleanacionaldevenezuela.org`
   - SENIAT: `seniat.gob.ve` \u2192 Normativa Legal

2. **Subir al disco persistente** (una sola vez, o tras borrar el disco):
   - Opción A: SSH al servicio Render (paid plans) \u2192 `scp` o `rsync`
   - Opción B: `render` CLI con `render disks upload` (si disponible)
   - Opción C: Script one-off que descargue de bucket privado (fuera de scope T7)

3. **Estructura en disco:**
   ```
   /var/data/corpus/
   ├── ley-islr.pdf
   └── providencia-seniat.pdf
   ```

### 12.3 Calcular SHA-256 y p\u00e1ginas para el manifiesto

```powershell
# SHA-256 de cada PDF (copiar el valor en min\u00fasculas al manifest)
Get-FileHash -Algorithm SHA256 /var/data/corpus/ley-islr.pdf | Select-Object Hash
```

```python
# N\u00famero de p\u00e1ginas y verificaci\u00f3n de texto extra\u00edble
from pypdf import PdfReader
r = PdfReader("/var/data/corpus/ley-islr.pdf")
print(f"P\u00e1ginas: {len(r.pages)}")
# Verificar que extrae texto (no PDF escaneado sin OCR)
sample = r.pages[0].extract_text()
print(f"Primeros 200 chars: {sample[:200]}")
```

### 12.4 Actualizar `manifest.json` en disco persistente

1. Copiar `data/manifest.example.json` \u2192 `/var/data/manifest.json`
2. Rellenar `sha256` reales de cada PDF (calculados arriba)
3. Rellenar `pages` reales
4. En `bm25_index_sha256` poner `0` * 64 (placeholder; la ingesta lo sobrescribe)

### 12.5 Ejecutar la ingesta (idempotente)

```bash
# En el contenedor (via SSH o render shell)
python -m scripts.ingest_if_needed
# O con rutas expl\u00edcitas:
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
[ingest_if_needed] Corpus sincronizado \u2192 saltando ingesta (idempotente)
```

### 12.6 Verificar que el corpus qued\u00f3 cargado

```bash
curl -s https://contafis-assistant-prod.onrender.com/readyz
# \u2192 {"status":"ready","corpus_version":1,"chroma_count":314,"corpus_status":"ok"}
```

### 12.7 Rotar modelo de embeddings (reingesta obligatoria)

Si cambias `EMBEDDING_MODEL` o `EMBEDDING_DIMENSIONS`:

1. Actualizar variables de entorno en Render
2. Actualizar `dimensions` en `/var/data/manifest.json`
3. Borrar colecci\u00f3n Chroma vieja: `rm -rf /var/data/chroma`
4. Ejecutar ingesta: `python -m scripts.ingest_if_needed --force`
5. Copiar el nuevo `sha256` del BM25 al `manifest.json`
6. Reiniciar servicio (auto-deploy o manual)

---

## 13. Checklist de Deploy a Producci\u00f3n (Paid)

- [ ] Plan Render **Starter o superior** (requerido para disco persistente)
- [ ] Disco persistente creado y montado en `/var/data` (configurado en `render.yaml` \u2192 `disk`)
- [ ] `CHROMA_PATH=/var/data/chroma` y `BM25_INDEX_PATH=/var/data/bm25_index.json` en variables de entorno
- [ ] `preDeployCommand: "python -m scripts.ingest_if_needed"` habilitado en `render.yaml`
- [ ] PDFs subidos a `/var/data/corpus/`
- [ ] `/var/data/manifest.json` creado con hashes y p\u00e1ginas reales
- [ ] Primer deploy ejecuta ingesta autom\u00e1ticamente v\u00eda `preDeployCommand`
- [ ] `/readyz` devuelve `corpus_status: ok` y `chroma_count > 0`
- [ ] Smoke test chat funcional