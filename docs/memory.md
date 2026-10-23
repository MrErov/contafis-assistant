# Memoria del Proyecto (LangChain RAG)

## Estado Actual
- Repo recién inicializado.
- Fase 0: Configuración de entorno y estructura SDD.
- Núcleo de 8 skills LangChain instalado en `.agents/skills/` (lockfile: `skills-lock.json`): `ecosystem-primer`, `langchain-dependencies`, `langchain-fundamentals`, `langchain-rag`, `langgraph-fundamentals`, `langgraph-persistence` (oficiales) + `langchain-architecture`, `rag-implementation` (`wshobson/agents`). Listo para la fase de implementación.

## Próximos Pasos
1. Ejecutar `/sdd-spec 001-chat-fiscal` para definir el alcance del MVP.
2. Configurar entorno virtual de Python e instalar dependencias (LangChain, LangGraph).

## Decisiones y Notas
- La conexión con Odoo se hará mediante API XML-RPC (o MCP si se desarrolla).
