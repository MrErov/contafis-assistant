---
name: odd-vertical-slice
phase: 3
version: 0.1.0
description: Wire a TDD-tested spec into a real end-to-end vertical slice
  (DB → API → UI, or whatever "end" the spec declares) and hand it to the
  Navigator for direct observation. This is the only phase with a mandatory
  human gate before completion.
compatible_with: [opencode, claude-code, cursor, copilot-cli, gemini-cli, generic]
inputs:
  - spec_path: path to specs/<US-ID>.spec.md (status must be implemented)
  - audit_path: "path to specs/<US-ID>.audit.<N>.md (effective_verdict: audited, possibly via overrides)"
  - observation_notes: Section 6 of the spec (required to be non-empty)
outputs:
  - infrastructure/wiring code (DB migrations, API routes, UI components)
  - end-to-end tests derived from Section 6
  - side-effect: "spec metadata updated (status: observed on Navigator approval)"
estimated_tokens: 4000-10000
requires: [03-tdd-cycle]
blocks: [05-drift-control]
---

# Meta-Skill 04 — ODD Vertical Slice (Observation-Driven Development)

## Objective

Take a spec whose logic already passes unit/integration tests (Phase 2) and
make it a **real, observable, end-to-end slice** that the Navigator can run
and verify by direct observation. Phase 3 is the phase where the loop closes:
the Navigator sees the slice working, and either approves it or rejects it.

> **Golden rule:** Phase 3 does not add behavior. It *exposes* behavior
> that Phase 2 already tested. If a behavior is needed and not in Section 6,
> it is a spec change, not a slice task.

## What "vertical slice" means here

A **vertical slice** is one user-facing capability delivered end-to-end.
For this methodology, "end-to-end" is defined by the spec, not by us:

| Spec type | "End" means | Example observation |
|-----------|-------------|---------------------|
| **Library function** | A runnable script that calls the public function | `python -m mylib.demo` prints expected output |
| **Backend API** | A real HTTP request to a running server | `curl` / Postman against `localhost` |
| **CLI tool** | A real invocation from the shell | `mycli retry --invoice 42` |
| **UI feature** | A real interaction in a browser | Navigator clicks and sees the result |
| **Full-stack** | DB → API → UI, all three | Navigator submits a form, sees the DB row, sees the UI update |

**Section 6 of the spec (`Observation Notes`) declares what "end" means
for this spec.** If Section 6 is empty or vague, this skill **cannot run**.

## What "observation" means here

Observation is a **human act**, performed by the Navigator, on a **running
system**, against **Section 6's checklist**. It is not:

- The agent reading code and saying "looks right".
- Passing e2e tests (that is a prerequisite, not observation).
- A screenshot the agent hallucinated.
- The Navigator reading a log the agent pasted.

It is: the Navigator runs the thing, sees the thing work, checks it against
the checklist, and says "yes" or "no". Anything less is theater.

This human gate is the difference between ODD and plain TDD. TDD can be
fully agent-driven. ODD cannot — it requires a human witness.

## When to use

- After Phase 2 (TDD) is complete: spec `status: implemented`, all Section 4
  tests GREEN, no open audit findings.
- Only for specs whose Section 6 declares a non-empty observation checklist.

Do **not** use when:

- Spec `status` is not `implemented` → finish Phase 2 first.
- Section 6 is empty → the spec is a "horizontal layer" (library internals,
  schema change, refactor) and does not need a vertical slice. Skip Phase 3
  and go straight to Phase 4. **Document this skip in the spec metadata.**
- The Navigator cannot observe (headless CI, no runtime) → **stop**. ODD
  requires observation. If you cannot observe, this spec is not a slice,
  it is a library increment. Convert it to a spec without Section 6 and
  skip Phase 3.

## Agent role

You are the **Slice Builder**. You are allowed to:

- Read the spec, the audit, the bounded context doc, Phase 2's code and
  tests, and the repo's existing infra patterns (how other slices are wired).
- Write infrastructure and wiring code: DB migrations, API routes,
  dependency injection, UI components — **only** what Section 3 or
  Section 6 declares as necessary.
