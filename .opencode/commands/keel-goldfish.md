---
description: Keel Nav Fase 1 — auditoría EGM/Goldfish a ciegas de la spec activa, delegada al @reviewer
agent: build
---
Auditoría Goldfish a ciegas (skill `.meta-skills/02-egm-goldfish-audit.md`). Delegá al subagente `reviewer`; este comando es de solo lectura y NUNCA modifica código.

Prompt para el `reviewer`:
- Leé `specs/<activa>/spec.md` y el código + tests que la implementan.
- Auditá a ciegas: sin acceder a la intención original del autor, solo a la spec y al código.
- Verificá trazabilidad RF por RF (spec → test → implementación) y emití veredicto por RF (PASS/FAIL) con evidencia.
- Guardá el reporte en `specs/<activa>/<slug>.audit.<n>.md` (n = siguiente número disponible).

Presupuesto: ~1000-2000 tokens. Si la spec activa no es obvia, usá `/keel-status` para identificarla.
