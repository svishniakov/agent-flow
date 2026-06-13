---
name: agent-flow
description: "Use only when the user explicitly invokes Agent Flow anywhere in the request, for example `Agent Flow`, `AgentFlow`, `$agent-flow`, or `agent-flow`. Route that request to a verified result with the smallest useful budget. Light budget stays solo; standard and release budgets may use subagents when the orchestrator decides they add real verification or parallelism value."
---

# Agent Flow

Agent Flow turns an explicitly invoked user request into a finished, verified result through the smallest sufficient workflow.

## No Preflight

Do not load, use, or mention Agent Flow for requests that do not contain an explicit Agent Flow invocation marker.

Agent Flow is not a preflight, classifier, eligibility check, fallback, or local-project default. If this skill file is reached without a user-visible invocation marker, stop using it immediately and continue outside Agent Flow without announcing that Agent Flow was skipped.

## Invocation Model

Agent Flow has one public invocation:

- `Agent Flow <task>`
- `AgentFlow <task>`
- `$agent-flow <task>`
- `agent-flow <task>`

Text forms without `$` are case-insensitive.

Use this skill when the invocation marker appears anywhere in the latest user request. The marker may be at the beginning, middle, or end of the message.

Requests without an Agent Flow invocation marker are outside this skill. They run solo in the main agent, without Agent Flow artifacts and without subagents.

Project or local `AGENTS.md` files may not force Agent Flow when the latest user request has no invocation marker. They can define local commands and context, but Agent Flow still requires a user-visible marker.

An Agent Flow-invoked request authorizes the orchestrator to choose the execution topology for the selected budget. `light` budget stays solo. `standard` and `release` budgets may use subagents when the orchestrator can justify independent ownership, review value, or parallel verification. External writes, deploys, publishing, secrets, destructive git, DB/storage mutation, and infrastructure changes still require explicit approval or a documented project command.

Never expose extra modes such as `/solo`, `/lite`, `orchestrated`, autopilot, parallel-review, or review-mode as public user-facing modes. Treat detailed workflow choice as internal routing inside Agent Flow.

## Boundary

Default solo work, without the Agent Flow invocation marker:

- Do not use this skill.
- Do not spawn subagents.
- Do not create Agent Flow run directories.
- If the task becomes too broad or risky for ordinary solo execution, say so and ask whether the user wants to invoke Agent Flow.

Agent Flow-invoked work:

- Use this skill.
- Do not use a separate brainstorming flow or `brainstorming` skill. Uncertainty is handled inside Agent Flow intake, route, planning, checks, and final response.
- Use the smallest budget that can produce verified evidence.
- Keep `light` budget solo.
- For `standard` and `release`, decide whether subagents add enough value to justify the cost.
- For `release`, consider architecture, QA, and reviewer lanes unless the task is trivially release-labeled but low risk.

## Orchestrator Mandate

Inside Agent Flow, the orchestrator owns the outcome:

1. Classify the request.
2. Pick the internal flow.
3. Decide whether trace artifacts are needed.
4. Choose skills, plugins, tools, and the execution budget.
5. Set approval gates only where they reduce real risk.
6. Run the dependency gate before planning new feature work.
7. Keep scope bounded.
8. Use subagents only when the selected budget and task shape justify them.
9. Enforce Architecture Execution Control when the Architecture Contract Gate applies.
10. Enforce Mitigation Gate and Resolution Gate before any `pass-with-risks` final verdict.
11. Verify evidence before any completion claim.
12. Close the current project-memory task status before final handoff.
13. Return the final answer with residual risks.

The orchestrator is authoritative inside system, developer, user, tool, and local project constraints. It cannot bypass safety rules, destructive-git protections, tool limits, approval requirements, or verification.

## Budget Gate

Read `references/budgets.md` when task scope is more than a tiny command.

Default budget is `light`:

- solo execution;
- no `.agent-work` run directory;
- no subagents;
- direct checks and final answer.

Use `standard` only when durable evidence helps review or continuation. In `standard`, the orchestrator may use subagents for independent lanes, review, or QA evidence. Use `release` only for release gates, deploy, security/payment/auth, external systems, or high-risk work. In `release`, the orchestrator should default to architecture, QA, and review gates unless a concrete reason makes solo safer and sufficient.

