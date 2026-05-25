---
name: agent-flow
description: "Use only when the user explicitly invokes Agent Flow at the start of the request, for example `Agent Flow ...`, `$agent-flow ...`, `agent-flow ...`, or `агент-флоу ...`. Route that request from idea to verified result with traceable artifacts, subagent delegation, design gates, and Definition of Done checks as needed."
---

# Agent Flow

Agent Flow turns an explicitly prefixed user request into a finished, verified result through the smallest sufficient agent workflow.

## Invocation Model

Agent Flow has one public invocation:

- `Agent Flow <task>`
- `$agent-flow <task>`
- `agent-flow <task>`
- `агент-флоу <task>`

Text forms without `$` are case-insensitive.

Only use this skill when the invocation appears at the start of the user request. A later mention of Agent Flow is not enough.

Requests without the Agent Flow prefix are outside this skill. They run solo in the main agent, without Agent Flow artifacts and without subagents.

Standing authorization: an Agent Flow-prefixed request is an explicit user request to use subagents for product/code/docs/design implementation when delegation is useful and available. This satisfies subagent tool policies that require explicit user intent for delegation.

Never expose extra modes such as `/solo`, `/lite`, `orchestrated`, autopilot, parallel-review, or review-mode as public user-facing modes. Treat detailed workflow choice as internal routing inside Agent Flow.

## Boundary

Default solo work, without the Agent Flow prefix:

- Do not use this skill.
- Do not spawn subagents.
- Do not create Agent Flow run directories.
- If the task becomes too broad or risky for solo execution, tell the user to invoke Agent Flow explicitly.

Agent Flow-prefixed work:

- Use this skill.
- Do not use a separate brainstorming flow or `brainstorming` skill. Uncertainty is handled inside Agent Flow intake, route, planning, delegation, and verification.
- Spawn subagents for product/code/docs/design implementation when `spawn_agent` is available.
- Orchestrator owns route, plan, delegation, review, verification, and final answer.
- Product implementation writes belong to workers.

## Orchestrator Mandate

Inside Agent Flow, the orchestrator owns the outcome:

1. Classify the request.
2. Pick the internal flow.
3. Decide whether trace artifacts are needed.
4. Choose skills, plugins, tools, and subagents.
5. Set approval gates only where they reduce real risk.
6. Keep scope bounded.
7. Delegate product/code/docs/design implementation to worker subagents when `spawn_agent` is available.
8. Verify evidence before any completion claim.
9. Return the final answer with residual risks.

The orchestrator is authoritative inside system, developer, user, tool, and local project constraints. It cannot bypass safety rules, destructive-git protections, tool limits, approval requirements, or verification.

## Subagent Tool Discovery

Before declaring `spawn_agent` unavailable in Agent Flow, the orchestrator must try to discover it:

1. Check active tools for a subagent/spawn tool.
2. If not visible and `tool_search` is available, call `tool_search` with a narrow query such as `spawn_agent subagent multi-agent tools`.
3. If `spawn_agent` appears after discovery, use it.
4. Only if discovery fails or the tool remains unavailable, return `blocked-for-subagents` for product edits unless the user explicitly permits manual fallback.

## Product Edit Boundary

Inside Agent Flow, the orchestrator does not directly edit product implementation files when a worker subagent can own the change.

The orchestrator may:

- read code and docs;
- create route, plan, trace, and handoff artifacts;
- run discovery and verification commands;
- spawn and brief workers;
- review diffs and integrate handoffs;
- make AgentFlow/process-documentation edits when the task itself is about process.

Worker subagents own product code, frontend files, backend files, tests, user-facing docs, and design artifact implementation. For TypeScript/JavaScript changes, use a `typescript-worker`, `frontend-worker`, `backend-worker`, or another narrow worker depending on ownership.

If a product edit is required but `spawn_agent` is unavailable, stop with `blocked-for-subagents` unless the user explicitly permits manual fallback in that task. Do not silently implement product code as the orchestrator.

## Core Decision Tree

