---
template: spec
version: 0.1.0
phase: 1
consumed_by: [01-sdd-spec-writer, 02-egm-goldfish-audit, 03-tdd-cycle, 05-drift-control]
estimated_tokens_to_fill: 2000-4000
max_lines: 120
---

# SPEC: `<US-ID>` — <short User Story title>

> **How to use this template:** fill in only the sections marked as mandatory
> (`[M]`). Sections marked as optional (`[O]`) are deleted if they do not
> apply. Do not add new sections. Do not exceed the per-section line limits.
> If a mandatory section cannot be filled, it is a signal that the User Story
> is not ready: stop and return it to the Navigator.

---

## 1. [M] User Story

**As a** `<role>`,
**I want** `<capability>`,
**so that** <business benefit>.

**Bounded context:** `<context-name>` (reference: `docs/context/<name>.md`)

**Explicit out of scope:**
- <what does NOT belong to this story, to prevent scope creep>

> Limit: 10 lines. If you need more, the story is too big: split it.

---

## 2. [M] Acceptance Criteria

| ID | Given (context) | When (action) | Then (observable result) |
|----|-----------------|---------------|--------------------------|
| AC-1 | ... | ... | ... |
| AC-2 | ... | ... | ... |
| AC-3 | ... | ... | ... |

> Limit: 3–7 criteria. Every criterion must be **verifiable by a test**.
> If a criterion cannot be translated into a test, it is not a criterion:
> it is an aspiration. Rewrite it or delete it.

---

## 3. [M] Interface Contracts

> Only if the story exposes or consumes an interface (API, public function,
> event, UI component). If not applicable, write "N/A — internal story" and
> move to Section 4.

### 3.1 Input contract

```
<VERB> <path/signature>
```

Payload / parameters:

```
<field>: <type> — <validation>
```

Responses:

```
2xx: <shape>
4xx: <shape>
```

### 3.2 Output contract / side effects

- <what is persisted, what is emitted, what is notified>

> Limit: 15 lines. If the contract needs more, the story is mixing too many
> responsibilities. Split it.

---

## 4. [M] Test Plan (TDD seed)

> This section is the **scope contract** for Phase 2. The AI will not write
> code that is not covered here.

| Test ID | Covered AC | Type | Short description |
|---------|------------|------|-------------------|
| T-1 | AC-1 | unit | ... |
| T-2 | AC-2 | integration | ... |
| T-3 | AC-3 | e2e | ... |

**Expected initial state:** all **RED** (`FAIL`).

> Limit: 5–10 tests. If you need more, the story is too big.

---

## 5. [O] Dependencies and Risks

- **Depends on:** <other stories, contexts, external services>
- **Main risk:** <what could make this story fail>
- **Mitigation:** <how that risk is reduced>

> If not applicable, delete this entire section.

---

## 6. [O] Observation Notes (ODD)

> Only for stories with a visual component or runtime-observable behavior.
> Filled in Phase 3, not Phase 1.

- <what the Navigator must observe to validate the vertical slice>

---

## 7. [M] Exit Gate (self-check before Goldfish Audit)

| Gate | Criterion | Status |
|------|-----------|--------|
| G1 | User Story in As-a/I-want/So-that format | [ ] |
| G2 | Explicit out-of-scope list declared | [ ] |
| G3 | 3–7 verifiable acceptance criteria | [ ] |
| G4 | Every AC has at least one test in the plan | [ ] |
| G5 | Interface contracts complete or "N/A" justified | [ ] |
| G6 | Total length ≤ 120 lines | [ ] |
| G7 | No sections added outside this template | [ ] |

**If any gate fails, the spec is NOT sent to Goldfish Audit.**
Return to the Navigator with the failing gate and the reason.

---

## 8. [M] Traceability Metadata

```yaml
# YAML guide: values containing ': ' must be quoted. Example:
#   goldfish_audit: 'verdict: audited'
#   note: 'status: implemented, drift_report: pending'
us_id: <US-ID>
spec_version: 0.1.0
created_by: 01-sdd-spec-writer
created_at: <YYYY-MM-DD>
status: draft | audited | approved | implemented | observed | certified
goldfish_audit: <path to audit.md or "pending">
drift_report: <path to drift-report.md or "pending">
```
