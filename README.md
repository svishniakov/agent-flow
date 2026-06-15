# AgentFlow

[Russian version](README.ru.md)

AgentFlow is a local agent orchestration and verification framework for Codex.
It turns one explicitly marked prompt into a controlled engineering workflow:
project memory, scoped role lanes, architecture gates, trace validation, and
local learning from real runs.

Use it when a coding agent must do more than make an edit. AgentFlow is for
work that needs a bounded scope, evidence, reviewable handoffs, and a clear
answer to the question: "What changed, why is it safe, and how was it checked?"

AgentFlow is built for Codex with OpenAI models. Claude Code, Cursor, Hermes,
and other hosts are outside this package scope.

## Why It Exists

Coding agents fail in predictable ways:

- they drift outside the requested scope;
- they make architecture claims that are not backed by files or tests;
- they call role-lane work a subagent run when no subagent trace exists;
- they leave risky work as `pass-with-risks` without resolving the risks;
- they repeat a local mistake because the previous lesson was not structured.

AgentFlow gives each of those failure modes a gate. The framework does not ask
the user to pick a workflow depth. The orchestrator reads the task, checks the
project state, chooses the smallest useful route, and requires evidence before
the final answer.

## Invocation

AgentFlow runs only when the latest user request contains one of these markers:

```text
Agent Flow <task>
AgentFlow <task>
$agent-flow <task>
agent-flow <task>
```

The marker can appear at the beginning, middle, or end of the prompt. Requests
without a marker stay outside AgentFlow and run as ordinary solo Codex work.
Project `AGENTS.md` files cannot force AgentFlow on.

## What It Controls

AgentFlow has five main layers.

### 1. Orchestration

The orchestrator owns routing, sequencing, and final integration. It decides
whether the task stays solo, needs trace artifacts, or should split into role
lanes or real subagents. `light` work stays small. `standard` and `release`
work may use architecture, implementation, QA, review, and integration lanes
when the extra control is worth the cost.

### 2. Project Memory

AgentFlow reads the current project's task memory before starting new feature
work. The Dependency Gate blocks or pauses a new run when another active task
may touch the same files, API, data model, UI flow, tests, deploy path, or
acceptance criteria.

Task Status Completion Gate keeps that memory honest: completed work must move
from `in_progress` to `done` after verification or commit evidence exists.

### 3. Architecture Control

For architecture-sensitive work, AgentFlow requires an Architecture Contract
before workers start. Schema v2 trace runs can include:

- Architecture Matrix facets for product, surface, stack, risk, and
  verification context;
- Architecture Capability Router selections from
  `registries/architecture-capabilities.json`;
- Architecture Design Mode and an approved Architecture Design Brief;
- Architecture Artifact Authoring Automation through
  `init-run.py --architecture-gate`;
- Architecture Context Propagation from architect to workers, QA, and reviewer;
- Architecture Execution Control, including Engineering Simplicity Gate,
  Simplicity Scope Coverage, Lane Boundary Evidence Gate, and Claim Evidence
  Gate.

These gates are not decorative checklist text. `scripts/validate-run.py` blocks
positive final verdicts when required artifacts are missing, stale, out of
order, or unsupported by evidence.

### 4. Trace Validation

Traceable work stores durable evidence under a local run directory. The central
file is `lane-map.json`; the validator checks lane ownership, handoffs, artifact
paths, timeline events, subagent traces, architecture controls, and final
verdict rules.

Golden Trace Runs in `testdata/golden-traces/` are the acceptance pack for this
runtime. They include both valid and intentionally invalid runs, so changes to
the architecture layer are tested against full persisted traces.

### 5. Local Learning

AgentFlow learns locally, not globally. Harness Evaluation Loop writes
`harness-evaluation.json` when a run produces useful learning evidence:
continuation, blocked recovery, risk resolution, architecture drift, readiness
recovery, or a non-positive architecture final.

Validated findings can be promoted only into the current project's
`## Evidence Records`. Architecture Matrix, capability registry, role prompts,
validator guards, and Golden Trace Runs remain canonical runtime artifacts and
are not promotion targets for project traces.

Local Best Practice auto gate can reuse a learned approach only when the
Evidence Records analyzer confirms the pattern, the context matches, reuse
boundaries are present, no matching "do not reuse" condition applies, and fresh
verification passes.

