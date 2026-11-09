---
name: drift-control
phase: 4
version: 0.1.0
description: Final certification. Compare the implemented code against the
  audited spec, line by line, to certify no-drift (or reject). Closes the
  Keel Nav flow. Does not fix, does not re-audit, does not re-test.
compatible_with: [opencode, claude-code, cursor, copilot-cli, gemini-cli, generic]
inputs:
  - spec_path: "specs/<US-ID>.spec.md (status implemented or observed)"
  - audit_path: "specs/<US-ID>.audit.<N>.md (effective_verdict audited)"
  - test_plan_path: "test plan derived from spec Section 4"
  - code_scope: "git-diff base=<sha> OR files=<explicit list>"
outputs:
  - "specs/<US-ID>.drift.<N>.md"
  - "side-effect: spec metadata (status: certified | unchanged, drift_report: path)"
estimated_tokens: 2000-3000
requires: [03-tdd-cycle]
blocks: []
---

# Meta-Skill 05 — Drift Control (Final Certification)

## Objective

Certify that the implemented code **matches the audited spec** and that no
hidden scope, no missing behavior, and no orphan test exists. This is the
only phase that closes the loop.

> **Golden rule:** drift is not "did the tests pass". Drift is "does the
> codebase say exactly what the spec says, and nothing more". A green suite
> plus dead code is drift. An AC with no test is drift. A test with no AC
> is drift. A refactor that changed a public signature without updating
> the spec is drift.

## What "drift" means (5 categories)

| Category | Definition | Example |
|----------|------------|---------|
| **SPEC_DRIFT** | Code implements behavior not declared in the spec. | `utils.py` helper no AC justifies. |
| **CODE_DRIFT** | Spec declares behavior not implemented. | AC-3 promised an event; no emitter exists. |
| **TEST_DRIFT** | Test with no AC, or AC with no test. | `T-4` asserts idempotency; no AC mentions it. |
| **CONTRACT_DRIFT** | Section 3 differs from actual implementation. | Spec says `202`; code returns `200`. |
| **OBSERVATION_DRIFT** | Phase 3 observation no longer matches current code. | Navigator approved A; code now does B. |

**Sixth special state** (not a category): **OBSERVATION_BLOCKED** — Phase
3 was blocked (no runnable environment) and no override exists. The code
may be correct but no human witnessed it. Prevents certification.

## When to use

- After Phase 2 (`status: implemented`) for specs without Phase 3.
- After Phase 3 (`status: observed`) for specs with Phase 3.
- Whenever the Navigator suspects spec/code divergence.
- Before tagging a release.

Do not use when:
- `status` is `draft`/`audited` → not implemented yet.
- `goldfish_audit` is missing → Phase 1 was skipped → escalate.
- The spec has unimplemented ACs → Phase 2 problem, not drift.

## Agent role

**Allowed:** read the four inputs + every file in the declared code scope;
read git metadata to establish scope; write `specs/<US-ID>.drift.<N>.md`;
update exactly two spec fields (`status:`, `drift_report:`).

**Forbidden:** modify code; modify tests; modify spec body; propose fixes;
re-audit the spec; re-run the suite and certify "passes" as drift-free;
read outside scope; reuse prior drift reports; certify with open blocking
findings lacking overrides.

## Protocol (7 steps)

### Step 1 — Load & gate (cost: ~300 tokens)

| Check | Required | If fails |
|-------|----------|----------|
| `spec.status` ∈ {`implemented`, `observed`} | yes | Escalate |
| Audit `effective_verdict: audited` | yes | Escalate |
| `spec.goldfish_audit` points to existing file | yes | Escalate |
| If `observed`, Section 6 non-empty | yes | Escalate |
| `code_scope` provided | yes | Ask Navigator in Step 2 |

### Step 2 — Establish code scope (cost: ~200 tokens)

**Preferred (git):** derive base from `git log --diff-filter=A -- <test-file>`;
`git diff --stat <base>..HEAD` gives the scope. Emit and wait for
Navigator approval.

**Fallback (no git):** derive list from spec Section 3 + test plan +
context-directory conventions. Emit and wait for approval.

If Navigator cannot provide scope, stop. "Audit the codebase" is
unbounded and unauditable.