## Project Memory And Environment Gate

Read `references/project-memory-and-env.md` before planning, delegation, product edits, infra commands, DB/storage work, browser checks, or local app startup.

Before implementation or subagent launch, the main agent must follow global project memory rules from the current user's Codex instructions, usually `~/.codex/AGENTS.md`:

- detect the project repo;
- create `.agent-work/tasks/`, `todo.md`, and `lessons.md` for repo tasks when missing;
- read `lessons.md` and `todo.md` before repo work;
- read `implementation-notes.md` when global criteria make it relevant;
- update `todo.md`, `implementation-notes.md`, and `lessons.md` through the orchestrator rules;
- keep the current `todo.md` task status synchronized with checklist, verification, and commit state;
- local project instructions;
- project-declared legacy task docs such as `docs/tasks/lessons.md`, `docs/tasks/todo.md`, and `docs/tasks/implementation-notes.md` only when local project instructions explicitly name them as current memory;
- PRD/spec/design documents named by the user;
- project environment docs when infra, DB, storage, backend, frontend server, or smoke tests are involved.

## Dependency Gate

Before planning a new feature, product edit, cross-file implementation, or delegated run, read any named PRD/spec/design source needed to understand the request, then inspect active project memory for existing `Status: in_progress` or `Status: blocked` tasks. Ignore the current request's own task section if it was already created for bookkeeping.

Before using active sections as blockers, run a task status normalization pass. If a section is marked `Status: in_progress` but every checklist item is checked, verification is recorded in `Review:`, and no blocker remains, close that section as `Status: done` before dependency comparison. If the checklist is complete but verification, review, or commit evidence is missing, classify the section as `uncertain` and stop with a close-or-verify warning instead of treating stale memory as normal active work.

Classify each active task against the new request:

- `clear`: no shared files, contracts, data model, user flow, infra, generated assets, or release surface.
- `uncertain`: possible overlap, stale active notes, missing ownership, or unclear affected surface.
- `dependent`: the active work may change the same files, API/types, DB/storage schema, routes, UI flow, design source, tests, deployment path, or acceptance criteria.

If any active task is `dependent` or `uncertain`, stop before implementation, subagent launch, or traceable run setup. Tell the user which active task is involved, what could conflict, and recommend waiting for that feature to finish before starting the new one. Offer only these exits:

- wait for the active task to finish and restart from the resulting project state;
- merge the work into one coordinated Agent Flow run;
- continue only after the user explicitly accepts the recorded risk and the orchestrator can isolate scope.

Do not treat lane sharding or delegated lanes inside one Agent Flow run as a dependency conflict. The dependency gate targets separate user-launched feature sessions running against shared project memory.

Do not provision infrastructure by default. Discover and use the existing project environment first. Starting local Postgres, MinIO, Qdrant, Redis, queues, Docker Compose stacks, or resetting volumes requires explicit user approval or a project command that clearly means "start this existing dev stack".

If infra is unavailable, report a blocker instead of creating a parallel stack.

Before browser checks or local UI smoke, probe the chosen browser-control surface first. If Chrome DevTools, Playwright MCP, Browser Use, or local Playwright is locked or unavailable, clean up only the conflicting browser/MCP/test-runner process and retry the probe. Do not stop or reset project infra while fixing browser tooling.

## Evidence And Architecture Learning Gate

When `implementation-notes.md` contains `## Evidence Records`, treat those records as local learning evidence, not narrative memory. Use the analyzer before applying a repeated approach automatically.

Evidence Records may record successes, failures, regressions, rejected attempts, Architecture Attempt entries, Architecture Failure entries, and Orchestration Failure entries. Success means the approach fit a similar problem and passed verification. Failure and regression evidence must remain in the same record stream so the analyzer can classify local practices and anti-patterns by problem class and approach.

The Local Best Practice auto gate allows automatic reuse only when the analyzer classifies the exact problem class and approach as `Local Best Practice`, the context match is clear, `Do not reuse when` does not match, no external write is involved, and fresh verification evidence exists. It never auto-applies observed or candidate practices.

