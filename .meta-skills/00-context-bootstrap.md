---
name: context-bootstrap
phase: 0
version: 0.1.0
description: Map a repository (brownfield or greenfield) so later phases plan
  against real architecture instead of assumptions.
compatible_with: [opencode, claude-code, cursor, copilot-cli, gemini-cli, generic]
inputs:
  - repo_root: path to the target repository
  - optional_scope: subdirectory to map (default = whole repo)
outputs:
  - docs/context/00-overview.md
  - docs/context/01-dependency-map.md
  - docs/context/02-risk-register.md
  - docs/context/<bounded-context>.md  (one per bounded context)
estimated_tokens: 3000-8000
requires: []
blocks: [01-sdd-spec-writer, 02-egm-goldfish-audit, 03-tdd-cycle, 04-odd-vertical-slice, 05-drift-control]
---

# Meta-Skill 00 — Context Bootstrap (Elephant Memory)

## Objective

Produce the **structural memory** of the repository before a single line of
spec or code is written. The AI must understand *what exists*, *how it
connects*, and *where the risk is* — without reading whole files unnecessarily.

> **Golden rule:** this skill writes no code, proposes no refactors, emits no
> style opinions. It only **maps**.

## When to use

- Start of any **brownfield** project (mandatory).
- **Greenfield** project: run it anyway to document the empty baseline.
- Before re-planning after a failed `Drift Check` (Phase 4).

## Agent role

You are the **Cartographer Architect**. You are allowed to:

- Read directory listings, manifests, and file names.
- Read files **selectively** (never the whole repo).
- Write only inside `docs/context/`.

You are **forbidden** to:

- Modify production code.
- Run build/test commands.
- Suggest improvements or refactors.
- Invent architecture that is not in the repo.

## Protocol (6 steps)

### Step 1 — Passive reconnaissance (cost: ~500 tokens)

1. List the directory tree to depth 3 (`tree -L 3 -I 'node_modules|.git|dist|build'`).
2. Locate manifests: `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `pom.xml`, `composer.json`, `Gemfile`, `*.csproj`.
3. Locate framework signals: `next.config.*`, `vite.config.*`, `django/`, `rails/`, `fastapi`, `spring/`, `Dockerfile`, `docker-compose.yml`.
4. Extract build/test/lint commands from manifests (npm scripts, Makefile, `tox.ini`, `justfile`).

**Intermediate output:** table `{ stack, frameworks, commands }`.

### Step 2 — Bounded-context discovery (DDD-lite) (cost: ~1k tokens)

1. Group directories by **domain cohesion** (not by file type).
2. Name each bounded context (e.g. `billing`, `auth`, `catalog`, `shared-kernel`).
3. Identify the **Shared Kernel** (types/utilities used by ≥2 contexts).
4. Mark contexts as **isolated** or **coupled**.

**Grouping heuristics:**

- A directory with its own manifest/`__init__`/`index` ⇒ context candidate.
- A directory with ≥5 files and a nameable responsibility ⇒ candidate.
- `utils/`, `common/`, `shared/` ⇒ Shared Kernel (not a business context).

**Intermediate output:** list `{ context_name → paths }`.

### Step 3 — Per-context documentation (cost: ~2k–5k tokens, selective)

For **each detected context**, produce `docs/context/<name>.md` using this
**exact** template:

```markdown
# CONTEXT: <context-name>

## Purpose
<1–2 sentences. What business problem it solves.>

## Responsibilities
- <bullets, max 5>

## Public interface
- `<symbol/endpoint/type>` — <what it exposes>

## Dependencies
- Internal: <other contexts>
- External: <packages/services>

## Persistence / State
- <DB, tables, collections, cache — or "N/A">

## Commands
- Build: `<cmd>`
- Test: `<cmd>`
- Run: `<cmd>`

## Gotchas / Observed tech debt
- <only what is visible in structure, not opinion>

