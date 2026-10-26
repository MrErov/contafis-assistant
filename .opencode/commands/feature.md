---
description: Planifica una nueva funcionalidad antes de tocar código
agent: plan
---
Respeta los 6 principios de `@docs/constitution.md`.

Quiero añadir esta funcionalidad: $ARGUMENTS

Antes de escribir código, prepárame un plan con:

1. **Cómo la vas a implementar**, respetando las reglas de `@AGENTS.md`.
2. **Qué archivos vas a modificar** y qué cambia en cada uno.
3. **Los casos límite** y las dudas que debo decidir yo antes de empezar.
4. **Qué actualizarías en `docs/capacidades/` y `docs/memory.md`**.

## Contexto obligatorio a leer

- Reglas del proyecto: `@AGENTS.md`
- Estado actual: `@docs/memory.md`
- Índice de specs: `@SPEC.md`

## Reglas estrictas

- ❌ NO modifiques ningún archivo hasta que apruebe el plan.
- ❌ NO crear archivos temporales (`check_*.py`, `diag_*.py`, etc.).
- ❌ NO entrar en loops de "arreglar → fallar → arreglar".
- ✅ Espera mi confirmación explícita antes de codear.

Empieza por leer los archivos de contexto y dame el plan.