### Step 3 — Forward check: spec → code (cost: ~1000 tokens)

For each AC and each contract: locate implementation (`file:line`) and
test (`Test ID`). Classify:

| Condition | Verdict |
|-----------|---------|
| Both exist AND test asserts the AC's observable result | `match` |
| Test asserts weaker/different result | TEST_DRIFT |
| No test | TEST_DRIFT |
| No implementation | CODE_DRIFT |
| Implementation does not match declared contract | CONTRACT_DRIFT |

Open the test and read its assertion. A test named `test_retry_succeeds`
that only asserts `is not None` does not match an AC requiring
`{retries: 3}`. Populate §3.1 and §3.2 of the report.

### Step 4 — Backward check: code → spec (cost: ~500 tokens)

For each production file in scope, find its spec anchor (§3 interface
contracts, §6 observations, or "justified by derivation" for infra
migrations/DI). No anchor → **UNJUSTIFIED (scope creep)** → BLOCKING
SPEC_DRIFT. Helper files justified only if trivially inferable. Test
helpers not subject to this check (they go to §5).

### Step 5 — Test traceability (cost: ~500 tokens)

For each test in scope: map to exactly one AC (or one §6 observation).
Classify:

| Condition | Verdict |
|-----------|---------|
| Exactly one AC, asserts its behavior | `match` |
| Zero ACs | ORPHAN (TEST_DRIFT) |
| Multiple ACs | OVERLOADED (TEST_DRIFT) |
| Skipped / commented | DISABLED (BLOCKING TEST_DRIFT) |

An ORPHAN test is drift even if it passes. Populate §5.

### Step 6 — Observation traceability (cost: ~300 tokens, conditional)

Only if `spec.status = observed`. For each §6 observation: covered by e2e?
Still passing? Navigator approved on record? Populate §6. If any is
`OBSERVATION_BLOCKED` or drifted, add a finding. If §6 was edited after
Phase 3, treat as unobserved → OBSERVATION_DRIFT.

### Step 7 — Verdict, report, metadata (cost: ~300 tokens)

Let `B` = blocking findings, `O` = findings with Navigator override,
`A = B - O`.

| Condition | Verdict | New spec status |
|-----------|---------|-----------------|
| `B = 0` | `no-drift` | `certified` |
| `B > 0`, `A = 0` | `drifted-accepted` | `certified` |
| `B > 0`, `A > 0` | `drifted` | unchanged |
| OBSERVATION_BLOCKED, no override | `observation-blocked` | unchanged |

Write the report. Update exactly two fields: `status:` → `certified` on
`no-drift`/`drifted-accepted`, unchanged otherwise; `drift_report:` →
path. Do not touch anything else.

Do not mark `certified` while `A > 0` or an observation is blocked without
an override.

## Navigator overrides (formal mechanism)

An override records that the Navigator has **accepted a blocking finding
as known, acceptable drift**. The finding stays in §7.1 — the override
does not delete it. This preserves the audit's evidence while recording
the decision.

**Override fields (all required):**

| Field | Purpose |
|-------|---------|
| Overrides finding | Traceability (finding ID) |
| Navigator | Accountability |
| Date | Temporal record |
| Justification | Why accepted (one sentence) |
| Scope | Limitation (`this file`, `this AC`, `permanently`, `until refactor`) |

Missing any field → not an override. It is an unrecorded decision, and
the Drift Auditor must reject it as invalid.

**Re-audit trigger:** an override has a scope. If scope was "until
refactor", a future report checks whether the refactor happened. Manual
for v0.1.0.

## Quality gates

| Gate | Criterion | Blocking |
|------|-----------|----------|
| G1 | `spec.status` was `implemented` or `observed` at load | yes |
| G2 | Audit `effective_verdict: audited` | yes |
| G3 | Code scope declared and Navigator-approved (§2) | yes |
| G4 | Every AC in §2 appears in forward traceability | yes |
| G5 | Every production file appears in backward traceability | yes |
| G6 | Every test appears in test traceability | yes |
| G7 | Every finding has category, location, one-sentence body | yes |
| G8 | Verdict computed mechanically from §7 and §8 counts | yes |
| G9 | Spec metadata updated per verdict table | yes |
| G10 | No file outside `specs/` and spec metadata modified | yes |
| G11 | Report body ≤ 250 lines | yes |
| G12 | No fix proposed, no code modified, no test modified | yes |

