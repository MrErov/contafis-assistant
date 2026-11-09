---
name: sdd-spec-writer
phase: 1
version: 0.1.0
description: Turn a Navigator-provided User Story into a bounded, auditable
  spec by filling templates/spec.template.md. Enforces size limits, testability
  of every acceptance criterion, and a hard stop when the story is not ready.
compatible_with: [opencode, claude-code, cursor, copilot-cli, gemini-cli, generic]
inputs:
  - user_story: free text describing role + capability + benefit (Navigator-provided)
  - us_id: suggested identifier (e.g. US-001); agent may propose if absent
  - context_doc: path to the bounded-context document for the affected area
outputs:
  - specs/<US-ID>.spec.md  (filled from templates/spec.template.md, Sections 1–4 + 7–8)
estimated_tokens: 2000-4000
requires: [00-context-bootstrap]
blocks: [02-egm-goldfish-audit, 03-tdd-cycle, 05-drift-control]
---

# Meta-Skill 01 — SDD Spec Writer (Lightweight Spec)

## Objective

Turn **one** User Story into **one** bounded spec file, by filling
`templates/spec.template.md`. No prose. No planning document. No 30-page
design. Just enough structure for the Goldfish Audit (Skill 02) to find gaps
and for Strict TDD (Skill 03) to have a scope contract.

> **Golden rule:** the template is the ceiling, not the floor. If you cannot
> fit the story in the template's line limits, the story is too big — split
> it or return it. Do **not** expand the template.

## When to use

- After Phase 0 has produced `docs/context/` and the Navigator has provided a
  User Story for the affected bounded context.
- Before any code, any test, or any plan. Always.

Do **not** use when:

- The User Story is missing or ambiguous → escalate to Navigator.
- No context doc exists for the affected area → invoke `00-context-bootstrap`
  on that scope first.
- The Navigator wants to "just sketch something" → refuse politely, route
  through this skill. (See `AGENTS.md` §5 on vibe coding.)

## Agent role

You are the **Spec Writer**. You are allowed to:

- Read the context doc for the affected bounded context (mandatory).
- Read `docs/context/00-overview.md` and `docs/context/01-dependency-map.md`
  for stack and boundary awareness.
- Read `templates/spec.template.md` (mandatory — it is the output shell).
- Read the files of the affected bounded context **selectively** (interface
  files, models, routes) — never whole directories, never >300 lines per file.
- Write **only** `specs/<US-ID>.spec.md`.

You are **forbidden** to:

- Read code outside the affected bounded context.
- Run build/test commands.
- Write tests (that is Skill 03).
- Write code (that is Skill 03).
- Modify `templates/spec.template.md`.
- Fill Sections 5 or 6 unless explicitly justified (they are `[O]`).
- Produce a second spec file in the same invocation.

## Protocol (6 steps)

### Step 1 — Load context (cost: ~300 tokens)

1. Read `docs/context/00-overview.md` (stack, commands, contexts).
2. Read the `context_doc` provided by the Navigator.
3. Read `docs/context/01-dependency-map.md` if the story touches more than
   one context.

**If `context_doc` does not exist**, stop:

```
🛑 Escalation — Phase 1
Rule at risk: requires: [00-context-bootstrap]
Reason: docs/context/<name>.md not found for context "<name>".
Proposed Navigator action: invoke 00-context-bootstrap with
optional_scope=<path> before writing this spec.
```

### Step 2 — Validate story readiness (cost: ~200 tokens)

Before opening the template, answer these internally. Any "no" is a hard stop.

- [ ] The story has a clear **role** (who), **capability** (what), **benefit** (why).
- [ ] The story targets **one** bounded context. If it crosses two, stop and ask
      the Navigator to split it.
- [ ] At least **3** acceptance criteria can be written from the story text
      alone. If fewer, the story is under-specified → escalate.
- [ ] No more than **7** acceptance criteria can be written without splitting
      the story. If more, the story is too big → escalate with a proposed split.
