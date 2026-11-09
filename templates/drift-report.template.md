---
template: drift-report
version: 0.1.0
phase: 4
consumed_by: [05-drift-control]
source_of_truth: "specs/<US-ID>.spec.md (this report is an audit of it)"
estimated_tokens_to_fill: 1500-3000
max_lines: 250
---

# DRIFT REPORT: <US-ID> — <spec title>

> **How to use this template:** produced by `05-drift-control`. Fill every
> `[M]` section. Do not add sections. Do not exceed the per-section line
> limits. A drift report that needs more than 250 lines is a report on a
> spec that was too large — escalate instead of expanding.

---

## 1. [M] Verdict

**Verdict:** `no-drift | drifted | drifted-accepted | observation-blocked`

| Counter | Value |
|---------|-------|
| Blocking drift findings | `<N>` |
| Warning drift findings | `<M>` |
| Info drift findings | `<K>` |
| Navigator overrides | `<O>` |
| Spec version audited | `<0.1.0>` |
| Code scope audited | `<git-range \| files>` |

> `no-drift`: zero blocking findings.
> `drifted`: ≥1 blocking finding without override.
> `drifted-accepted`: ≥1 blocking finding, all overridden (§8).
> `observation-blocked`: Phase 3 was observation-blocked and not overridden.

---

## 2. [M] Code scope

**Method:** `git-diff | explicit-list`

If `git-diff`:

```
Base: <commit sha>
Head: <commit sha>
Files changed: <N>
```

If `explicit-list`:

```
Files: <N>

<path>
<path>
```

> This declaration is the audit's boundary. Code outside this scope was
> not checked. Any finding outside scope is not the report's concern.

---

## 3. [M] Spec → Code traceability

### 3.1 Acceptance criteria

| AC | Implemented in | Covered by test | Status |
|----|----------------|-----------------|--------|
| AC-1 | `<file:function/endpoint>` | `T-1` | `match \| drift \| gap` |
| AC-2 | `<...>` | `T-2, T-3` | `<status>` |
| AC-3 | `<...>` | `T-4` | `<status>` |

> `match`: implementation exists, test asserts the AC's observable result.
> `drift`: implementation exists but does not match the AC.
> `gap`: implementation missing or test missing.

### 3.2 Interface contracts

| Contract (Section 3) | Implemented in | Matches? |
|-----------------------|-----------------|----------|
| `POST /invoices/{id}/retry` | `billing/routes.py:42` | `yes \| no \| partial` |
| `invoice.charge_failed` event | `billing/events.py:88` | `<verdict>` |

---

## 4. [M] Code → Spec traceability

> Every production file in scope, mapped to the spec section that justifies it.

| File | Justification | Verdict |
|------|---------------|---------|
| `billing/retry.py` | Section 3.1 `retry_charge` | `justified` |
| `billing/events.py` | Section 3.2 event | `justified` |
| `billing/utils.py` | — | `UNJUSTIFIED (scope creep)` |

> `UNJUSTIFIED` files are drift by definition. They exist without a spec
> section demanding them.

---

## 5. [M] Test → AC traceability

| Test ID | File | Maps to AC | Verdict |
|---------|------|------------|---------|
| T-1 | `tests/test_retry.py::test_first_retry` | AC-1 | `match` |
| T-2 | `tests/test_retry.py::test_second_retry` | AC-2 | `match` |
| T-4 | `tests/test_retry.py::test_idempotency` | — | `ORPHAN` |

> `ORPHAN` tests have no AC. Either the spec is missing an AC or the test
> is scope creep. Both are drift findings.

---

## 6. [O] Observation traceability

> Only if Phase 3 ran with `status: observed`. Otherwise delete this section.

| Section 6 observation | Covered by e2e test | Navigator observed | Current code still matches? |
|------------------------|----------------------|---------------------|------------------------------|
| <behavior> | `T-e2e-1` | `yes \| no` | `yes \| no` |

**Observation gate outcome:** `<approved | deferred | blocked>`

> If `deferred` or `blocked`: record as drift finding unless the Navigator
> explicitly overrides in §8.

---

## 7. [M] Drift findings

### 7.1 Blocking

| # | Category | Location | Finding |
|---|----------|----------|---------|
| B1 | `<SPEC_DRIFT \| CODE_DRIFT \| TEST_DRIFT \| CONTRACT_DRIFT \| OBSERVATION_DRIFT>` | `<§2 AC-1 \| file:line>` | <one sentence> |

> If zero: write "None." Do not delete the section.

### 7.2 Warning

| # | Category | Location | Finding |
|---|----------|----------|---------|
| W1 | `<CATEGORY>` | `<location>` | <one sentence> |

> If zero: write "None."

### 7.3 Info

| # | Category | Location | Finding |
|---|----------|----------|---------|
| I1 | `<CATEGORY>` | `<location>` | <one sentence> |

> If zero: write "None."

---

## 8. [M] Navigator overrides

> A Navigator override records that the Navigator has **accepted a specific
> blocking finding as known, acceptable drift**. An override does **not**
> delete the finding — the finding stays in §7.1 for the historical record.
> The override changes the verdict, not the evidence.

| # | Overrides finding | Navigator | Date | Justification | Scope |
|---|-------------------|-----------|------|---------------|-------|
| O1 | `B1` | `<name>` | `<YYYY-MM-DD>` | <one sentence> | `<what's accepted: this file, this AC, permanently, until X>` |

> If zero: write "None." Do not delete the section — its emptiness is
> meaningful (it certifies that no override was needed).

---

## 9. [M] Certification

**Spec status after this report:** `certified | implemented | observed`

**Statement:**

> The audit compared the spec at version `<0.1.0>` against the code in
> scope `<git-range | files>` on `<YYYY-MM-DD>`. Of `<N>` blocking
> findings, `<O>` were overridden by the Navigator. The remaining
> `<N-O>` blocking findings determine the verdict: `<verdict>`.

**Signature:**

```yaml
# YAML guide: values containing ': ' must be quoted.
us_id: <US-ID>
drift_report: 'specs/<US-ID>.drift.<N>.md'
verdict: <no-drift | drifted | drifted-accepted | observation-blocked>
certified_by: 05-drift-control
certified_at: <YYYY-MM-DD>
spec_version: 0.1.0
```