## Anti-patterns (forbidden)

- ❌ Marking `certified` because "the tests pass".
- ❌ Skipping backward check because "everything looks justified".
- ❌ Treating an ORPHAN test as harmless.
- ❌ Skipping observation check when `status: observed`.
- ❌ Reading test names instead of assertions.
- ❌ Re-auditing the spec inline ("Goldfish missed this"). Escalate.
- ❌ Modifying code or tests to make drift go away.
- ❌ Bypassing the code scope.
- ❌ Recording an override without justification or scope.
- ❌ Certifying with a blocking finding that lacks an override.
- ❌ Producing a second drift report in the same dispatch.

## Invocation example

**Navigator (OpenCode):**

```text
Run `.meta-skills/05-drift-control.md` on `specs/US-042.spec.md`.
Audit: `specs/US-042.audit.2.md`.
Code scope: `git-diff base=1a2b3c4 head=HEAD`.
```

**Agent:** Step 1 gates pass → Step 2 scope = 6 files, Navigator approves
→ Step 3 forward: all 3 ACs `match` → Step 4 backward: `billing/utils.py`
has no anchor → B1 SPEC_DRIFT → Step 5: `test_idempotency` has no AC →
B2 TEST_DRIFT → Step 6: 3 observations match → Step 7: `B=2`, `O=0` →
verdict `drifted` → status unchanged → handoff "routes fixes".

**Navigator's options:** (a) route back to Phase 2 (delete `utils.py`,
add idempotency AC via Phase 1a + new audit round, then rerun Phase 2);
or (b) accept both as overrides in §8 → rerun skill 05 → `drifted-accepted`
→ `certified`.

## Handoff messages

**On `no-drift`:**

```text
✅ Drift certified: specs/<US-ID>.drift.<N>.md (0 blocking).
📁 Spec status: certified. Report: specs/<US-ID>.drift.<N>.md.
🎉 Keel Nav flow complete for <US-ID>. The artifact is certified.
```

**On `drifted-accepted`:**

```text
✅ Drift certified with overrides: specs/<US-ID>.drift.<N>.md
(<B> blocking findings, <O> overridden).
📁 Spec status: certified. Report: specs/<US-ID>.drift.<N>.md.
⚠️ Overrides recorded in §8. Review at refactor time.
🎉 Keel Nav flow complete for <US-ID>.
```

**On `drifted`:**

```text
🛑 Drift rejected: specs/<US-ID>.drift.<N>.md
(<B> blocking findings, <O> overridden, <A> unaddressed).
📁 Spec status: unchanged. Code and spec diverged.
➡️ Next: Navigator routes fixes.

SPEC_DRIFT (unjustified code) → delete code or add an AC.
CODE_DRIFT (missing impl) → Phase 2 re-run.
TEST_DRIFT (orphan/uncovered) → Phase 1a revision + new audit.
CONTRACT_DRIFT → Phase 1a revision.
OBSERVATION_DRIFT → Phase 3 re-observation.
After fixes: new drift round (N+1).
```

**On `observation-blocked`:**

```text
🛑 Drift blocked: specs/<US-ID>.drift.<N>.md
Observation gate not passed (deferred/blocked).
📁 Spec status: unchanged.
➡️ Next: Navigator either (a) runs Phase 3 observation to completion,
or (b) adds a Navigator override accepting the observation gap and
reruns Phase 4.
```

## Design notes

- **Why compare both directions?** Forward catches missing implementation;
  backward catches scope creep. One-directional misses half the drift.
- **Why keep overridden findings in §7.1?** Deleting destroys evidence;
  if scope expires, the finding must be visible for re-check.
- **Why `observation-blocked`?** Certifying un-observed behavior is
  a lie; blocked without override means drift cannot certify.
- **Why require code scope?** "Audit the codebase" is unbounded. A git
  range or file list lets both sides agree what was audited.
- **Why not fix inline?** The auditor is a witness, not a participant; fixing makes the verdict self-serving.

## Known gaps

Moved to [`docs/known-limitations.md`](../docs/known-limitations.md). The
11 live gaps across skills 03, 04, and 05 are consolidated there.
