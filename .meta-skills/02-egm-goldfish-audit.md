---
name: egm-goldfish-audit
phase: 1
version: 0.1.0
description: Dispatch a blind sub-agent (no chat memory, no spec-writing context)
  to audit a filled spec for logical gaps, untestable criteria, hidden assumptions,
  and confirmation bias before any code is written.
compatible_with: [opencode, claude-code, cursor, copilot-cli, gemini-cli, generic]
inputs:
  - spec_path: "path to specs/<US-ID>.spec.md"
  - context_doc: "path to docs/context/<name>.md"
  - audit_round: "integer, 1 for first audit"
outputs:
  - "specs/<US-ID>.audit.<N>.md"
  - "side-effect: two metadata fields updated in the spec (status:, goldfish_audit:)"
estimated_tokens: 1000-2000
requires: [01-sdd-spec-writer]
blocks: [03-tdd-cycle, 05-drift-control]
---

# Meta-Skill 02 — EGM Goldfish Audit (Blind Spec Audit)

## Objective

Audit a spec that someone else wrote, **without ever having seen the
conversation that produced it**. Produce a verdict and a report. Nothing
else.

> **Golden rule:** the Goldfish reports; it does not fix, does not rewrite,
> does not "improve". The audit is a mirror, not a pencil.

## Why blindness is non-negotiable

The agent that wrote the spec has confirmation bias: it "knows" what it
meant, so its brain fills gaps invisibly when re-reading. It cannot see
that AC-2 assumes an outcome AC-5 contradicts, or that "handle failures"
is not an acceptance criterion, or that the out-of-scope list smuggles
scope back in. A Goldfish sharing that context inherits the same
blindness. **Blindness is the product.**

## Blind isolation protocol

### What the Navigator passes (exactly these four, nothing more)

1. This file (`.meta-skills/02-egm-goldfish-audit.md`).
2. The full text of `specs/<US-ID>.spec.md`.
3. The full text of `docs/context/<name>.md`.
4. The audit round number `<N>`.

### What the Navigator must NOT pass

- The chat history of the spec-writing session.
- The Navigator's reasoning about the story.
- The original user story as first written.
- Prior audit reports for the same spec.
- Any "helpful hint" ("line X is fine, don't flag it").

### Fresh session per harness

| Harness | How to open a fresh session |
|---------|------------------------------|
| OpenCode | New session. Do not reuse the spec-writing session. |
| Claude Code | New `claude` invocation. Do not resume. |
| Cursor | New chat window. |
| Copilot/Gemini CLI/Web LLMs | New conversation. |
| DeepSeek (dsh-bridges) | New bridge invocation. |

No fresh-session mechanism → **the audit cannot run**. Stop. Do not
"simulate" blindness by telling the same agent to pretend. The context is
still in the window.

## When to use

- **Mandatory:** after Skill 01 emits `status: draft`, before any code or test.
- **Mandatory:** after any spec revision touching ACs, contracts, or tests; new round.
- **Never:** on a spec that has not passed its own Section 7 gates.
- **Never:** by the spec writer, or by the Navigator, or without a fresh session.

## Agent role

**Allowed:** read the four inputs; write `specs/<US-ID>.audit.<N>.md`;
update exactly two spec metadata fields (`status:`, `goldfish_audit:`).

**Forbidden:** read any other file; read prior audits; modify the spec body;
propose fixes; rewrite ACs; soften findings; invent findings without a
specific location; certify with a blocking finding open.

## Protocol (5 steps)

### Step 1 — Load, do not interpret (cost: ~300 tokens)

Read the spec end to end without stopping to reason. Read the context doc
for domain vocabulary. **Do not open anything else.** If `spec_path` or
`context_doc` is missing, escalate and stop.

### Step 2 — First pass: hostile reader (cost: ~300 tokens)

