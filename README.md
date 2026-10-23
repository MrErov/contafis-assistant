# ContaFis AI Assistant 🧠📊

Asistente fiscal inteligente basado en RAG (Retrieval-Augmented Generation) para el ecosistema ContaFis (Odoo 19).

Permite a los contadores consultar su estado de cumplimiento fiscal, vencimientos y normativas usando lenguaje natural.

*(Documentación detallada próximamente)*

## API

El servicio se ejecuta en un único proceso. No uses más de un worker: la memoria de conversaciones, el caché de alcance y el corpus viven en el proceso.

```powershell
uvicorn contafis_assistant.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Para operación local o detrás de un reverse proxy, mantené `API_HOST=127.0.0.1`. Si configurás un bind remoto sin `API_SHARED_SECRET`, la aplicación no arranca.

- `POST /chat` recibe `{"message": "...", "thread_id": "..."}`. Los pedidos inválidos responden HTTP 200 con estado `invalid_request`.
- `GET /health` devuelve el estado agregado de alcance, corpus, Chroma/BM25 y Odoo. Responde HTTP 503 solo cuando el runtime es inseguro en producción.
- Si `API_SHARED_SECRET` está definido, todas las rutas exigen el header `X-API-Key`.
