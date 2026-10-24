import os
import sys
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)

from pydantic import ValidationError

from contafis_assistant.config import Settings
from contafis_assistant.main import build_runtime

MESSAGE = "Hola, ¿qué vencimientos tengo esta semana?"

_PLACEHOLDERS = {
    "",
    "changeme",
    "change_me",
    "placeholder",
    "your_api_key",
    "your-api-key",
    "xxx",
    "todo",
    "none",
    "null",
}


def _usable_secret(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return bool(normalized) and normalized not in _PLACEHOLDERS


def _configure_langsmith(settings: Settings) -> tuple[str, bool]:
    tracing = "off"
    has_key = False
    if settings.langsmith_tracing:
        tracing = "on"
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        secret = settings.langsmith_api_key
        if secret is not None and _usable_secret(secret.get_secret_value()):
            os.environ["LANGSMITH_API_KEY"] = secret.get_secret_value()
            has_key = True
    return tracing, has_key


def _print_headers(settings: Settings, tracing: str) -> None:
    print("=== ContaFis agent smoke run ===")
    print(f"app_env: {settings.app_env}")
    print(f"tracing: {tracing}")
    print(f"langsmith_project: {settings.langsmith_project}")
    print(f"llm_model: {settings.llm_model}")
    print(f"embedding_model: {settings.embedding_model}")
    print(f"query: {MESSAGE}")
    print()


def _print_response(response) -> None:
    print("--- respuesta ---")
    print(f"status: {response.status}")
    print(f"answer: {response.answer}")
    print(f"warnings: {[w.code for w in response.warnings]}")
    print(f"tool_call_count: {response.tool_call_count}")
    print(f"tools_used: {response.tools_used}")
    print(f"truncated: {response.truncated}")
    print(f"request_id: {response.request_id}")
    print(f"thread_id: {response.thread_id}")
    print()


def _report_validation_error(exc: ValidationError) -> int:
    print("ERROR: Settings() falló: falta o es inválida configuración requerida")
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ())) or "(raíz)"
        print(f"  {location}: {error.get('msg')}")
    return 1


def _fail(label: str, exc: BaseException) -> int:
    print(f"ERROR: {label} falló")
    traceback.print_exc()
    print(f"repr: {exc!r}")
    return 1


def _report_trace(
    project: str,
    request_id: str,
    tracing: str,
    has_key: bool,
) -> None:
    print("=== LangSmith trace ===")
    if tracing != "on":
        print("tracing deshabilitado: no se generó trace")
        return
    if not has_key:
        print("no se generó trace porque falta LANGSMITH_API_KEY")
        return
    print(f"proyecto: {project}")
    print(f"request_id: {request_id}")
    print("Abrí la UI de LangSmith para ver la traza:")
    print("  https://smith.langchain.com")
    print(
        f"  Proyecto -> runs y buscá por request_id: {request_id} "
        "(la key de tracing no permite leer la API; la traza se ve en la UI)"
    )


def _flush_langsmith() -> None:
    try:
        from langsmith import Client

        print(f"Enviando traza al proyecto: {os.environ.get('LANGSMITH_PROJECT')}")
        Client().flush()
        print("Flush de LangSmith enviado.")
    except Exception as exc:
        print(f"No se pudo enviar la traza a LangSmith: {exc!r}")


def _run() -> int:
    try:
        settings = Settings()
    except ValidationError as exc:
        return _report_validation_error(exc)
    except Exception as exc:
        return _fail("Settings()", exc)

    tracing, has_key = _configure_langsmith(settings)
    _print_headers(settings, tracing)

    try:
        runtime = build_runtime(settings)
    except Exception as exc:
        return _fail("build_runtime()", exc)

    try:
        try:
            runtime.startup()
        except Exception as exc:
            return _fail("runtime.startup()", exc)
        response = runtime.chat(
            MESSAGE,
            thread_id=None,
            request_id=str(uuid.uuid4()),
        )
    except Exception as exc:
        return _fail("runtime.chat()", exc)
    finally:
        runtime.close()

    _print_response(response)
    _report_trace(
        settings.langsmith_project, response.request_id, tracing, has_key
    )
    return 0


def main() -> int:
    try:
        return _run()
    finally:
        _flush_langsmith()


if __name__ == "__main__":
    raise SystemExit(main())