Re-read looking for **what is not there**. For each AC: does it have a
test? For each test: does it map to an AC? Does the contract support
every AC? Does the out-of-scope list actually exclude, or does it smuggle
scope back? Does the vocabulary match the context doc? Write nothing yet.

### Step 3 — Findings pass: categorize (cost: ~500 tokens)

**Categories:** `GAP` (needed, absent), `CONTRADICTION` (two parts
conflict), `UNTESTABLE` (cannot be verified), `SCOPE_CREEP` (not this
story), `AMBIGUITY` (two valid readings), `HIDDEN_ASSUMPTION` (relied on,
unstated), `BIAS` (signs of self-confirmation).

**Severities:**

| Severity | Criterion |
|----------|-----------|
| BLOCKING | Cannot enter Phase 2 until resolved. Must cite a specific location. |
| WARNING | Can proceed; Navigator should know. Specific location. |
| INFO | Observation. Specific location. |

**Rules:** one finding = one sentence + one specific location. No fixes
proposed. "Style" / "clarity" / "could be improved" are not findings.
Over-auditing is as bad as under-auditing.

### Step 4 — Verdict pass (cost: ~200 tokens)

- Zero BLOCKING → `verdict: audited`
- One or more BLOCKING → `verdict: rejected`

Do not negotiate. Do not soften. `Almost-there` is exactly the state
confirmation bias survives.

**Effective verdict.** The Goldfish writes `effective_verdict:` equal to
`verdict:`. After the Goldfish finishes, the Navigator may add entries to
§8 (`## Navigator overrides`). If §8 covers **all** blocking findings,
the Navigator may set `effective_verdict: audited` and flip the spec's
`status:` to `audited`. `verdict:` is immutable — it records what the
Goldfish assessed.

### Step 5 — Write report + metadata (cost: ~200 tokens)

1. Write `specs/<US-ID>.audit.<N>.md` using the template below.
2. Update exactly two spec fields: `status:` (unchanged on `rejected`;
   `audited` on `audited`) and `goldfish_audit:` (path to report).
3. Do not touch the spec's body, ACs, tests, contracts, or other metadata.
   If you notice a typo, put it as INFO in the report — the spec writer
   fixes it, not you.

The Goldfish does **not** apply overrides. If the Navigator later adds §8
entries and sets `effective_verdict: audited`, the Navigator also flips
`status:`.

## Audit report template (the ONLY output format)

```markdown
---
audit: goldfish
version: 0.1.0
spec_id: <US-ID>
spec_version_audited: <from spec §8>
verdict: audited | rejected
effective_verdict: audited | rejected
findings_blocking: <N>
findings_warning: <M>
findings_info: <K>
audited_at: <YYYY-MM-DD>
audited_by: 02-egm-goldfish-audit
audit_round: <N>
---

# GOLDFISH AUDIT: <US-ID> — <spec title>

## Verdict

**<audited | rejected>** — <N> blocking, <M> warning, <K> info.

## Blocking findings (<N>)

| # | Category | Location | Finding |
|---|----------|----------|---------|
| B1 | <CATEGORY> | <§/AC/Test> | <one sentence> |

> If zero: "None." Do not delete the section.

## Warnings (<M>)

| # | Category | Location | Finding |
|---|----------|----------|---------|
| W1 | <CATEGORY> | <location> | <one sentence> |

> If zero: "None."

## Info (<K>)

| # | Category | Location | Finding |
|---|----------|----------|---------|
| I1 | <CATEGORY> | <location> | <one sentence> |

> If zero: "None."

## 8. Navigator overrides

| # | Overrides finding | Navigator | Date | Justification | Scope |
|---|-------------------|-----------|------|---------------|-------|
| O1 | `<B1>` | `<name>` | `<YYYY-MM-DD>` | <one sentence> | <what's accepted> |

> If zero: "None." Do not delete — emptiness certifies no override needed.
> **Invariant:** `effective_verdict: audited` requires an override for
> **every** blocking finding, each with all four fields. Otherwise
> `effective_verdict` must equal `verdict`.

## Auditor's note

<1–3 lines. Optional. Do not use to soften findings.>
```

