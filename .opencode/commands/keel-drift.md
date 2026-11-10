---
description: Keel Nav Fase 4 — control de deriva spec↔código, genera el drift report delegando al @reviewer
agent: build
---
Control de deriva (skill `.meta-skills/05-drift-control.md`, template `templates/drift-report.template.md`). Delegá al subagente `reviewer`; este comando es de solo lectura y NUNCA modifica código ni commitea.

Prompt para el `reviewer`:
- Sobre la spec activa `specs/<activa>/spec.md`: hacé el forward check (spec → código) y el backward check (código → spec).
- Clasificá la deriva en las 5 categorías documentadas y verificá la trazabilidad de tests.
- Aplicá el mecanismo de "overrides del Navigator" cuando corresponda.
- Generá el reporte con `templates/drift-report.template.md` en `specs/<activa>/<slug>.drift.<n>.md`.

Presupuesto: ~2000-3000 tokens. El veredicto de deriva es jurisdicción exclusiva del `reviewer`.