- Write end-to-end tests derived from Section 6.
- Install the minimum e2e testing tool (Playwright, SuperTest, RTL, etc.) —
  declare it explicitly.
- Update exactly three metadata fields in the spec: `status:`,
  `observed_at:` (new field, added on Navigator approval), `drift_report:`
  (stays `pending`).

You are **forbidden** to:

- Add behavior not declared in Section 2 (Acceptance Criteria) or
  Section 3 (Interface Contracts).
- Modify Phase 2's tests. They are frozen. If a change is needed, escalate.
- Modify Phase 2's production logic. If a bug is found, escalate (it is a
  Phase 2 defect, not a Phase 3 feature).
- Add UI polish, extra screens, extra endpoints, extra fields.
- Add logging, metrics, or observability beyond what the e2e tests assert.
- "Refactor while you're here" — refactors are Phase 2 scope, not Phase 3.
- Mark `status: observed` yourself. Only the Navigator does that.
- Skip the observation gate because "the tests pass".
- Commit anything.

## Protocol (7 steps)

### Step 1 — Load & gate (cost: ~300 tokens)

1. Read `spec_path`. Locate Section 8 metadata.
2. Verify:

| Check | Required | If fails |
|-------|----------|----------|
| `spec.status` is `implemented` | yes | Escalate (Phase 2 not done). |
| `spec.goldfish_audit` points to a file with `effective_verdict: audited` | yes | Escalate (Phase 1 not done). |
| Section 6 is non-empty and specific | yes | If empty → **skip Phase 3** (see "When to use"). If vague → escalate. |
| Section 4 tests pass (rerun them) | yes | Escalate (Phase 2 regression). |

**Escalation message:**

```
🛑 Escalation — Phase 3
Check failed: <which>
Reason: <one line>
Proposed Navigator action: <finish Phase 2 / fill Section 6 / re-run 03-tdd-cycle>
```

### Step 2 — Slice thickness check (cost: ~200 tokens)

Before writing any code, audit the slice's shape from Section 6:

1. Count the distinct behaviors Section 6 asks the Navigator to observe.
2. Apply:

| Behaviors in Section 6 | Verdict | Action |
|------------------------|---------|--------|
| 1 – 5 | Healthy slice | Proceed. |
| 0 | Not a slice | Skip Phase 3 (see "When to use"). |
| 6 – 10 | Slice too thick | Escalate: propose split into 2 specs. |
| > 10 | Horizontal layer, not a slice | Escalate: spec is wrong shape. |

3. Additionally check: does Section 3 declare the infrastructure targets
   (DB tables, API routes, UI components) needed to expose these behaviors?
   If yes, list them. If no, escalate — the spec is missing its wiring
   contract.

**Output of this step** (emit in chat, wait for Navigator confirmation
before proceeding):

```
Slice thickness check:

Section 6 behaviors: <N> (<list one line each>)

Verdict: healthy / too thick / not a slice

Wiring targets from Section 3: <list>
Proceed? (y/n)
```

### Step 3 — Wiring pass: infrastructure only, minimum code (cost: ~2000–5000 tokens)

Write the **plumbing** that connects Phase 2's logic to the real world.
"Plumbing" means, and only means:

- **Database:** schema/migrations for entities Section 3.2 declares as
  persisted. No indexes, no optimizations, no constraints beyond what the
  spec requires.
- **API:** route registration, request parsing, response serialization
  for endpoints Section 3.1 declares. No middleware, no auth, no
  rate-limiting unless declared.
- **Dependency injection:** the minimum wiring to instantiate the service
  with real collaborators instead of mocks.
- **UI:** components Section 3.2 declares (forms, tables, buttons).
  No styling, no animations, no accessibility beyond the platform default,
  no "polish".

**Wiring is not behavior.** If wiring requires adding a conditional, a
validation, or a fallback that no test covers → that is behavior → escalate.

**Framework convention:** follow the repo's existing patterns. If a
`billing/` slice already uses `FastAPI` + `SQLAlchemy` + `React`, the new
`billing` slice uses the same. If there is no precedent, **ask the
Navigator** before choosing a stack. Never invent a parallel stack.