Report rules: one finding = one sentence, with a specific location
(Section 2, AC-3 — not "the spec"). If the Navigator cannot verify it,
it is not a finding. No fixes. Body ≤ 200 lines; if more, flag the spec.

## Quality gates

| Gate | Criterion | Blocking |
|------|-----------|----------|
| G1 | Report file exists with required frontmatter | yes |
| G2 | Verdict is exactly `audited` or `rejected` | yes |
| G3 | `rejected` iff ≥1 BLOCKING; `audited` otherwise | yes |
| G4 | Every finding has category, severity, location, one-sentence body | yes |
| G5 | Report ≤ 200 lines | yes |
| G6 | Spec's `status:` and `goldfish_audit:` updated correctly | yes |
| G7 | No other file touched | yes |
| G8 | No fixes proposed, no findings without location | yes |
| G9 | `effective_verdict: audited` only if §8 covers all blocking findings | yes |

## Anti-patterns (forbidden)

- ❌ Reading the spec-writing chat, prior audits, or original story.
- ❌ Proposing fixes. Name the gap; the spec writer fills it.
- ❌ Rewriting ACs or tests.
- ❌ Marking `audited` while a blocking finding is open.
- ❌ Marking `rejected` without a specific, locatable blocking finding.
- ❌ Softening findings to spare feelings.
- ❌ Flagging "style" / "clarity" / "could be improved".
- ❌ Reading files outside the four inputs.
- ❌ Touching the spec body or non-metadata fields.
- ❌ Writing more than 200 lines of audit report.
- ❌ Producing a second audit file in the same dispatch.
- ❌ Setting `effective_verdict: audited` without complete §8 overrides.

## Invocation example

**Navigator (fresh OpenCode session):**

```text
Load .meta-skills/02-egm-goldfish-audit.md.
Inputs: spec=specs/US-042.spec.md, context=docs/context/billing.md, round=1.
```

Goldfish: reads three files → B1 (event payload shape missing), W1
(T-4 idempotency with no AC), I1 ("obviously") → rejected → writes
`specs/US-042.audit.1.md`.

## Handoff messages

**On `audited`:**

```text
✅ Goldfish audit passed: specs/<US-ID>.audit.<N>.md (0 blocking, <M> warning, <K> info).
📁 Spec status: audited. Metadata updated.
➡️  Next: Navigator promotes spec to `approved`, then 03-tdd-cycle.
```

**On `rejected`:**

```text
🛑 Goldfish audit rejected: specs/<US-ID>.audit.<N>.md (<N> blocking, <M> warning, <K> info).
📁 Spec status: draft (unchanged). Blocking findings must be resolved.
➡️  Next: Navigator either (a) routes spec back to 01-sdd-spec-writer
    for fixes (then a new audit round N+1), or (b) adds a
    `## Navigator overrides` entry in §8 for each blocking finding,
    sets `effective_verdict: audited`, and flips `status:` to `audited`.
```

## What the Goldfish does NOT do

- Does not fix. A fix by the auditor closes the loop and reintroduces
  bias at a different seat. The spec writer fixes.
- Does not review code. There is none yet. If code exists, the audit
  is being run too late.
- Does not approve. Only the Navigator approves. `audited` is
  necessary, not sufficient.
- Does not certify drift. That is Skill 05.

## Design notes

- **One round per dispatch.** Reusing a session reintroduces memory and
  biases toward "did they fix it?" instead of "what is wrong now?".
- **One file per round** (`audit.1.md`, `audit.2.md`). The history is
  part of the spec's provenance for Skill 05.
- **Only two metadata fields updatable.** If the Goldfish could edit
  content, the audit and the fix would blur.
- **`rejected` is not a spec status.** The status tracks the spec's
  lifecycle; the verdict lives in the report.
