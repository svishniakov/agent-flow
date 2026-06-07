# Agent Flow: skill overview

Agent Flow is a Codex skill that helps move a complex task from the user request to a verified result. It does not activate automatically. The user must start the request with one of these prefixes: `Agent Flow`, `$agent-flow`, or `agent-flow`.

The main idea is simple: choose the shortest useful workflow, keep the task bounded, gather the needed context, do the work, and verify the result before the final response. By default, Agent Flow runs solo: one main agent owns the outcome. Subagents are used only when the user separately asks for subagents, spawn, multi-agent, or delegation, and the current environment has a subagent/spawn tool.

## Purpose

Agent Flow is useful when a task is larger than one short answer or one mechanical edit. It helps to:

- parse the request and choose the right flow;
- avoid extra process when direct work is enough;
- read project memory and local rules before changes;
- avoid touching infrastructure without need;
- separate ordinary solo work from tasks with trace artifacts;
- prepare a delegation packet for subagents when the user explicitly allows them;
- keep evidence: checks, handoffs, timeline, risks, and final status;
- avoid claiming the work is done without fresh verification.

## Activation

The skill activates only at the start of the user message:

- `Agent Flow <task>`
- `$agent-flow <task>`
- `agent-flow <task>`

If the prefix is absent, the request stays outside Agent Flow. Codex then works in normal solo mode, without trace artifacts and without automatic routing through this skill.

## Core rules

- Agent Flow is not a preflight for every request.
- A project `AGENTS.md` cannot force Agent Flow on.
- Do not run a separate brainstorming flow before Agent Flow.
- Subagents are not launched by default.
- If a task is complex and subagents would help, Agent Flow must ask for explicit permission instead of launching them by itself.
- Trace artifacts are created only when justified by risk, budget, or a direct user request.
- `.agent-work/` must not be included in product commits.

## Internal flows

Agent Flow chooses the smallest suitable flow:

- `quick-check-flow` - one short answer, command, or check;
- `bugfix-flow` - reproduction, fix, regression check;
- `feature-flow` - bounded implementation of a new capability;
- `docs-flow` - documentation, PRD, README, specification;
- `design-flow` - UI/UX, design documents, Pencil/Figma/Stitch;
- `ci-release-flow` - CI, release, deploy, external services;
- `review-flow` - review of code, plan, or readiness;
- `initiative-flow` - a small path from idea to finished result.

## Traceable runs

For high-risk work, release gates, CI/deploy, external services, or a direct user request, Agent Flow can create a run directory in `.agent-work/runs/YYYY-MM-DD-task-slug/`.

It usually contains:

- `manifest.md`;
- `context.md`;
- `route.md`;
- `plan.md`;
- `checks/`;
- `handoffs/`;
- `artifacts/`;
- `timeline.jsonl`;
- `final.md`.

The timeline records the real order of work. If a product commit was created, the commit event is recorded after successful checks and before the final event. Trace artifacts remain local memory and are not added to the product commit unless the user explicitly asks for that.

## Lane Sharding

For large PRDs with explicitly authorized subagents, Agent Flow can split work into implementation, integration, QA, and review lanes. This is an internal workflow pattern, not a public mode and not a bypass around explicit subagent authorization.

In a traceable run, `lane-map.json` becomes the machine-readable source of truth. Markdown files such as `checks/coverage-matrix.md` remain human-readable summaries. Before final handoff, `validate-run.py` checks `lane-map.json` and rejects `Verdict: ship` when a critical lane has no evidence or valid replacement lane.

## Subagents

The repository contains bundled role files in `agents/<role>.md` and stable identities in `agents/agent-identities.json`. These files let others clone the repository from GitHub and use Agent Flow without author-local paths.

Subagents are used only when both conditions are true:

- the user explicitly asked for subagents, spawn, multi-agent, or delegation;
- the Codex environment provides a subagent/spawn tool.

If the tool is unavailable, role files can be used as a solo checklist or role lane, but that is not subagent execution.

Subagent dependencies are described in `registries/agent-skills.json`. Role files in `agents/*.md` state which skills a role needs; the registry stores tiers, roles, target paths, prompts, and install instructions.

After installing Agent Flow, run the post-install wizard:

```bash
python3 ~/.codex/skills/agent-flow/scripts/check-agent-deps.py --post-install
```

The wizard reports missing `core` and `full` skills and recommends starting with core:

- `core` means skills without which a role loses its main capability: browser, QA, test, find-skills, language/runtime/toolchain, and key design/plugin skills;
- `full` means every skill referenced by `agents/*.md`, including niche, paid, or plugin-gated dependencies.

Target selects where the skill should be installed:

- `global` means the user's global environment, usually `~/.codex/skills`;
- `project` means the current project, usually `.codex/skills`.

There is no silent install. `--guided-install` executes only allowlisted `git`/`local` registry commands and only after the user confirms with `yes`. `plugin`, `prompt`, and `manual` entries are not executed: the checker prints instructions, the official prompt, or a note to enable a plugin. After guided install, the checker reruns the scan and reports remaining missing skills.

## Checks

After changing the skill, these checks are useful:

```bash
python3 -m py_compile scripts/*.py
python3 scripts/validate-agent-skill-registry.py
python3 scripts/check-agent-deps.py --scope core
python3 scripts/init-run.py --help
python3 scripts/append-timeline.py --help
python3 scripts/record-agent-trace.py --help
python3 scripts/validate-run.py --help
python3 scripts/test-validate-run-lanes.py
```

To check runtime files for Russian text:

```bash
rg -n '\p{Cyrillic}' SKILL.md agents references scripts LICENSE
```

Russian text is allowed only in `README.ru.md` and `docs/ru/`.