**Confirm before writing:** for each file to be created, the agent emits:

```
Wiring plan:
<path> ← Section 3.<n> / Section 6.<m>
<path> ← Section 3.<n> / Section 6.<m>
Proceed? (y/n)
```

No file gets written without a Section 3 or Section 6 anchor.

### Step 4 — E2E tests: TDD them from Section 6 (cost: ~1000–2000 tokens)

Section 6 is the e2e test plan. Each observation behavior becomes one e2e
test. Follow the same TDD discipline as Phase 2:

1. **RED:** write all e2e tests first, run them, confirm all fail.
   - Fail for the *right* reason (e.g., "server not running" is not a
     real RED; the test must reach the server and fail on the assertion).
   - If a test passes on the first run, something is wrong — investigate.
2. **GREEN:** implement the minimum wiring to make each e2e test pass,
   one at a time, in Section 6 order.
3. **Verify Phase 2 tests still pass** after every green e2e test.

E2E tests must use **real infrastructure** (real DB, real HTTP, real
browser). No mocks in Phase 3. If a real DB is unavailable, escalate —
testcontainers, in-memory SQLite, or a real local instance are acceptable;
mock ORMs are not.

Emit per-test summary (mirror Phase 2's format):

```
🔴 RED phase complete. <N> e2e tests written, all failing.
🟢 T-e2e-<i> GREEN. Phase 2 suite: still all GREEN.
```

### Step 5 — Freeze Phase 2, verify no regression (cost: ~300 tokens)

After all e2e tests pass:

1. Re-run the Phase 2 test suite. Every test must be GREEN. If any is RED,
   the wiring broke Phase 2's logic → revert the offending wiring → fix.
2. Confirm:
   - No Phase 2 test file was modified (diff should show zero changes).
   - No Phase 2 production file was modified (same check).
   - No new dependency beyond the e2e tool was added.

**If a Phase 2 test now fails**, this is a Phase 2 defect surfaced by real
infra. Escalate:

```
🛑 Escalation — Phase 3
Reason: <T-i in Phase 2 now fails because real <DB|API|UI> reveals <issue>.
Proposed Navigator action: route back to 03-tdd-cycle with a spec revision
(this is a Phase 2 defect, not a Phase 3 feature).
```

Do **not** patch Phase 2's tests to accommodate the e2e failure. That would
hide a real bug.

### Step 6 — Human observation gate (cost: ~200 tokens + Navigator's time)

This step is **blocking on the Navigator**, not on the agent.

1. Start the system (or document how the Navigator starts it).
2. Emit the observation checklist below. **Pause. Do not proceed to Step 7
   until the Navigator replies.**

   **Observation checklist format:**

   ```
   🧭 ODD Observation Gate — <US-ID>

   The slice is running at: <URL | command | path>
   To observe: <exact steps>
   Section 6 checklist:

   [ ] 1. <behavior 1>
   [ ] 2. <behavior 2>
   [ ] 3. ...

   Please run the system and observe each item. Reply with:

   "approved" (all checks pass) → I proceed to Step 7.

   "failed: <item number>: <observation>" → I escalate to Navigator for
   spec revision or Phase 2 defect routing.

   "cannot observe: <reason>" → I stop and document this slice as
   observation-deferred (see Notes on CI/headless).

   Awaiting observation.
   ```

3. When the Navigator replies:
   - `approved` → go to Step 7.
   - `failed: ...` → escalate. Do not silently fix; the failure may be a
     spec issue, a Phase 2 defect, or a Phase 3 wiring issue. Let the
     Navigator decide which.
   - `cannot observe: ...` → stop. Document in the spec metadata that
     observation was deferred. The slice can go to Phase 4, but the
     `observed_at` field stays unset and Phase 4's report will flag it.

**The agent does not proceed to Phase 4 on its own.** Even after Navigator
approval. Phase 4 is dispatched by the Navigator.

### Step 7 — Metadata update (cost: ~100 tokens)

On Navigator approval, update exactly:

1. `spec.status: observed`
2. `spec.observed_at: <YYYY-MM-DD>` (new field, added if missing)
3. `spec.drift_report: pending` (unchanged)

Do not touch anything else.

Emit the handoff (below) and stop.

## Quality gates (self-check before closing)

| Gate | Criterion | Blocking |
|------|-----------|----------|
| G1 | Spec was `status: implemented` at load | yes |
| G2 | Section 6 is non-empty and specific | yes |
| G3 | Slice thickness check ran and was healthy (1–5 behaviors) | yes |
| G4 | Every wiring file is anchored to Section 3 or Section 6 | yes |
| G5 | All e2e tests from Section 6 written and GREEN | yes |
| G6 | Phase 2 tests still all GREEN | yes |
| G7 | No Phase 2 test or production file modified | yes |
| G8 | No new dependency beyond the e2e tool | yes |
| G9 | Observation checklist emitted and Navigator replied `approved` | yes |
| G10 | Metadata updated correctly (`status: observed`, `observed_at`) | yes |

**If any blocking gate fails**, do not emit the handoff. Fix in place or
escalate to the Navigator.

## Anti-patterns (forbidden)

- ❌ Adding behavior not in Section 2 or Section 3.
- ❌ Adding UI polish, extra screens, extra endpoints.
- ❌ Adding logging/metrics beyond what e2e tests assert.
- ❌ Modifying Phase 2 tests or production code.
- ❌ Mocking infrastructure in e2e tests ("in-memory DB" is fine;
  "mock ORM" is not).
- ❌ Marking `status: observed` without Navigator approval.
- ❌ Skipping the observation gate because "the tests pass".
- ❌ Proceeding to Phase 4 without Navigator dispatch.
- ❌ Choosing a stack without Navigator confirmation.
- ❌ "Refactoring while you're here".
- ❌ Adding accessibility, i18n, or styling not declared in Section 3.2.
- ❌ Emitting a checklist the Navigator cannot actually run (e.g., requires
  an env var not documented).

## Invocation example

**Navigator (OpenCode):**

> Run `.meta-skills/04-odd-vertical-slice.md` on `specs/US-042.spec.md`.
> Audit: `specs/US-042.audit.2.md`.

**Agent:**

1. **Step 1:** spec `status: implemented`, audit `audited`, Section 6 lists
   3 behaviors. Phase 2 tests: 4/4 GREEN. Proceed.
2. **Step 2:** slice thickness check — 3 behaviors, healthy. Wiring targets:
   - `POST /invoices/{id}/retry` (Section 3.1)
   - `invoice.charge_failed` event (Section 3.2)
   - `invoice_retry_log` table (Section 3.2)
   Emits plan, Navigator approves.
3. **Step 3:** writes:
   - `migrations/0007_invoice_retry_log.sql`
   - `billing/routes.py` (adds `POST /invoices/{id}/retry`)
   - `billing/di.py` (wires `retry_charge` to real gateway + real DB)
   Confirms each against Section 3 / Section 6. 0 extra files.
4. **Step 4:** writes 3 e2e tests:
   - `tests/e2e/test_retry_e2e.py::test_first_retry_succeeds_after_1s`
   - `tests/e2e/test_retry_e2e.py::test_second_retry_succeeds_after_2s`
   - `tests/e2e/test_retry_e2e.py::test_third_retry_emits_event_and_stops`
   RED: all 3 fail (endpoint 404). GREEN: add route handler, then wiring,
   then event emitter. All 3 pass.
5. **Step 5:** Phase 2 suite still 4/4 GREEN. No Phase 2 files touched.
6. **Step 6:** emits observation checklist:

   ```
   🧭 ODD Observation Gate — US-042
   Slice running at: http://localhost:8000
   To observe: run uvicorn billing.app:app --reload, then execute the
   three curl commands in specs/US-042.observation.md.

   Section 6 checklist:
   [ ] 1. First failed charge retries after ~1s and succeeds.
   [ ] 2. Second failed charge retries after ~2s and succeeds.
   [ ] 3. Third failed charge emits invoice.charge_failed and stops.
   Awaiting observation.
   ```

   Navigator runs the commands, observes behavior matching all 3 checks,
   replies `approved`.
7. **Step 7:** metadata updated (`status: observed`, `observed_at: ...`).
   Handoff emitted.

## Handoff messages

**On success:**

```
✅ Phase 3 complete: specs/<US-ID>.spec.md observed.
E2E tests: <N>/<N> GREEN.
Phase 2 tests: <M>/<M> GREEN (no regression).
Files created: <list>. Files modified: <list>.
🧭 Observation: Navigator approved on <date>.
📁 Spec metadata: status=observed, observed_at=<date>, drift_report=pending.
➡️ Next: 05-drift-control. Compares spec vs. code for non-drift.
```

**On escalation (spec change needed):**

```
🛑 Escalation — Phase 3
Reason: <e.g., "Section 6 requires observing behavior not in Section 2">
Proposed Navigator action: revise spec → new audit round → re-invoke
03-tdd-cycle → re-invoke 04-odd-vertical-slice.
```

**On observation deferred (CI/headless):**

```
⚠️ Phase 3 observation deferred.
E2E tests: <N>/<N> GREEN.
Reason: <no runnable environment / Navigator unavailable>.
This slice can proceed to Phase 4 but will be flagged as
"observation-deferred" in the drift report. Navigator may re-observe
later and update observed_at.
```

## Notes on CI / headless environments

ODD requires human observation. If the Navigator is not available (CI,
async work, headless), the slice can be:

1. **Observation-deferred:** the e2e tests pass, but no human has observed
   the running system. Phase 4 proceeds and flags this. The Navigator can
   re-observe later and update `observed_at`.
2. **Observation-blocked:** no runnable environment exists (e.g., a spec
   that requires cloud infra the repo does not have). The slice cannot
   proceed to Phase 4. This is a spec/scope problem, not a Phase 3 bug.

The skill documents the choice in the spec's Section 8 metadata. It does
not pretend observation happened.

## Design notes (for contributors)

- **Why a human gate and not "the tests passing is enough"?** Because e2e
  tests can pass while the system is broken in ways that only a human
  notices (ugly UI, confusing error messages, slow response). ODD's
  contribution is exactly that the human sees the thing. Skipping the gate
  turns Phase 3 into "Phase 2 with extra steps".
- **Why "wiring is not behavior"?** Because the biggest Phase 3 risk is
  that "I need to wire this" becomes an excuse to add logic. The rule
  that every wiring file must anchor to Section 3 or Section 6 forces the
  agent to point at the spec for each line.
- **Why freeze Phase 2's tests?** Because the temptation to "just tweak
  one assertion" to make the e2e pass is enormous. That tweak hides the
  exact bug Phase 2 was supposed to catch. Frozen tests are the only
  defense.
- **Why a thickness check?** Because "vertical slice" is often used to
  justify horizontal layers. A 15-behavior "slice" is not a slice; it is
  a phase in disguise. The 1–5 heuristic (with escalation for thicker)
  keeps slices honest.
- **Why does the agent not proceed to Phase 4 after approval?** Because
  `AGENTS.md` §6 makes phase transitions Navigator-only. Skill 04 sets
  `status: observed`, emits the handoff, and stops. The Navigator
  dispatches Phase 4.
- **Why can Phase 3 be skipped for backend-only specs?** Because a library
  function has no runtime to observe. That is legitimate. The skill
  documents the skip in metadata; it does not force a fake observation.

## Gaps I am not closing in this skill (documented debt)

- **UI test tool selection:** Playwright vs. Cypress vs. RTL. Follow repo
  convention. A future `harnesses/<x>/e2e-tool.md` could formalize.
- **DB migration convention:** Alembic vs. Prisma vs. raw SQL. Follow repo
  convention. Same future harness note.
- **Observation checklist persistence:** currently emitted as chat text.
  A future `templates/observation-report.template.md` could make the
  Navigator's observations durable. Not in this turn.