- [ ] The story does not require architectural decisions the Navigator has
      not made (new service, new datastore, new framework). If it does,
      escalate; architecture is the Navigator's call, not the spec's.

**If any check fails**, stop and emit:

```
🛑 Escalation — Phase 1
Check failed: <item>
Reason: <one line>
Proposed Navigator action: <split the story / clarify scope / make architecture decision / invoke 00-context-bootstrap>
```

Never fill the template anyway "to be helpful". That is vibe coding
(`AGENTS.md` §5).

### Step 3 — Fill the template (cost: ~1500–3000 tokens)

Copy `templates/spec.template.md` verbatim into `specs/<US-ID>.spec.md`.
Fill **in place**. Do not reorder sections. Do not add sections. Fill:

- **Section 1 [M] User Story** — As-a / I-want / So-that in the Navigator's words,
  plus an explicit out-of-scope list. ≤10 lines.
- **Section 2 [M] Acceptance Criteria** — 3–7 Given/When/Then rows.
  Each row must be **verifiable by a single test**. If you cannot describe
  the test in one sentence, the AC is not an AC — rewrite it or escalate.
- **Section 3 [M] Interface Contracts** — input contract (verb, path,
  payload, responses) and output contract (side effects, persistence,
  emissions). If the story is internal, write `N/A — internal story` and
  justify in one line.
- **Section 4 [M] Test Plan (TDD seed)** — 5–10 rows. One test per AC
  minimum. Each test names the AC it covers and its type (unit /
  integration / e2e). All tests start **RED**.
- **Section 7 [M] Exit Gate** — fill the checklist as the
  self-check output of Step 4. Do **not** pre-check all boxes.
- **Section 8 [M] Traceability Metadata** — `us_id`, `spec_version:
  0.1.0`, `created_by: 01-sdd-spec-writer`, `created_at`, `status: draft`,
  `goldfish_audit: pending`, `drift_report: pending`.

**Sections 5 [O] and 6 [O]:** fill **only if** the Navigator explicitly
mentioned a dependency/risk, or the story has a visual/runtime observation
component. Otherwise **delete the section entirely** — do not write "N/A".
An optional section filled with "N/A" is noise.

### Step 4 — Self-check against Section 7 gates (cost: ~200 tokens)

Open the file you just wrote. Walk through Section 7's checklist honestly.

| Gate | Criterion | Blocking |
|------|-----------|----------|
| G1 | Section 1 has As-a/I-want/So-that format | yes |
| G2 | Out-of-scope list is non-empty | yes |
| G3 | 3–7 acceptance criteria, all testable | yes |
| G4 | Every AC has ≥1 test in Section 4 | yes |
| G5 | Section 3 is complete or `N/A` is justified in one line | yes |
| G6 | Total file ≤ 120 lines | yes |
| G7 | No sections were added or reordered | yes |
| G8 | Section 8 metadata is fully filled | yes |

**If any blocking gate fails**, do **not** emit the file. Fix it in place
until all pass, **or** escalate to the Navigator with the failing gate and
one proposed fix. Never emit a spec with a known failing gate.

When all gates pass, mark them `[x]` in Section 7 of the file.

### Step 5 — Emit the spec file (cost: ~100 tokens)

Write `specs/<US-ID>.spec.md`. If `specs/` does not exist, create it. Do
not touch any other file.

### Step 6 — Handoff to Goldfish Audit (cost: ~100 tokens)

Emit this exact 3-line message:

```
✅ Spec written: specs/<US-ID>.spec.md (<N> ACs, <M> tests, <L> lines).
📁 Gate: all Section 7 checks passing.
➡️ Next: 02-egm-goldfish-audit. Requires a blind sub-agent (no chat memory).
```

Then **stop**. Do not invoke Skill 02 yourself — the Navigator dispatches the
blind sub-agent. Self-audit defeats the purpose of the Goldfish protocol.