## Last updated
YYYY-MM-DD (skill 0.1.0)
```

Selective reading rules:

- Read only index/`__init__`/main/app/routes/models of the context.
- Never read a file >300 lines in full: request a range summary or grep for symbols.
- If the context has >20 files, document only its public interface + 3 core files.

### Step 4 — Dependency map (cost: ~500 tokens)

Produce `docs/context/01-dependency-map.md`:

```markdown
# Dependency Map

| Context | Depends on | Depended on by | Coupling |
|---------|-----------|----------------|----------|
| auth    | shared-kernel | billing, catalog | low |
| billing | auth, shared-kernel | — | medium |

## External services
- <name> — <purpose> — <where configured>

## Data stores
- <type> — <contexts using it>
```

### Step 5 — Risk register (cost: ~500 tokens)

Produce `docs/context/02-risk-register.md`:

```markdown
# Risk Register

| ID | Context | Risk | Evidence | Severity |
|----|---------|------|----------|----------|
| R1 | legacy/ | No tests detected | no `__tests__`, no `.test.*` | high |
| R2 | billing | Circular coupling with auth | cross imports | medium |
| R3 | shared-kernel | God object | >15 exports | medium |

## Uncovered zones
- <paths>

## Change hotspots (if git available)
- <files with highest churn>
```

### Step 6 — Overview & handoff (cost: ~300 tokens)

Produce `docs/context/00-overview.md`:

```markdown
# Repository Overview

## Stack
- Language(s): ...
- Frameworks: ...
- Package manager: ...

## Bounded contexts
1. `<name>` — `<path>` — <one-sentence purpose>

## Global commands
- Install: ...
- Test: ...
- Lint: ...

## Phase 0 status
- [x] Reconnaissance
- [x] Contexts detected (N)
- [x] Per-context documentation
- [x] Dependency map
- [x] Risk register

## Handoff → Phase 1
Mandatory reading for `01-sdd-spec-writer`:
- `docs/context/00-overview.md`
- `docs/context/01-dependency-map.md`
- `docs/context/<user-story-context>.md`
```

## Quality gates (self-check before closing)

| Gate | Criterion | Blocking |
|------|-----------|----------|
| G1 | `00-overview.md` exists with stack + commands | yes |
| G2 | Every detected context has its `<name>.md` | yes |
| G3 | `01-dependency-map.md` covers 100% of contexts | yes |
| G4 | `02-risk-register.md` has ≥1 row or a justified "no risks observed" | yes |
| G5 | No file outside `docs/context/` was modified | yes |
| G6 | Tokens used ≤ 8000 (if exceeded, split by `optional_scope`) | no (warning) |

If a blocking gate fails → do not mark the phase complete; report the failure.

## Anti-patterns (forbidden)

❌ Dumping the whole repo into context.

❌ Documenting file-by-file (it's a map, not an index).

❌ Emitting value judgments ("this code smells").

❌ Proposing refactors or tickets.

❌ Running the test suite.

❌ Assuming a stack without manifest evidence.

## Invocation example

User (in OpenCode):

```
Run .meta-skills/00-context-bootstrap.md on this repo.
Scope: src/billing/ only.
```

Agent: runs steps 1–6 with `optional_scope = src/billing/` and produces
only `docs/context/billing.md` + the corresponding entries in overview,
dependency-map, and risk-register.

## Handoff to Phase 1

On close, the AI must emit a 3-line message:

```
✅ Phase 0 complete. Contexts: <N>. Risks: <M>.
📁 Artifacts: docs/context/ (N+3 files).
➡️  Next: 01-sdd-spec-writer. Requires a User Story from the Navigator.
```

## Design notes (for contributors)

- Why no YAML config? Because portability is the point. Frontmatter YAML is
  optional; an LLM can follow the protocol even if its harness ignores it.

- Why token estimates? So the Navigator can decide whether to run in one
  pass or by `optional_scope`.

- Why DDD-lite and not full DDD? Full DDD needs domain events, aggregates,
  etc. Here we only need boundaries.
