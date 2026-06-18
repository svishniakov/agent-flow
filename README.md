# AgentFlow

[Russian version](README.ru.md)

AgentFlow is a local orchestration and verification framework for Codex. It
turns an explicitly marked prompt into a controlled engineering run with project
memory, scoped lanes, architecture gates, trace validation, mandatory
independent QA where required, and a local CodeGraph fact layer.

Use it when a coding agent must do more than make a small edit. AgentFlow is
for work that needs bounded scope, evidence, reviewable handoffs, and a clear
answer to: what changed, why is it safe, and how was it checked?

AgentFlow is built for Codex with OpenAI models. Claude Code, Cursor, Hermes,
and other hosts are outside this package scope.

## Inspired by

AgentFlow is not a wrapper around these projects, and none of them are runtime
dependencies. They were used as research and design references for the shape of
AgentFlow: local state, evidence-backed gates, reviewable handoffs, CodeGraph,
and controlled learning from verified runs.

GitHub repositories:

- [kayba-ai/agentic-context-engine](https://github.com/kayba-ai/agentic-context-engine)
  for structured local learning, skillbook-style entries, provenance, and
  counter-based reflection.
- [hexo-ai/sia](https://github.com/hexo-ai/sia) for self-improvement loops that
  separate harness changes from model changes.
- [Ancienttwo/repo-harness](https://github.com/Ancienttwo/repo-harness) for
  repo-local workflow state, contracts, checks, review evidence, and handoffs.
- [DanMcInerney/architect-loop](https://github.com/DanMcInerney/architect-loop)
  for architect/builder separation, source-backed design, and boundary evidence.
- [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) for
  the Engineering Simplicity Gate: prefer native, minimal, existing code before
  adding abstractions or dependencies.
- [unclebob/swarm-forge](https://github.com/unclebob/swarm-forge) for simple
  multi-agent coordination, durable handoffs, and work queue discipline.
- [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)
  for code intelligence and knowledge-graph tradeoffs that informed local
  CodeGraph boundaries.

arXiv papers:

- [Evolving Contexts for Self-Improving Language Models](https://arxiv.org/abs/2510.04618)
  for Agentic Context Engineering and structured context evolution.
- [SIA: Self Improving AI with Harness & Weight Updates](https://arxiv.org/abs/2605.27276)
  for separating harness learning from weight-update learning.
- [Self-Harness: Harnesses That Improve Themselves](https://arxiv.org/abs/2606.09498)
  for the idea that the harness itself can be improved through evaluation
  evidence, with guardrails.

## Why It Exists

Coding agents fail in predictable ways:

- they drift outside the requested scope;
- they make architecture claims that are not backed by files, tests, or traces;
- they call role-lane work a subagent run when no subagent trace exists;
- they close implementation work without independent QA;
- they mark risky work as `pass-with-risks` without resolving the risk;
- they repeat local mistakes because previous lessons were not structured.

AgentFlow turns those failure modes into gates. The user does not choose a
workflow depth manually. The orchestrator reads the task, checks project state,
chooses the smallest useful route, and requires evidence before a positive
final answer.

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

## Core Runtime

AgentFlow has seven main layers.

### 1. Orchestration

The orchestrator owns routing, sequencing, lane coordination, and final
integration. It decides whether a task stays solo, needs trace artifacts, or
should split into role lanes or real subagents. `light` keeps work small.
`standard` and `release` may use architecture, implementation, QA, review, and
integration lanes when the extra control is worth the cost.

### 2. Project Memory

AgentFlow reads the current project's task memory before feature work. The
Dependency Gate blocks or pauses a new run when another active task may touch
the same files, API, data model, UI flow, tests, deploy path, or acceptance
criteria.

Task Status Completion Gate keeps memory honest: completed work must move from
`in_progress` to `done` after verification or commit evidence exists.

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

These gates are not checklist text. `scripts/validate-run.py` blocks positive
final verdicts when required artifacts are missing, stale, out of order, or not
backed by evidence.

### 4. Mandatory Independent QA

Implementation and change runs that touch product files, tests, runtime docs,
validator behavior, templates, golden traces, ADR/spec/plan status, or commits
must run a real `reviewer.qa` subagent before a positive final answer.

Role-lane review does not satisfy this gate. The run must provide:

- `delegation-summary.json` with a reviewer subagent record;
- an agent trace with a spawned event and `codex_thread_id`;
- a terminal reviewer handoff;
- a final `Mandatory Independent QA Review` evidence section.

If the subagent tool cannot launch or the reviewer runtime fails, the run closes
as `blocked`. AgentFlow must not fall back to solo review for mandatory QA.

### 5. Trace Validation

Traceable work stores durable evidence under a local run directory. The central
file is `lane-map.json`; the validator checks lane ownership, handoffs,
artifact paths, timeline events, subagent traces, architecture controls,
handoff state, acceptance traceability, contract negative fixtures, and final
verdict rules. Opt-in Handoff State Gate uses `handoff_state_required` plus
`scripts/record-handoff-state.py` for `queued`, `accepted`, and `completed`
lane lifecycle state.

Golden Trace Runs in `testdata/golden-traces/` are the runtime acceptance pack.
They include both valid and intentionally invalid runs, so changes to the gates
are tested against persisted traces instead of isolated unit cases only.

### 6. CodeGraph

CodeGraph is a local fact layer for AgentFlow. It indexes the current working
tree into SQLite and answers dependency, impact, test, context, boundary, and
task-overlap questions through JSON-only CLI commands.

Public command:

```bash
python3 scripts/codegraph.py index
python3 scripts/codegraph.py status
python3 scripts/codegraph.py impact --target scripts/validate-run.py
python3 scripts/codegraph.py tests --target scripts/codegraph.py
python3 scripts/codegraph.py context --target CodeGraphError
python3 scripts/codegraph.py boundary --path scripts/codegraph.py --allowed 'scripts/**'
python3 scripts/codegraph.py deps --active-task 'edit validator' --new-task 'edit codegraph'
python3 scripts/codegraph.py doctor
```

Storage and config:

```text
.agent-work/codegraph/codegraph.sqlite
.agent-work/codegraph/config.json
```

CodeGraph v1 indexes tracked, dirty, and relevant untracked files while
respecting git ignore rules. It supports Python plus TypeScript/JavaScript with
Tree-sitter adapters and Python `ast` enrichment.

Boundary checks are graph-backed: `boundary` now fails when either direct
changed paths or graph-derived `affected_surface_violations` leave allowed
patterns or match forbidden patterns. CodeGraph is support evidence, not the
sole authority for release or security decisions.

### 7. Local Learning

AgentFlow learns locally, not globally. Harness Evaluation Loop writes
`harness-evaluation.json` when a run produces useful learning evidence:
continuation, blocked recovery, risk resolution, architecture drift, readiness
recovery, or a non-positive architecture final.

Validated findings can be promoted only into the current project's
`## Evidence Records`. Architecture Matrix, capability registry, role prompts,
validator guards, and Golden Trace Runs remain canonical runtime artifacts.

Promoted records use an ACE-inspired shape: `Section`, `Keywords`,
`Provenance`, `Helpful`, `Harmful`, `Neutral`, and `Active`. These fields help
the analyzer separate task context lessons from harness/runtime lessons, count
helpful or harmful reuse, and keep inactive evidence out of promotion decisions.
They do not add an `ace-framework` dependency, MCP server, vector store, or
separate skillbook. `Outcome`, `Evidence`, reuse boundaries, and fresh
verification remain the gate authority.

## What Ships In This Repository

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Codex entrypoint and runtime contract |
| `agents/*.md` | bundled role prompts |
| `agents/agent-identities.json` | stable role identities for traces and handoffs |
| `references/architecture-matrix.md` | reusable architecture facets |
| `references/architecture-capability-router.md` | capability routing and Soft Skill Binding |
| `references/architecture-artifact-authoring.md` | generated architecture artifact contract |
| `references/traceable-runs.md` | run directory structure and validator contract |
| `references/harness-evaluation-loop.md` | local learning contract |
| `references/definition-of-done.md` | completion gates |
| `references/delegation.md` | subagent and role-lane delegation rules |
| `references/role-catalog.md` | role lifecycle and boundaries |
| `requirements-codegraph.txt` | pinned CodeGraph parser dependencies |
| `scripts/codegraph.py` | local CodeGraph CLI and SQLite indexer |
| `scripts/test-codegraph.py` | CodeGraph fixture and contract tests |
| `scripts/check-all.py` | repository validation suite |
| `scripts/validate-run.py` | trace and lane-map validator |
| `scripts/init-run.py` | trace skeleton generator |
| `scripts/record-handoff-state.py` | Handoff State Gate state recorder |
| `scripts/record-lane-boundary.py` | worker changed-path boundary recorder |
| `scripts/promote-harness-evaluation.py` | promotion from Harness Evaluation into Evidence Records |
| `scripts/analyze-evidence-records.py` | local learning analyzer |
| `scripts/test-golden-traces.py` | Golden Trace Runs acceptance runner |
| `testdata/codegraph/` | CodeGraph fixture notes |
| `testdata/golden-traces/` | full valid and invalid trace fixtures |

The repository currently ships 27 roles and tracks 138 role skill dependencies.

## Install

```bash
git clone https://github.com/svishniakov/agent-flow.git ~/.codex/skills/agent-flow
python3 ~/.codex/skills/agent-flow/scripts/check-agent-deps.py --post-install
```

`--post-install` reports missing role dependencies and recommends the `core`
set. It does not install anything silently.

Install CodeGraph parser dependencies when you need CodeGraph locally:

```bash
python3 -m pip install -r ~/.codex/skills/agent-flow/requirements-codegraph.txt
python3 ~/.codex/skills/agent-flow/scripts/codegraph.py doctor
```

## Update

```bash
python3 ~/.codex/skills/agent-flow/scripts/update-agent-flow-skill.py --dry-run
python3 ~/.codex/skills/agent-flow/scripts/update-agent-flow-skill.py
```

The updater fetches `origin`, reports local state, and fast-forwards only a
clean checkout. Use `--overwrite` only when local edits or divergent commits
should be discarded deliberately.

## Local Checks

Run checks from repository root:

```bash
python3 scripts/check-all.py
python3 scripts/check-agent-deps.py --strict
python3 scripts/validate-architecture-capabilities.py
python3 scripts/codegraph.py doctor
python3 scripts/test-codegraph.py
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

Use CodeGraph as support evidence:

```text
Agent Flow Before changing <area>, run CodeGraph status, impact, tests, boundary, and deps checks. Use graph output as support evidence, then verify with normal tests.
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
- [CodeGraph ADR](docs/adr/adr-001-codegraph.md)
- [CodeGraph v1 Implementation Plan](docs/implementation/impl-001-codegraph-v1.md)
- [English overview](docs/en/agent-flow.md)
- [Russian overview](docs/ru/agent-flow.md)
- [License](LICENSE)