## Size heuristic (how to detect "too big")

A story is too big for one spec if **any** of these is true:

- More than 7 acceptance criteria.
- More than 10 planned tests.
- More than 1 bounded context touched.
- The interface contract section would need >15 lines.
- The out-of-scope list cannot be stated in <5 lines (scope creep signal).

In any of these cases, **stop before filling the template** and propose a
split to the Navigator:

```
⚠️ Story too big for one spec.
Signals: <which heuristics fired>
Proposed split:

<sub-story A> — <ACs covered>

<sub-story B> — <ACs covered>
Approve split? (y/n)
```

## Anti-patterns (forbidden)

- ❌ Expanding the template ("just one more section").
- ❌ Writing a spec longer than 120 lines.
- ❌ Writing ACs that cannot be tested ("the system should be fast").
- ❌ Writing tests (Skill 03 does that).
- ❌ Writing code.
- ❌ Pre-checking Section 7 gates without actually verifying.
- ❌ Filling optional Sections 5/6 with "N/A" to avoid deleting them.
- ❌ Running the Goldfish Audit inline ("I can audit it myself"). You cannot.
- ❌ Producing multiple spec files in one invocation.
- ❌ Reading code outside the affected context "for inspiration".

## Invocation example

**Navigator (in OpenCode):**
> User story: "As a billing admin, I want to retry a failed invoice charge
> up to 3 times with exponential backoff, so that transient payment gateway
> failures don't require manual intervention."
> Context: `billing`. US-ID: US-042.

**Agent:**
1. Reads `docs/context/00-overview.md`, `docs/context/billing.md`,
   `docs/context/01-dependency-map.md`.
2. Runs Step 2 checklist — all pass (3 ACs, 1 context, no new architecture).
3. Fills `specs/US-042.spec.md` from the template:
   - AC-1: first failure retries after 1s
   - AC-2: second failure retries after 2s
   - AC-3: third failure emits `invoice.charge_failed` event and stops
   - Tests T-1..T-4 cover ACs and the idempotency invariant.
4. Self-checks Section 7 — all pass, marks `[x]`.
5. Writes the file, emits the handoff message.
6. **Stops.** Does not open the spec again, does not audit it, does not
   touch code.

## Handoff to Phase 1b (Goldfish Audit)

The Navigator's next action is to spawn a **blind** sub-agent (no chat
memory, no spec-writing context) and point it at
`.meta-skills/02-egm-goldfish-audit.md` with `spec_path =
specs/<US-ID>.spec.md` as input. The Goldfish will produce
`specs/<US-ID>.audit.md` with gaps and bias findings.

Until that audit returns `status: audited`, the spec is a **draft** and
must not enter Phase 2 (TDD). This is enforced by `AGENTS.md` §6.

---

## Design notes (for contributors)

- **Why not write the tests in this skill?** Because mixing "design the
  acceptance criteria" with "design the test suite" reintroduces the
  self-confirmation bias the Goldfish Audit exists to remove. Two skills,
  two separate mental passes.
- **Why 120 lines max?** Because the Drift Check (Skill 05) compares spec
  to code line by line. A 400-line spec is un-auditable. A 120-line spec
  fits in one screen and can be re-read in Phase 4 without rehydration.
- **Why refuse stories with >7 ACs?** Because test coverage of 8+ ACs in
  one slice always produces integration scaffolding that the story did not
  ask for — the exact over-engineering Keel Nav exists to prevent.
- **Why a mandatory context doc read?** Because the spec must speak the
  bounded context's language (domain terms, existing interfaces). Otherwise
  Phase 3's vertical slice will invent a new vocabulary, and Phase 4's
  Drift Check will flag every noun.
- **Why hand off explicitly to Skill 02?** Because `AGENTS.md` §6 requires
  the Navigator, not the agent, to trigger phase transitions. Skill 01
  emits a hint; the Navigator dispatches.
