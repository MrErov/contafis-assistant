# CONTEXT: api

## Purpose
Superficie HTTP del asistente (FastAPI): chat, alcance, salud, liveness y digitalización de facturas, con autenticación por sesión de Odoo o token legacy.

## Responsibilities
- Exponer `/chat`, `/scope`, `/health`, `/livez` y `/digitize`.
- Autenticar por `X-Odoo-Session` o token legacy y construir el contexto de usuario.
- Validar la forma y los límites del request antes de ejecutar el grafo.
- Traducir fallos a respuestas HTTP seguras (400/401/503) y contratos `ChatResponse`.
- Ejecutar trabajo síncrono (grafo, digitize) en threadpool para no bloquear el event loop.

## Public interface
- `router` — `APIRouter` con los cinco endpoints.
- `UserContext`, `get_user_context_from_session` — autenticación y alcance por request.
- `ApiKeyGuard`, `verify_api_key`, `API_KEY_HEADER` — autenticación de servicio.
- `DigitizeService`, `get_digitize_service`, `NIMError`, `NIMTimeoutError`, `NIMParsingError`.
- `ServiceUnavailable`, `invalid_request_response`, `unavailable_payload`.
- `create_app`, `build_runtime`, `ChatRuntime` — composition root en `main.py`.

## Dependencies
- Internal: `domain` (contratos, digitize, request, enums, answer), `odoo` (client, scope, allowlist, errors), `graph` (vía `runtime.chat_with_user`), `config`.
- External: `fastapi`, `starlette`, `uvicorn`, `httpx`, `pydantic`, `langchain-openai` (digitize con visión).

## Persistence / State
- Sin base de datos propia.
- `ChatRuntime` y `Settings` viven en `app.state`.
- `UserContext` es por request y contiene `OdooClient`, `ScopeSnapshot` y `ScopeService`.
- `ThreadStore` (memoria) se comparte a través del runtime.

## Commands
- Build: `pip install -r requirements.txt`
- Test: `pytest tests/api tests/unit/test_session_auth.py tests/unit/test_request_contract.py tests/unit/test_config.py tests/integration/test_prompt_injection.py tests/acceptance/test_security_audit.py -q`
- Run: `uvicorn contafis_assistant.main:app --app-dir src --host 0.0.0.0 --port 8000`

## Gotchas / Observed tech debt
- `main.py` (1068 líneas) mezcla app factory, lifespan, `ChatRuntime`, clasificador LLM y dos rutas de ensamblado de tools (`_build_tools` y `_build_user_deps`).
- `routes.py` imprime el traceback completo a stdout ante errores de `/chat` (`print` + `traceback.print_exc`).
- `/livez` está exento del middleware de API key; `/health` sí requiere autenticación.
- `ApiKeyGuard` permite todas las requests si `API_SHARED_SECRET` no está configurado (`verify_api_key` devuelve `True`).
- `user_auth.py` (>400 líneas) soporta dos modos (`session` y `token`) y deriva `user_key` con SHA-256 del token.
- `/digitize` depende de un LLM Vision externo (NIM) y mapea timeouts/parseos a 503.

## Last updated
2026-11-09 (skill 0.1.0)
