---
name: tdd-cycle
phase: 2
version: 0.1.0
description: Execute a strict Red-Green-Refactor cycle against an audited spec.
  Write all tests first (batch RED), then implement one test's worth of minimum
  code at a time (incremental GREEN). No code without a failing test, no code
  beyond what a test requires.
compatible_with: [opencode, claude-code, cursor, copilot-cli, gemini-cli, generic]
inputs:
  - spec_path: path to specs/<US-ID>.spec.md (status must be audited or approved)
  - audit_path: "path to specs/<US-ID>.audit.<N>.md (effective_verdict: audited, possibly via overrides)"
outputs:
  - test files at the paths declared by the spec's Section 4 (or derived)
  - production code at the paths declared by the spec's Section 3
  - side-effect: "spec metadata updated (status: implemented, drift_report: pending)"
estimated_tokens: 2000-6000
requires: [01-sdd-spec-writer, 02-egm-goldfish-audit]
blocks: [04-odd-vertical-slice, 05-drift-control]
---

# Meta-Skill 03 — TDD Cycle (Strict Red-Green-Refactor)

## Objective

Turn an audited spec into tested code by running the strict TDD cycle:
**write all tests first (RED), then implement the minimum code per test
(GREEN), then refactor only if tests stay green.** Produce production code
that is traceable line-by-line to the spec, and stop the instant the last
test passes.

> **Golden rule:** code exists only to make a failing test pass. If no test
> fails without it, the code must not be written. If the last test passes,
> you stop — even if "one more method would be nice".

## When to use

- After Phase 1 is complete: spec `status` is `audited` or `approved`,
  audit verdict is `audited`, and Section 7 gates are all checked.
- Before any UI, DB, or infrastructure work (that is Phase 3 — ODD).
- Before any drift check (that is Phase 4).

Do **not** use when:

- Spec `status: draft` → invoke `02-egm-goldfish-audit` first.
- Spec `status: implemented` → this phase is already done.
- No `goldfish_audit` path in spec metadata → audit not run → refuse.
- The Navigator wants to "prototype" or "sketch" → refuse politely, route
  through `01-sdd-spec-writer` + `02-egm-goldfish-audit`.

## Agent role

You are the **TDD Practitioner**. You are allowed to:

- Read the spec (`spec_path`), the audit report (`audit_path`), the bounded
  context doc referenced by the spec, and existing test/config files for the
  affected context.
- Write test files at the paths declared or derivable from Section 4 of the
  spec.
- Write production code at the paths declared or derivable from Section 3
  of the spec.
- Install the minimum test framework / mock library the spec's tests need
  (declare it explicitly; do not add unrelated tooling).
- Update exactly three metadata fields in the spec: `status:`,
  `spec_version:` (only if a spec revision happened — it should not),
  `drift_report:` (set to `pending`).

You are **forbidden** to:

- Write production code before its test exists and fails.
- Write code not exercised by at least one test.
- Add methods, classes, parameters, error handling, logging, or abstractions
  that no test in Section 4 exercises.
- Delete, weaken, or skip a test from Section 4.
- Add tests outside Section 4 (that is a spec change → escalate).
- Change the spec's acceptance criteria, contracts, or test plan.
- Add dependencies beyond the test framework and mock library.
- "Refactor while you're here" — restructure code not covered by the
  current spec.
- Commit anything. Commits are the Navigator's job (`AGENTS.md` §4).
- Invoke Phase 3 or Phase 4 yourself.

## Protocol (6 steps)

### Step 1 — Load & gate (cost: ~200 tokens)

1. Read `spec_path`. Locate Section 8 metadata.
2. Read `audit_path`. Locate the verdict.
3. Verify all three:

| Check | Required | If fails |
|-------|----------|----------|
| `spec.status` ∈ {`audited`, `approved`} | yes | Escalate (Step 1 escalation). |
| `spec.goldfish_audit` points to an existing file | yes | Escalate. |
| Audit `effective_verdict:` is `audited` (fall back to `verdict:` if the field is absent) | yes | Escalate. |