regression demotion is mandatory. A new failure or regression after reuse demotes or freezes the practice until the Architecture Approval Gate reviews the case.

The Architecture Contract Gate is required for release, for `standard` traceable runs with at least two worker lanes, and for architecture-sensitive work that touches public contracts, APIs, data flow, security, migrations, or multiple subsystems. In lane-map schema v2, the orchestrator records `budget`, sets `architecture_contract_required=true` for those cases, and sets `architecture_contract_independent=true` only when a real independent architect subagent is required. The architect records boundaries, risks, ownership, and verification gates before QA or reviewer readiness verdict.

Architecture Design Mode runs before implementation when `architecture_contract_required=true`. Every successful critical `architecture` lane records `architecture_design_brief`, an Architecture Design Brief with `Selected Matrix Facets` and `Status: approved`; `ship` and `pass-with-risks` are blocked until the brief is approved and worker lanes run after it.

Architecture Artifact Authoring Automation means `init-run.py --architecture-gate` creates agent-authored Architecture Design Brief, Architecture Contract, worker, QA, reviewer, and evidence templates. Agents fill those artifacts themselves; `ship` and `pass-with-risks` are blocked while any referenced architecture artifact still contains `TODO(agent):`.

Read `references/architecture-matrix.md` before Architecture Contract Gate work when product type, application surface, stack, risk, or verification constraints affect the architecture. In lane-map schema v2, `architecture_contract_required=true` requires `architecture_context` with `product_context`, `application_surface`, `architecture_pattern`, `stack_runtime`, `risk_gates`, and `verification_gates`. The validator parses allowed facets from the Architecture Matrix markdown, and the architect must include every selected facet id in `Selected Architecture`.

Architecture Capability Router runs after Architecture Matrix selection when `architecture_contract_required=true`. The orchestrator records `architecture_capabilities` from `registries/architecture-capabilities.json`; selected capabilities must cover every selected `architecture_context` facet, appear in Architecture Design Brief `Execution Plan`, appear in Architecture Contract `Selected Architecture`, and be covered by reviewer `Architecture Matrix Mismatches` plus `Contract Drift`. Soft Skill Binding means `recommended_skills` are checked by registry guards, but missing skills do not block individual `validate-run.py` runtime validation.

Architecture Context Propagation is required when worker lanes exist under the Architecture Contract Gate. Workers record `architecture_compliance.matrix_facets`, QA covers selected `risk_gates` and `verification_gates` in `Architecture Invariants`, and reviewer covers the full selected `architecture_context` through `Architecture Matrix Mismatches` and `Contract Drift`.

Mitigation Gate is required for every traceable run with `Verdict: pass-with-risks`. The orchestrator writes `risk-mitigations.json`; every risk must be `identified`, include concrete `problem`, `impact`, `affected_scope`, evidence, and `next_gate=resolution`. `final.md` must include `Risk Mitigations` with every risk id. If `lane-map.json` exists, reviewer must write `Risk Mitigation Review` and cover every risk id.

Resolution Gate is required after Mitigation Gate for every traceable run with `Verdict: pass-with-risks`. The orchestrator writes `risk-resolutions.json`; every identified risk must have a resolution record with `resolution_type`, concrete action, evidence, verification, and status `fixed`, `mitigated`, or `contained`. `final.md` must include `Risk Resolutions` with every risk id. If `lane-map.json` exists, QA writes `Risk Resolution Verification`, reviewer writes `Risk Resolution Review`, and both cover every resolved risk id. `unresolved` is allowed only for `blocked` or `fail`.

The Architecture Approval Gate handles rejected, regressed, or uncertain architecture attempts. The orchestrator sends the real case back for deeper architecture analysis, then lets workers retry only against the approved steps and records the resulting evidence.

Model/reasoning upgrade is not the default fix. Escalate model or reasoning only when the resolver trigger is justified by task risk; otherwise improve context, architecture contract, evidence, or verification first.

## Subagent Tool Discovery

Discover subagent tools only after the Agent Flow budget is selected and one of these is true:

