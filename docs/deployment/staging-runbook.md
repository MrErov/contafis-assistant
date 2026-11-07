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