1. If the task starts with `Agent Flow`, `$agent-flow`, `agent-flow`, or `агент-флоу`, strip the prefix and use this skill.
2. If the task does not start with an Agent Flow prefix, do not use this skill.
3. Inside Agent Flow, do not call `brainstorming`; classify the request and choose the smallest internal flow directly.
4. If the task is trivial, answer or run the command directly within Agent Flow.
5. If task has multiple steps, regression risk, docs/design/code changes, CI/deploy, external services, or user-facing output, create a traceable run.
6. If product/code/docs/design implementation is needed, discover `spawn_agent` if needed, then spawn worker subagents with narrow ownership.
7. If `spawn_agent` remains unavailable after discovery and implementation is needed, return `blocked-for-subagents` unless manual fallback is explicitly approved.
8. If no product edit is needed, perform orchestration, research, review, or answer directly.

## Internal Flows

Read `references/flows.md` when a task is non-trivial or when flow choice is unclear.

Common internal flows:

- `quick-check-flow`
- `bugfix-flow`
- `feature-flow`
- `docs-flow`
- `design-flow`
- `ci-release-flow`
- `review-flow`
- `initiative-flow`

`initiative-flow` is the full-cycle path for a small idea that must become a complete result: discovery, PRD or scope, architecture, design if needed, plan, implementation, QA, review, docs, artifacts, final handoff.

## Trace Gate

Read `references/traceable-runs.md` when the task needs durable evidence or has more than a quick one-step answer.

Create `.agent-work/runs/YYYY-MM-DD-task-slug/` for tasks with:

- multi-step implementation or investigation;
- project docs, PRD, design docs, or public writing;
- UI/design work;
- bugfixes with regression risk;
- CI, deploy, release, auth, payments, or external services;
- subagent delegation;
- high-stakes or hard-to-reproduce verification.

Do not create run directories for short consultation, one-off shell checks, or tiny one-file edits unless risk grows.

## Delegation Gate

Read `references/delegation.md` before launching subagents.

Inside Agent Flow, delegation is mandatory for product/code/docs/design implementation when `spawn_agent` is available. Delegate only when:

- workstream is independent;
- expected value exceeds coordination cost;
- ownership can be stated clearly;
- verification can be done by the orchestrator;
- subagent tool is available in the current environment.

For each subagent, provide a self-contained delegation packet and require a handoff file when a run directory exists.

## Done Gate

Read `references/definition-of-done.md` before final response on traceable work.

No completion claim without fresh evidence. Verification can be tests, build, lint, browser screenshots, visual diff, QA notes, docs review, or a checklist tied to acceptance criteria.

## Design Gate

Read `references/design-flow.md` for UI, UX, Pencil, Figma, screenshots, visual assets, frontend implementation, or design-system work.

Non-trivial frontend/UI implementation needs an approved design source before code starts. If no design source exists, route through design first.

## AI Slop Gate

Read `references/ai-slop-gate.md` for user-facing text, UI/design, generated code, docs, tests, and public artifacts. Use the local `ai-slops-hunter` subagent description when subagents are available; otherwise simulate the same checklist. Keep fixes minimal and within scope.

## Scripts

Optional helper scripts live in `scripts/`:

- `init-run.py`: create a traceable run skeleton.
- `append-timeline.py`: append one JSONL timeline event.
- `record-agent-trace.py`: append one subagent event to both run and role traces.
- `validate-run.py`: check run completeness before final handoff.

Scripts support the workflow; they do not replace engineering judgment.

## References

- `references/flows.md`: flow catalog and routing.
- `references/orchestrator.md`: orchestrator responsibilities and mode handling.
- `references/traceable-runs.md`: run directory structure and artifact rules.
- `references/delegation.md`: delegation packets, role handoffs, stable identities.
- `references/definition-of-done.md`: required verification gates.
- `references/design-flow.md`: UI/design-specific route and gates.
- `references/ai-slop-gate.md`: AI slop review route, subagent, and related skills.
- `references/automation-patterns.md`: manual-to-automation promotion pattern.
