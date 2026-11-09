---
template: test-plan
version: 0.1.0
phase: 2
consumed_by: [03-tdd-cycle, 04-odd-vertical-slice, 05-drift-control]
source_of_truth: "specs/<US-ID>.spec.md Section 4 (this file is a view, not the source)"
estimated_tokens_to_fill: 500-1000
max_lines: 100
---

# TEST PLAN: <US-ID> — <spec title>

> **How to use this template:** this file is a **projection** of Section 4
> of `specs/<US-ID>.spec.md`. It is for humans (reviewers, Phase 4 auditors,
> future maintainers) who want the test plan without opening the whole spec.
>
> **Do not edit the source of truth here.** If a test needs to change, the
> spec changes. This file is regenerated from the spec. Any edit here that
> does not also change the spec is a drift signal — Phase 4 will flag it.

---

## 1. Source

- **Spec:** `specs/<US-ID>.spec.md`
- **Spec version:** `0.1.0`
- **Audit:** `specs/<US-ID>.audit.<N>.md` (`verdict: audited` on `<YYYY-MM-DD>`)
- **Phase 2 status:** `<not started | RED | partial GREEN | implemented>`
- **Phase 3 status (if applicable):** `<not started | e2e RED | e2e GREEN | observed>`

---

## 2. Test inventory

| Test ID | Covered AC | Type | Description | File (Phase 2) | Status |
|---------|------------|------|-------------|----------------|--------|
| T-1 | AC-1 | unit | <one line, from spec Section 4> | `<path/test_file>` | `<RED \| GREEN>` |
| T-2 | AC-2 | integration | <one line> | `<path>` | `<status>` |
| T-3 | AC-3 | e2e | <one line> | `<path>` | `<status>` |
| ... | ... | ... | ... | ... | ... |

> Every row here must have a matching row in spec Section 4. Every row in
> spec Section 4 must appear here. Any delta is drift.

---

## 3. Coverage matrix (AC → Test)

| Acceptance Criterion | Covered by | Independent verification |
|----------------------|------------|--------------------------|
| AC-1 | T-1 | `<one sentence: what T-1 asserts that proves AC-1>` |
| AC-2 | T-2, T-3 | `<one sentence>` |
| AC-3 | T-4 | `<one sentence>` |

> Each AC must be covered by ≥1 test. Each test must cover ≥1 AC.
> "Independent verification" is the one-liner a reviewer reads to decide
> whether the test actually exercises the criterion — not just names it.

---

## 4. E2E observations (Phase 3, if applicable)

> Only if the spec has a non-empty Section 6. Otherwise, delete this section.

| Observation | Covered by E2E test | Navigator observed? | Date |
|-------------|---------------------|---------------------|------|
| <behavior from spec Section 6> | `T-e2e-<i>` | `<yes \| no \| deferred>` | `<YYYY-MM-DD or —>` |

**Observation gate result:** `<approved | deferred | blocked | N/A>`

> If any row is `deferred` or `blocked`, Phase 4 will flag this plan. That is
> intentional. ODD without observation is not ODD.

---

## 5. Test infrastructure

- **Unit framework:** `<pytest / jest / go test / etc.>`
- **Integration framework:** `<same as unit | separate>`
- **E2E framework (if Phase 3):** `<Playwright / SuperTest / RTL / curl-based / etc.>`
- **Mock library:** `<unittest.mock / jest.mock / sinon / etc.>`
- **Real infrastructure used in Phase 3:** `<SQLite / Postgres / real HTTP / real browser>`

> Phase 2 tests must not depend on real infrastructure. Phase 3 e2e tests
> must. Any deviation is a Phase 2/Phase 3 boundary violation.

---

## 6. Run commands

```bash
# Phase 2 (unit + integration)
<command, e.g. `pytest tests/billing/ -v`>

# Phase 3 (e2e, requires running system)
<command, e.g. `pytest tests/e2e/ -v` or `npx playwright test`>

# Single test
<command, e.g. `pytest tests/billing/test_retry.py::test_first_retry -v`>
```

---

## 7. Known gaps (carried from the spec, not invented here)

- <gap 1 — from spec's audit report, or the spec's Section 5>
- <gap 2 — e.g., "idempotency under concurrent retries not covered by any AC">

> Do not add gaps that the spec does not acknowledge. This section records
> what the audit found and the spec accepted as known scope. New gaps found
> during Phase 2/3 belong in a new audit round, not here.

---

## 8. Traceability metadata

```yaml
us_id: <US-ID>
spec_version: 0.1.0
generated_by: 03-tdd-cycle
generated_at: <YYYY-MM-DD>
last_reviewed_by: <Navigator name or "pending">
```

> **YAML guide:** any string value containing `: ` (colon + space) must be
> quoted. Example: `note: 'verdict: audited'`. Unquoted `verdict: audited`
> parses as a nested key and breaks the frontmatter.
