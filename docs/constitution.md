# Constitución del Proyecto ContaFis AI

1. **El ERP manda:** El asistente NO inventa datos financieros. Siempre debe consultar la base de datos de Odoo o los documentos vectorizados antes de responder.
2. **Privacidad del Contador:** La base de datos vectorial es privada. El LLM no debe entrenarse con los datos de los clientes.
3. **LangGraph como Orquestador:** Las lógicas de decisión complejas (ej. "si pregunta por IVA, busca en la tabla X; si pregunta por ISLR, busca en Y") se manejan con grafos de estado, no con prompts gigantes.
4. **Tests sin gastar tokens:** Los tests de Python deben usar `Mock` o `FakeListChatModel` para no consumir la API real de OpenAI/AWS en cada `pytest`.
5. **Retrieval Híbrido:** El RAG debe combinar búsqueda semántica (embeddings) con búsqueda por palabras clave (BM25) para mayor precisión en términos fiscales.
6. **Cero alucinaciones:** Si el contexto no contiene la respuesta, el agente debe decir explícitamente "No tengo información sobre eso" en lugar de inventar una ley.