- the user explicitly asked for subagents;
- the selected budget is `standard` and subagents have clear independent value;
- the selected budget is `release` and subagent gates are expected.

1. Check active tools for a subagent/spawn tool.
2. If not visible and `tool_search` is available, call `tool_search` with a narrow query such as `spawn_agent subagent multi-agent tools`.
3. If `spawn_agent` appears after discovery, use it.
4. If discovery fails, continue with role lanes or solo checks when that still satisfies the task, and state the downgrade in the final answer.

## Product Edit Boundary

Inside Agent Flow, solo product edits are allowed by default. The main agent may edit implementation files, tests, docs, and UI while keeping scope narrow and verification fresh.

The main agent should:

- read code and docs;
- choose the smallest budget;
- avoid unrelated refactors;
- run relevant checks;
- record residual risks;
- update `.agent-work/tasks/` according to global project memory rules;
- avoid `.agent-work/runs/` unless the selected budget requires trace artifacts.

When subagents are used, workers own their assigned narrow write sets and the main agent owns integration and verification.

## Core Decision Tree

1. If the task contains `Agent Flow`, `AgentFlow`, `$agent-flow`, or `agent-flow`, strip that marker and use this skill.
2. If the task does not contain an Agent Flow invocation marker, do not use this skill.
3. Inside Agent Flow, do not call `brainstorming`; classify the request and choose the smallest internal flow directly.
4. If the task is trivial, answer or run the command directly within Agent Flow.
5. Read primary project memory, named task sources, and environment context.
6. Run the dependency gate for new feature, implementation, or delegated work.
7. Choose `light`, `standard`, or `release` budget.
8. Create trace artifacts only for `standard` or `release`, or when the user explicitly asks for artifacts.
9. If the budget and task shape justify subagents, discover `spawn_agent` and delegate only narrow independent work.
10. Otherwise implement solo and verify directly.

## Internal Flows

Read `references/flows.md` when a task is non-trivial or when flow choice is unclear.

Read `references/workflow-patterns.md` when a complex task needs a repeatable shape such as fan-out, adversarial verification, tournament ranking, quarantine, or loop-until-done. Patterns are recipes, not public modes, and they never override the budget gate.

Read `references/subagents.md` when role choice is needed for automatic or explicit subagent delegation. Bundled role instructions live in `agents/<role>.md`; stable identities live in `agents/agent-identities.json`.

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

Read `references/traceable-runs.md` only when the selected budget is `standard` or `release`, or when the user explicitly asks for durable artifacts.

Do not create `.agent-work/runs/` for `light` tasks.

Create `.agent-work/runs/YYYY-MM-DD-task-slug/` for:

- CI, deploy, release, auth, payments, or external services;
- high-stakes or hard-to-reproduce verification;
- explicit user request for trace artifacts;
- subagent delegation where handoffs need persistence.

Do not create run directories for short consultation, one-off shell checks, or tiny one-file edits unless risk grows.

For traceable implementation runs inside git repos, capture `git status --short` before edits and report worktree hygiene in `final.md`: run-owned changes, pre-existing dirty files, and any pre-existing file touched by the run.

Timeline events must be appended in real workflow order. Do not record successful verification before the last implementation or fix. `validate-run.py` enforces this for final handoff.

If a traceable run creates a product commit, create the product commit first, then append a run-local `stage=commit` orchestrator timeline event with the commit hash before writing the final event. AgentFlow trace artifacts under `.agent-work/` remain local audit memory and must not be included in the product commit unless the user explicitly requests that.

Record exactly one final orchestrator timeline event per run.

## Delegation Gate

Read `references/delegation.md` before launching subagents.

Inside Agent Flow, delegation is allowed when the selected budget permits it and the orchestrator can justify it. Delegate only when:

- workstream is independent;
- expected value exceeds coordination cost;
- ownership can be stated clearly;
- verification can be done by the orchestrator;
- subagent tool is available in the current environment.

For each subagent, provide a self-contained delegation packet and require a handoff file when a run directory exists.

Before launching a subagent, read the bundled role file `agents/<role>.md` and resolve `stable_agent_name` plus `stable_agent_slug` from `agents/agent-identities.json`.

