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

## Subagents

The repository contains bundled role files in `agents/<role>.md` and stable identities in `agents/agent-identities.json`. These files let others clone the repository from GitHub and use Agent Flow without author-local paths.

Subagents are used only when both conditions are true:

- the user explicitly asked for subagents, spawn, multi-agent, or delegation;
- the Codex environment provides a subagent/spawn tool.

If the tool is unavailable, role files can be used as a solo checklist or role lane, but that is not subagent execution.

## Checks

After changing the skill, these checks are useful:

```bash
python3 -m py_compile scripts/*.py
python3 scripts/init-run.py --help
python3 scripts/append-timeline.py --help
python3 scripts/record-agent-trace.py --help
python3 scripts/validate-run.py --help
```

To check runtime files for Russian text:

```bash
rg -n '\p{Cyrillic}' SKILL.md agents references scripts LICENSE
```

Russian text is allowed only in `README.md` and `docs/ru/`.
