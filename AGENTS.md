# AGENTS.md - Reglas del Proyecto (LangChain / RAG)

## Contexto
Este repositorio contiene el Asistente Fiscal basado en IA (RAG) para el proyecto ContaFis. Se conecta a Odoo 19 (repo separado) mediante APIs y usa LangChain, LangGraph y LangSmith.

## Stack
- Python 3.10+
- LangChain, LangGraph, LangSmith
- FastAPI (para exponer el servicio)
- Pytest (testing)
- AWS (Bedrock/OpenSearch - a definir en fase de plan)

## Reglas Estrictas para el Agente (IA)
1. **NO inventar código de LangChain:** Si no conocés la API exacta de la versión actual, buscá en la documentación oficial o usá `langchain-cli`. No uses sintaxis de versiones viejas.
2. **TDD es obligatorio:** Antes de escribir una cadena o un agente, escribe el test (usando Pytest y mocks de LLM).
3. **Tipado estricto:** Usá Pydantic para todas las entradas y salidas del agente (Structured Outputs).
4. **NO crear archivos temporales** (como `test_agent.py`, `check_rag.py`).
5. **Edición quirúrgica:** Editá usando herramientas que reemplacen texto exacto, no reescribas el archivo entero si solo cambias una línea.
6. **Observabilidad:** Todo agente debe tener `LangSmith` habilitado por defecto en desarrollo.

## Entorno y Secretos
- NUNCA commitees el archivo `.env`.
- Las API Keys (OpenAI/AWS) deben leerse con `os.getenv()`.