**Escalation message:**

```
🛑 Escalation — Phase 2
Check failed: <which>
Reason: <one line>
Proposed Navigator action: <run 02-egm-goldfish-audit / fix audit findings / approve spec>
```

Do **not** proceed "to be helpful". This gate is what makes the Goldfish
meaningful. Skip it and the whole flow becomes theater.

### Step 2 — Locate target files (cost: ~300 tokens)

1. Read the affected bounded-context doc.
2. From Section 3 (Interface Contracts), derive expected production file
   paths. Examples:
   - `POST /invoices/{id}/retry` in a Django repo → `billing/views.py`
     or `billing/api/retry.py` (follow the context doc's convention).
   - `formatInvoice(invoice)` in a TS repo → `src/billing/formatInvoice.ts`.
3. From Section 4 (Test Plan), derive expected test file paths. Mirror
   production paths: `test_<module>.py`, `<module>.test.ts`, etc.
4. If the repo has no existing files for this context (greenfield subdir),
   propose paths and **confirm with the Navigator** before writing. Do not
   invent a project structure without approval.

**Output of this step:** a table `{ Section, File path, Action (create | modify) }`.
Emit it in chat before proceeding. The Navigator reads it. If any path is
wrong, they stop you.

### Step 3 — RED phase: write all tests, confirm all fail (cost: ~800–1500 tokens)

1. Create or modify the test files derived in Step 2.
2. Write **one test per row** of Section 4, in the order declared. Do not
   reorder. Do not merge. Do not skip.
3. Each test must:
   - Have a name derived from the Test ID and the covered AC
     (e.g., `test_first_failure_retries_after_1s` for `T-1`).
   - Assert an **observable behavior** stated in Section 2 (not internal
     implementation details).
   - Use the minimum mocking needed. Mock external boundaries (HTTP, DB,
     clock) only. Do not mock internal collaborators unless the spec
     explicitly requires it.
4. Run the test suite for the affected module.
5. **Confirm ALL tests fail.** Every single one. If any test passes:
   - Investigate why.
   - If the test is tautological (no assertion, or asserts `true`) → fix the test.
   - If production code already exists that makes it pass → that code is
     pre-existing scope creep. Escalate.
   - If the test is testing the wrong thing → fix the test.
   - Any passing test in the RED phase is a **BLOCKING finding**.

**Do not proceed to Step 4 with a green test.** A passing test at RED
means the test or the codebase is lying.

Emit the RED summary:

```
🔴 RED phase complete.
Tests written: <N> (T-1..T-N).
All failing: yes.
Failing reason: <one line per test — e.g. "T-1: ImportError, module has no
retry function", "T-2: AssertionError, retries called once instead of twice">
```

### Step 4 — GREEN phase: one test at a time, minimum code (cost: ~1500–3000 tokens)

For each test, **in Section 4 order**:

1. Identify the target test (`T-i`). All others must remain RED.
2. Write the **minimum code** to make `T-i` pass. The definition of
   "minimum" is literal:
   - Smallest possible diff.
   - No methods, classes, or parameters not directly exercised by `T-i`.
   - No imports beyond what `T-i` needs.
   - No error handling, logging, or type hints unless `T-i` tests them.
   - No abstraction layers, interfaces, or hooks.
   - No comments (the tests are the documentation).
3. Run the full test suite for the affected module.
4. Verify:
   - `T-i` is **GREEN**.
   - Every `T-j` with `j < i` is still **GREEN**.
   - Every `T-k` with `k > i` is still **RED**.
5. **If any not-yet-targeted test goes GREEN**, you over-wrote. Revert
   the extra code. Re-implement more minimally. Retry until only `T-i`
   flips.
6. **If `T-i` fails after implementation**, investigate:
   - Is the test wrong? (spec mismatch) → escalate, do not change the test.
   - Is the implementation wrong? → fix.
   - Is a dependency missing? → install the minimum dependency, declare it.

Emit a per-test GREEN summary:

```
🟢 T-<i> GREEN.
Code added: <N> lines in <path>.
Suite state: <i> green, <N-i> red.
Next target: T-<i+1>.
```

Repeat until all N tests are GREEN.

### Step 5 — REFACTOR phase (optional, cost: ~500 tokens)

**Only if** at least one of these is true after all tests pass:

- The code has visible duplication (>3 identical lines repeated).
- A function/method exceeds the repo's typical size by >50% with no test
  justifying the extra size.
- The code has dead branches (branches no test exercises).

**Refactor only:**

1. Restructure without adding behavior.
2. Run the full suite after every change. **All tests must stay GREEN.**
3. If a refactor requires a new test → **stop**. That is a spec change,
   not a refactor. Escalate.
4. Emit a refactor summary:

```
♻️ Refactor applied: <one line>.
All tests still GREEN: yes.
```

**Do NOT refactor if:**

- The code is already clean → skip this phase entirely. Silence is fine.
- "It would look nicer" → that is style, not refactor.
- "One more abstraction would help future stories" → that is
  over-engineering. Refuse.

### Step 6 — Final verification & metadata (cost: ~300 tokens)

1. Run the full test suite one last time. Confirm all N tests GREEN.
2. Confirm:
   - No test was deleted from Section 4.
   - No test assertion was removed or weakened.
   - No code line exists that no test exercises (manual pass: for each
     code line added, cite the test).
   - No dependency was added beyond the test framework / mock library.
3. Update the spec's Section 8 metadata:
   - `status: implemented`
   - `spec_version: <unchanged unless a spec revision happened>` (it should
     not have — if it did, escalate)
   - `drift_report: pending`
4. Do not touch any other field of the spec.

## Quality gates (self-check before closing)

| Gate | Criterion | Blocking |
|------|-----------|----------|
| G1 | Spec `status` was `audited` or `approved` at load | yes |
| G2 | Audit verdict was `audited` | yes |
| G3 | All tests from Section 4 written (count matches) | yes |
| G4 | All tests were RED at end of Step 3 | yes |
| G5 | Each test went GREEN one at a time in Section 4 order | yes |
| G6 | Every code line is covered by a test (manual or coverage report) | yes |
| G7 | No test was deleted, weakened, or skipped | yes |
| G8 | No dependency added beyond test framework / mock library | yes |
| G9 | Spec metadata updated correctly (`status: implemented`) | yes |
| G10 | Nothing outside test files, production files, and spec metadata changed | yes |

**If any blocking gate fails**, do not emit the handoff. Fix in place or
escalate to the Navigator with the failing gate and one proposed fix.

## Anti-patterns (forbidden)

- ❌ Writing production code before its test exists and fails.
- ❌ Writing code that no test exercises ("I'll need it later").
- ❌ Adding a method/class/parameter "for future stories".
- ❌ Deleting a test because it's "hard to fix".
- ❌ Weakening an assertion to make it pass.
- ❌ Adding tests not in Section 4 without escalating a spec change.
- ❌ Marking `status: implemented` while any test is RED.
- ❌ Refactoring code outside the current spec.
- ❌ Adding frameworks, ORM helpers, or utilities beyond the test framework.
- ❌ Committing. The Navigator commits.
- ❌ Invoking Phase 3 or 4.
- ❌ "I'll clean it up in the refactor phase" — used as an excuse to
  write sloppy code first.

## Invocation example

**Navigator (OpenCode, fresh session not required):**

> Run `.meta-skills/03-tdd-cycle.md` on `specs/US-042.spec.md`.
> Audit: `specs/US-042.audit.2.md`.

**Agent:**

1. **Step 1:** spec `status: approved`, audit verdict `audited`, all gates
   checked. Proceed.
2. **Step 2:** proposes:
   | Section | File | Action |
   |---------|------|--------|
   | T-1, T-2, T-4 | `tests/billing/test_retry.py` | create |
   | T-3 | `tests/billing/test_retry_events.py` | create |
   | 3.1 | `billing/retry.py` | create |
   | 3.2 | `billing/events.py` | modify (add `invoice.charge_failed` emitter) |

   Navigator approves paths in chat. Agent proceeds.
3. **Step 3:** writes 4 tests (T-1..T-4). Runs suite. All 4 fail.
   - T-1: `ImportError: cannot import 'retry_charge' from 'billing.retry'`
   - T-2: same import error
   - T-3: `AttributeError: module 'billing.events' has no attribute 'emit_charge_failed'`
   - T-4: same as T-1
   Emits RED summary.
4. **Step 4:** implements `retry_charge` minimally to pass T-1 (single retry
   after 1s). Runs suite. T-1 GREEN, T-2/T-3/T-4 still RED. Continues.
   - T-2: adds exponential backoff (2s). T-1 stays GREEN, T-2 GREEN.
   - T-3: adds `emit_charge_failed` to `billing/events.py`. T-3 GREEN.
   - T-4: implements idempotency key. T-4 GREEN.
5. **Step 5:** no duplication, no dead branches. Refactor skipped.
6. **Step 6:** final suite: 4/4 GREEN. Metadata updated:
   - `status: implemented`
   - `spec_version: 0.1.0` (unchanged)
   - `drift_report: pending`
7. Emits handoff.

## Handoff messages

**On success:**

```
✅ Phase 2 complete: specs/<US-ID>.spec.md implemented.
Tests: <N>/<N> GREEN.
Files created: <list>. Files modified: <list>.
📁 Spec metadata: status=implemented, drift_report=pending.
➡️ Next: 04-odd-vertical-slice. Requires the implemented spec + its
context doc. Replaces mocks with real infra; verifies observable behavior.
```

**On escalation (spec needs revision):**

```
🛑 Escalation — Phase 2
Reason: <spec change needed — e.g. "T-3 requires an interface not declared
in Section 3.2">
Proposed Navigator action: revise spec (01-sdd-spec-writer) → re-audit
(02-egm-goldfish-audit, new round N+1) → re-invoke 03-tdd-cycle.
```

## Design notes (for contributors)

- **Why batch RED, incremental GREEN?** The spec template declares
  `Expected initial state: all RED` in Section 4. Pure per-test TDD (one
  test → one line → next test) contradicts that. Batch RED is also cheaper:
  one test-writing pass, not N. Incremental GREEN preserves the
  anti-over-engineering guarantee because each code diff must flip exactly
  one test. This is the pragmatic TDD that real practitioners do; the
  purist version is for pedagogues.
- **Why is "minimum code" defined so literally?** Because every token spent
  on "just in case" code is debt. The definition is intentionally narrow
  so the agent has no room to negotiate with itself.
- **Why refuse `status: draft`?** Because skipping the Goldfish recreates
  the exact bias the methodology exists to remove. The gate is not
  optional; it is the product.
- **Why not commit?** `AGENTS.md` §4 reserves commits for the Navigator.
  The agent writing files and the agent committing them would blur the
  audit trail.
- **Why allow refactor only conditionally?** Because "refactor" is the
  most over-used word in TDD. It hides behavior changes. The conditional
  gates (duplication, size, dead branches) keep it honest.
- **Why manual coverage verification (not just tool coverage)?** Because
  coverage tools measure lines executed, not lines *required*. A test that
  exercises code as a side effect is not coverage. The manual pass ("cite
  the test") forces explicit traceability.

## Gaps I am not closing in this skill (documented debt)

- **Test framework selection** is left to the repo's existing convention.
  If the repo has none, the agent installs the most standard option for
  the language and declares it. A future `harnesses/<x>/test-framework.md`
  could formalize this per ecosystem.
- **Coverage threshold** is not enforced numerically (e.g., "100%" is not
  required). The manual per-line traceability substitutes. If tooling
  exists, the agent should report it; the skill does not mandate a tool.
- **Cross-context tests** (a test that exercises two bounded contexts)
  are out of scope for Phase 2. They belong to Phase 3's vertical slice.
  If a spec's Section 4 declares such a test, escalate.