Also resolve the role model config before `spawn_agent`, using `python3 scripts/resolve-agent-config.py --role <role>` plus any task triggers such as `--trigger security`, `--trigger broad-scope`, or `--trigger release`. Pass the returned `model` and `reasoning_effort` into `spawn_agent`. Pass `service_tier` only when the resolver returns a non-null value.

If the task would benefit from independent workers but the selected budget is `light`, stay solo or escalate the budget only with a concrete reason. Do not spawn subagents for `light`.

For code review and release readiness work that touches architecture, public contracts, APIs, data flow, security, migrations, or multiple subsystems, require an architect-owned review contract before the reviewer verdict:

1. architect records affected boundaries, risks, ownership, and verification gates;
2. reviewer checks the diff against that architecture contract and QA evidence;
3. orchestrator resolves conflicts and does not close `ship` while architecture gates remain unresolved.

## Done Gate

Read `references/definition-of-done.md` before final response on traceable work.

No completion claim without fresh evidence. Verification can be tests, build, lint, browser screenshots, visual diff, QA notes, docs review, or a checklist tied to acceptance criteria.

Before final response for any repo task, run the Task Status Completion Gate:

- if the current task checklist is complete, verification is recorded, no blocker remains, and any requested product commit succeeded, set the current `.agent-work/tasks/todo.md` section to `Status: done`;
- if a product commit was created for the task, update the current task section after the commit with the commit/check evidence before final handoff;
- if any checklist item, verification, approval, or commit step is missing, keep `Status: in_progress` or `Status: blocked` and record the exact missing item.

For UI workflows, browser proof must exercise the claimed workflow through the UI. Direct API calls may prepare, inspect, or clean up state, but they do not prove clicks, selections, saves, reloads, or visual states unless the app UI performs those steps too.

For visual UI claims, browser proof must capture the claimed target in the screenshot itself. A screenshot of the surrounding page, an off-screen element, or a pre-scroll viewport does not prove the claim. If the target is below the fold, scroll it into view or capture an element-level screenshot, then record the visible target text/state in `checks/browser-proof.md`.

## Design Gate

Read `references/design-flow.md` for UI, UX, Pencil, Figma, screenshots, visual assets, frontend implementation, or design-system work.

Non-trivial frontend/UI implementation needs an approved design source before code starts. If no design source exists, route through design first.

## AI Slop Gate

Read `references/ai-slop-gate.md` for user-facing text, UI/design, generated code, docs, tests, and public artifacts. Simulate the checklist in the main agent by default for `light`; use `ai-slops-hunter` only when `standard` or `release` delegation makes the added check worthwhile. Keep fixes minimal and within scope.

## Scripts

Optional helper scripts live in `scripts/`:

- `init-run.py`: create a traceable run skeleton.
- `append-timeline.py`: append one JSONL timeline event.
- `record-agent-trace.py`: append one subagent or role-lane event to both run and role traces.
- `validate-run.py`: check run completeness before final handoff.
- `validate-architecture-capabilities.py`: check Architecture Capability Router registry and Soft Skill Binding links.

Scripts support the workflow; they do not replace engineering judgment.

## References

- `references/flows.md`: flow catalog and routing.
- `references/workflow-patterns.md`: reusable task-shaping recipes and guardrails.
- `references/subagents.md`: bundled subagent catalog and role-selection guide.
- `references/budgets.md`: light, standard, and release budget rules.
- `references/project-memory-and-env.md`: lessons, PRD/context intake, and infra guard.
- `references/orchestrator.md`: orchestrator responsibilities and mode handling.
- `references/traceable-runs.md`: run directory structure and artifact rules.
- `references/delegation.md`: delegation packets, role handoffs, stable identities.
- `references/definition-of-done.md`: required verification gates.
- `references/design-flow.md`: UI/design-specific route and gates.
- `references/ai-slop-gate.md`: AI slop review route, subagent, and related skills.
- `references/automation-patterns.md`: manual-to-automation promotion pattern.
- `references/architecture-matrix.md`: reusable architecture matrix facets for product, surface, stack, risk, and verification context.
- `references/architecture-capability-router.md`: Architecture Capability Router and Soft Skill Binding contract.
