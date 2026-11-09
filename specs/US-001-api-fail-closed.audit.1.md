---
audit: goldfish
version: 0.1.0
spec_id: US-001-api-fail-closed
spec_version_audited: 0.1.0
verdict: rejected
effective_verdict: rejected
findings_blocking: 1
findings_warning: 3
findings_info: 2
audited_at: 2026-11-09
audited_by: 02-egm-goldfish-audit
audit_round: 1
---

# GOLDFISH AUDIT: US-001-api-fail-closed — Arranque fail-closed sin API_SHARED_SECRET en producción

## Verdict

**rejected** — 1 blocking, 3 warning, 2 info.

## Blocking findings (1)

| # | Category | Location | Finding |
|---|----------|----------|---------|
| B1 | CONTRADICTION | §1 (I-want) / AC-4 | La cláusula I-want de §1 promete que en todos los entornos no-producción el servicio "emita un warning pero continúe", mientras AC-4 exige que `development` con `API_HOST=0.0.0.0` y sin secreto siga lanzando `ValidationError`. |

## Warnings (3)

| # | Category | Location | Finding |
|---|----------|----------|---------|
| W1 | GAP | §3.1 / AC-1, AC-2, T-1–T-4 | AC-1, AC-2 y T-1 a T-4 fijan explícitamente `ODOO_READONLY_ATTESTED=true` (T-5 lo deja implícito en `Settings(...)`), pero §3.1 no declara esa variable ni su tipo, default o condición de obligatoriedad. |
| W2 | HIDDEN_ASSUMPTION | AC-3 / §3.1 | AC-3 y §3.1 tratan `APP_ENV` no definido como `development` y continúan sin secreto, de modo que el fail-closed depende de que producción declare explícitamente `APP_ENV=production`, supuesto que §1 no enuncia. |
| W3 | GAP | AC-3 / T-6 | AC-1 y §3.1 equiparan secreto ausente, vacío y solo espacios, pero AC-3 solo menciona "ausente" y T-6 no cubre `""` ni `"   "` en entornos no-producción. |

## Info (2)

| # | Category | Location | Finding |
|---|----------|----------|---------|
| I1 | CONTRADICTION | §1 / docs/context/api.md (Public interface) | §1 ubica la validación en `config.py` y lo llama "composition root", mientras el contexto asigna el composition root a `main.py`. |
| I2 | AMBIGUITY | AC-3 / T-6 | T-6 no precisa si verifica el valor efectivo de `app_env` (por ejemplo `development` cuando no está definido) o solo la presencia del nombre en el warning. |

## 8. Navigator overrides

None.