## What Ships In This Repository

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Codex entrypoint and runtime contract |
| `agents/*.md` | 27 bundled role prompts |
| `agents/agent-identities.json` | stable role identities for traces and handoffs |
| `references/architecture-matrix.md` | reusable architecture facets |
| `references/architecture-capability-router.md` | capability routing and Soft Skill Binding |
| `references/architecture-artifact-authoring.md` | generated architecture artifact contract |
| `references/traceable-runs.md` | run directory structure and validator contract |
| `references/harness-evaluation-loop.md` | local learning contract |
| `references/definition-of-done.md` | completion gates |
| `references/role-catalog.md` | role lifecycle and boundaries |
| `registries/agent-skills.json` | role dependency metadata |
| `registries/architecture-capabilities.json` | capability registry |
| `scripts/check-all.py` | repository validation suite |
| `scripts/validate-run.py` | trace and lane-map validator |
| `scripts/init-run.py` | trace skeleton generator |
| `scripts/record-lane-boundary.py` | worker changed-path boundary recorder |
| `scripts/promote-harness-evaluation.py` | promotion from Harness Evaluation into Evidence Records |
| `scripts/analyze-evidence-records.py` | local learning analyzer |
| `scripts/test-golden-traces.py` | Golden Trace Runs acceptance runner |
| `testdata/golden-traces/` | full valid and invalid trace fixtures |

The repository currently ships 27 roles and tracks 138 role skill dependencies.

## Install

```bash
git clone https://github.com/svishniakov/agent-flow.git ~/.codex/skills/agent-flow
python3 ~/.codex/skills/agent-flow/scripts/check-agent-deps.py --post-install
```

`--post-install` reports missing role dependencies and recommends the `core`
set. It does not install anything silently.

## Update

```bash
python3 ~/.codex/skills/agent-flow/scripts/update-agent-flow-skill.py --dry-run
python3 ~/.codex/skills/agent-flow/scripts/update-agent-flow-skill.py
```

The updater fetches `origin`, reports local state, and fast-forwards only a
clean checkout. Use `--overwrite` only when local edits or divergent commits
should be discarded deliberately.

## Local Checks

AgentFlow is developed and validated locally. Run checks from the repository
root:

```bash
python3 scripts/check-all.py
python3 scripts/check-agent-deps.py --strict
python3 scripts/validate-architecture-capabilities.py
```

Expected final line from `check-all.py`:

```text
PASS all Agent Flow checks
```

## Example Prompts

Read a repository without changing it:

```text
Agent Flow Read the repository and project memory. Return active work, blocked items, next actions, and risks. Do not change anything.
```

Fix a bug with verification:

```text
Agent Flow Investigate this bug: <description>. Find the cause, make the smallest fix, run checks, and return changed files plus residual risks.
```

Run architecture-sensitive work:

```text
Agent Flow Implement <feature>. Use architecture gates where needed, keep worker changes inside approved boundaries, verify the result, and report evidence.
```

Refactor from architecture analysis:

```text
Agent Flow Analyze this project for architecture drift before refactoring. Map the current module boundaries, data flow, public contracts, and ownership hotspots. If no refactor is justified, say so. Otherwise propose the smallest behavior-preserving refactor, implement only that scope, and run the relevant checks.
```

Remove overengineering with Simplicity Gate:

```text
Agent Flow Review this codebase for overengineering using Engineering Simplicity Gate and Simplicity Scope Coverage. Look for unnecessary abstractions, duplicated helpers, dependency drift, broad changes, or code that solves problems we do not have. Remove only evidence-backed issues, do not introduce new frameworks, preserve behavior, and verify the cleanup.
```

Prepare a release review:

```text
Agent Flow Finish this feature for release. Run architecture, QA, and review gates, then return ship/pass-with-risks/blocked status with evidence.
```

## Reference Docs

- [Russian README](README.ru.md)
- [Architecture Matrix](references/architecture-matrix.md)
- [Architecture Capability Router](references/architecture-capability-router.md)
- [Traceable Runs](references/traceable-runs.md)
- [Harness Evaluation Loop](references/harness-evaluation-loop.md)
- [Definition of Done](references/definition-of-done.md)
- [Subagent Policy](references/subagents.md)
- [Delegation Rules](references/delegation.md)
- [Role Catalog](references/role-catalog.md)
- [English overview](docs/en/agent-flow.md)
- [Russian overview](docs/ru/agent-flow.md)
- [License](LICENSE)
