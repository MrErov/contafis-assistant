---
drift: control
version: 0.1.0
spec_id: US-001-api-fail-closed
spec_version_audited: 0.1.1
verdict: no-drift
findings_blocking: 0
findings_warning: 0
findings_info: 0
drift_checked_at: 2026-11-09
drift_checked_by: 05-drift-control
drift_round: 1
---

# DRIFT CONTROL: US-001-api-fail-closed — Arranque fail-closed sin `API_SHARED_SECRET` en producción

## Verdict

**no-drift** — 0 blocking, 0 warning, 0 info.

## 1. Code Scope (Navigator-approved)

Files audited:
- `src/contafis_assistant/config.py`
- `tests/unit/test_config.py`
- `tests/api/test_startup.py`
- `tests/api/conftest.py`
- `tests/api/test_health.py`

## 2. Forward Traceability (§2 ACs & §3 Contracts → Code & Tests)

| Spec Item | Code Location | Test Location | Verdict |
|-----------|---------------|---------------|---------|
| AC-1: Prod w/o secret → ValidationError | config.py:127-130 | test_config.py:136-146 (T-1) | match |
| AC-1: Prod w/ empty/whitespace → ValidationError | config.py:93-100 + 127-130 | test_config.py:149-163 (T-2) | match |
| AC-1: create_app() prod w/o secret fails early | config.py:127-130 via Settings() | test_startup.py:113-136 (T-3) | match |
| AC-2: Prod with secret → builds, no warning | config.py:127-130 passes; 132-138 only warns non-prod | test_config.py:11-46 (T-4 reg) | match |
| AC-2: Integration: app created, secret in ApiKeyGuard | main.py:996 | test_startup.py:25-43 (T-5 reg) | match |
| AC-3: Non-prod w/o secret → warning + continues | config.py:132-138 | test_config.py:166-183 (T-6) | match |
| AC-3: Non-prod w/ empty/whitespace → warning | config.py:93-100 + 132-138 | test_config.py:166-183 (T-6 param) | match |
| AC-3: APP_ENV absent → defaults dev + warns | config.py:40 default; 132-138 warns | test_config.py:186-197 (T-6b) | match |
| AC-3: Dev w/o secret → create_app() + /livez works | config.py:132-138; main.py:1002 | test_startup.py:139-170 (T-7) | match |
| AC-4: Dev w/ non-loopback host → ValidationError | config.py:114-117 | test_config.py:85-96 (T-8 reg) | match |
| §3.1 Input Contract | Settings class fields | N/A | match |
| §3.2 Output Contract | ValidationError messages; stdout warning | Tests assert messages | match |

## 3. Backward Traceability (Production Code → Spec Anchor)

| Production File & Lines | Spec Anchor | Verdict |
|------------------------|-------------|---------|
| config.py:127-130 (prod secret validation) | AC-1, §3.1, §3.2 | justified |
| config.py:132-138 (non-prod stdout warning) | AC-3, §3.2 | justified |
| config.py:114-117 (non-loopback host rule) | AC-4, §3.1 | justified (pre-existing) |
| config.py:93-100 (empty secret normalizer) | AC-1, AC-3, §3.1 | justified (pre-existing) |
| main.py:996 (ApiKeyGuard wiring) | AC-2, §3.2 | justified (pre-existing) |
| main.py:1002 (/livez exemption) | AC-3, §3.2 out-of-scope | justified (pre-existing) |

## 4. Test Traceability (Test → AC)

| Test | AC Mapping | Verdict |
|------|------------|---------|
| test_production_without_shared_secret_fails | AC-1 (T-1) | match |
| test_production_with_empty_or_whitespace_secret_fails (×3) | AC-1 (T-2) | match |
| test_non_production_without_secret_warns_and_continues (×6) | AC-3 (T-6) | match |
| test_non_production_without_app_env_defaults_to_development_warns | AC-3 (T-6b) | match |
| test_production_create_app_without_secret_fails | AC-1 (T-3) | match |
| test_development_without_secret_starts_warns_and_livez_works | AC-3 (T-7) | match |
| test_remote_bind_without_secret_does_not_start | AC-4 (T-8) | match (regression) |
| test_loopback_host_does_not_require_secret | AC-4 (T-8) | match (regression) |
| test_remote_host_requires_shared_secret | AC-4 (T-8) | match (regression) |
| test_production_refresh_unsafe_returns_503... | — | test infrastructure |

## 5. Observation Traceability

Not applicable: `spec.status = implemented` (Phase 3 skipped — horizontal layer, no Section 6).

## 6. Navigator Overrides

None.

## 7. Blocking Findings

None.

## 8. Auditor's Note

All 10 ACs (4 primary + 6 regression guards) trace to implementation and tests. No orphan tests, no unjustified production code, no contract mismatches. Phase 3 correctly skipped per protocol (no Section 6). Code is certified no-drift.