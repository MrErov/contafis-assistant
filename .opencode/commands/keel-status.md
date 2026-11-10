---
description: Keel Nav — muestra la fase activa de la spec en curso y qué artefacto falta
agent: build
---
Reporte de estado Keel Nav (solo lectura). NO delegues ni modifiques nada: hacé el relevamiento vos mismo y recomendá el siguiente paso.

1. Spec activa: la de mayor número en `specs/NNN-*/` que aún no esté cerrada (o la que indique el usuario). Considerá también `specs/US-*.spec.md`.

2. Mapeá los artefactos a las 5 fases y marcá presente/faltante:
   - Fase 0 Contexto: `docs/context/` (`00-overview.md`, `01-dependency-map.md`, `02-risk-register.md`).
   - Fase 1 Spec + Auditoría: `specs/<activa>/spec.md`, `specs/<activa>/plan.md`, y `specs/<activa>/*.audit.*.md`.
   - Fase 2 TDD: tests en `tests/**` que cubren los RF de la spec (y el plan de tests, §6).
   - Fase 3 Slice vertical: implementación E2E + observación humana registrada.
   - Fase 4 Deriva: `specs/<activa>/*.drift.*.md`.

3. Imprimí una tabla `fase | artefacto | estado (✅/❌) | próximo paso`.

4. Recomendá el próximo comando: `/keel-goldfish` si falta auditoría, `/tdd-check` antes de commitear, `/keel-drift` tras implementar.
