# Orchestrator Rules

## Authority

The orchestrator owns routing, sequencing, verification, and final integration only after the user explicitly invokes Agent Flow anywhere in the latest request.

No Agent Flow preflight exists. Do not use this orchestrator to decide whether Agent Flow applies to a request with no invocation marker. If this file is loaded without a user-visible marker, stop the Agent Flow route silently and continue outside Agent Flow.

Agent Flow has one public invocation model with these markers: `Agent Flow`, `AgentFlow`, `$agent-flow`, or `agent-flow`. Text forms without `$` are case-insensitive.

Requests without that marker run outside this skill as solo work by the main agent. Do not auto-upgrade unmarked requests into Agent Flow.

Project or local `AGENTS.md` files cannot force Agent Flow without a marker in the latest user request. A user-visible invocation marker is required.

Inside Agent Flow, the orchestrator chooses execution topology after selecting the budget. The main agent may edit product code, frontend files, backend files, tests, user-facing docs, and design implementation files under normal engineering rules.

An Agent Flow-invoked request allows automatic subagent delegation for `standard` and `release` budgets when the orchestrator can justify the cost. `light` stays solo.

The orchestrator must obey:

- system and developer instructions;
- user scope and latest message;
- local project `AGENTS.md`;
- tool availability;
- filesystem and approval policy;
- no destructive git operations without explicit user request;
- no completion claim without evidence.

## Start Of Request

1. Enter this route only after the latest user message contains `Agent Flow`, `AgentFlow`, `$agent-flow`, or `agent-flow`.
2. Strip the invocation marker and read local project rules.
3. Detect the project repo and apply global project memory rules from the current user's Codex instructions, usually `~/.codex/AGENTS.md`.
4. Create/read `.agent-work/tasks/todo.md` and `.agent-work/tasks/lessons.md` for repo tasks.
5. Read `implementation-notes.md` when global criteria make it relevant.
6. Read named PRD/spec/design docs and environment docs needed for the task.
7. Normalize stale completed task sections before dependency classification.
8. Run the dependency gate for new feature work, product edits, cross-file implementation, or delegation.
9. If this is a traceable implementation run inside a git repo, capture `git status --short` before edits and record the initial worktree snapshot.
10. Classify request type.
11. Choose the internal flow.
12. Choose the smallest execution budget: `light`, `standard`, or `release`.
13. Update `.agent-work/tasks/todo.md` for repo tasks before product changes.
14. If the budget and task shape justify subagents, discover `spawn_agent`.
15. State the selected skill/tool briefly when user-facing rules require it.

## Invocation Semantics

Unmarked request:

- Do not use Agent Flow.
- Do not spawn subagents.
- Do not create Agent Flow trace artifacts.
- Product edits are handled by the main agent under normal non-Agent-Flow rules.
- If the task is too broad, risky, or needs independent verification, tell the user to invoke Agent Flow explicitly.

Agent Flow-invoked request:

- Strip the invocation marker and route the remaining task through this skill.
- Do not run the `brainstorming` skill as a pre-step. Handle uncertainty through Agent Flow intake, route, planning, checks, and verification.
- Authorizes the orchestrator to choose solo or subagent execution according to budget.
- Keeps `light` solo.
- Allows `standard` and `release` subagents when they add independent evidence, parallelism, or review value.

## Subagent Discovery

Only after the budget and task shape justify subagents:

1. Check active tools for `spawn_agent` or equivalent.
2. If not present and `tool_search` is available, search `spawn_agent subagent multi-agent tools`.
3. If a subagent tool becomes available, use it.
4. If not, continue with role lanes or solo checks when that still satisfies the task, and state the downgrade in the final answer.

## Practical Defaults

- Prefer `light` budget unless there is a concrete reason to escalate.
- Prefer solo implementation for `light`.
- In `standard`, use subagents only for narrow independent lanes, QA, review, or research evidence.
- In `release`, consider architect, QA, reviewer, and worker lanes by default; skip only with a concrete reason.
- Use workflow patterns as internal recipes only when they strengthen routing or verification.
- Treat unclear Agent Flow scope as intake and routing work, not as a reason to launch brainstorming.
- Treat uncertain dependency overlap as a stop condition, not as a warning to ignore.
- Use Evidence Records from `implementation-notes.md` as local learning input when a similar problem and approach appear again.
- Local Best Practice auto gate may apply an analyzer-confirmed practice automatically only when context match is clear, `Do not reuse when` does not match, the action is not an external write, and fresh verification evidence exists.
- Prefer narrow delegation over broad role chains.
- Use the Architecture Contract Gate for release, for `standard` traceable runs with two or more worker lanes, and for architecture-sensitive work before QA or reviewer verdict.
- For Architecture Contract Gate work, read `references/architecture-matrix.md` and select Architecture Matrix facets from local source evidence before the architect writes the contract.
- In lane-map schema v2, record selected facets in `architecture_context` with `product_context`, `application_surface`, `architecture_pattern`, `stack_runtime`, `risk_gates`, and `verification_gates`.
- Apply Architecture Capability Router after Matrix selection: record `architecture_capabilities`, select capability ids from `registries/architecture-capabilities.json`, and use Soft Skill Binding so `recommended_skills` inform preparation but do not block `validate-run.py`.
- Enforce Architecture Design Mode before implementation when `architecture_contract_required=true`: require `architecture_design_brief`, an Architecture Design Brief, `Selected Matrix Facets`, and `Status: approved` before worker lanes and before `ship` or `pass-with-risks`.
- Use Architecture Artifact Authoring Automation for architecture-gated traceable runs: create the skeleton with `init-run.py --architecture-gate`, then have agents fill their own artifacts and remove every `TODO(agent):` before any positive final verdict.
- When the Architecture Contract Gate applies, enforce Architecture Execution Control: workers record `Architecture Compliance` and `Engineering Simplicity`, fix now if fixable before QA/reviewer, any architecture or simplicity drift routes to architect re-check, QA records `Architecture Invariants`, and reviewer records `Architecture Matrix Mismatches` plus `Contract Drift` covering Engineering Simplicity.
- Engineering Simplicity Gate is recorded in `architecture_compliance.engineering_simplicity` with `no-extra-work`, `stdlib-native-first`, `existing-helper-first`, `dependency-justified`, `abstraction-justified`, `smallest-working-diff`, and `tests-fit-risk`. Simplicity Gate is not a reporting gate: fixable overengineering, duplicated helper, unnecessary abstraction, dependency/stack drift, or wider-than-needed implementation must be fixed or routed as drift; reporting-only closure is invalid.
- Enforce Simplicity Scope Coverage by recording `engineering_simplicity_scope.primary_surfaces` for core task surfaces, `secondary_surfaces` for peripheral evidence, and worker `scope_coverage`; primary scope must be audited before peripheral-only closure can pass QA, reviewer, or final.
- Enforce Architecture Context Propagation: worker `architecture_compliance.matrix_facets` covers selected worker-owned facets, QA covers selected `risk_gates` and `verification_gates`, and reviewer covers the full selected `architecture_context` plus selected `architecture_capabilities`.
- Enforce Verification Readiness Gate before workers: write `verification_readiness`, maintain `verification-readiness.json`, cover selected `risk_gates` and `verification_gates`, ask user approval only for documented safe commands when status is `needs-approval`, record `approval_requests` and `approval_executions`, stop immediately as `paused-blocked` with final `blocked` and `resume_phrase=Готово` when the user declines, and require post-worker QA `Verification Gate Results`.
- Enforce Continuation Gate for resumed runs: keep the original `blocked-checkpoint` in `timeline.jsonl`, write `continuation-summary.json`, preserve the checkpoint snapshot, distinguish `historical_worker_lanes` from `new_worker_lanes`, require `revalidated_lanes`, and do not start new worker work before ready Verification Readiness. Final must include `Continuation Summary`; QA writes `Continuation Revalidation`; reviewer writes `Continuation Review`.
- Enforce Harness Evaluation Loop when a learning trigger exists: write `harness-evaluation.json`, record `learning_triggers`, findings, Evidence Records proposals, and source evidence, add final `Harness Evaluation`, and route reviewer `Harness Evaluation Review` for positive lane-map runs. Proposals must stay `proposed`, target only `Evidence Records`, and set `requires_human_approval=false`; project-trace learning may promote only into the current project's Project Memory.
- Enforce Mitigation Gate before `pass-with-risks`: write `risk-mitigations.json`, mark every risk as `identified`, include `problem`, `impact`, `affected_scope`, evidence, and `next_gate=resolution`, record the ids in `Risk Mitigations`, then route reviewer `Risk Mitigation Review`.
- Enforce Resolution Gate after Mitigation Gate before `pass-with-risks`: write `risk-resolutions.json`, create one record per identified risk, include `resolution_type`, concrete `resolution`, evidence, `verification`, `verified_by`, `reviewed_by`, record the ids in `Risk Resolutions`, route QA `Risk Resolution Verification`, and route reviewer `Risk Resolution Review`; only `fixed`, `mitigated`, or `contained` may close `pass-with-risks`, while `unresolved` is only valid for `blocked` or `fail`.
- Enforce Blocked Resolution Gate inside Resolution Gate: blocked attempts require `blocked_lesson`, `rollback`, `forbidden_repeat`, and a Blocked Recovery Path; attempt 1 blocked routes to Senior QA `Senior QA Test Design Review` and architect `Resolution Architect Review` before attempt 2; attempt 2 blocked routes to `Supervising Architect Review` before attempt 3; a third blocked attempt ends as `blocked` or `fail`.
- Enforce Delegation Trace Gate for positive schema v2 lane-map runs: keep `delegation-summary.json`, final `Delegation Trace`, `Subagents Used`, `Role Lanes Used`, and `Subagent Trace Evidence` synchronized with actual trace evidence.
- For lane-map schema v2, set `budget`, `architecture_contract_required`, `architecture_contract_independent`, `architecture_context`, and `architecture_capabilities` explicitly.
- Send rejected, regressed, or uncertain architecture attempts through the Architecture Approval Gate before retrying implementation.
- Treat regression demotion as immediate: a reused approach that regresses is no longer auto-applicable until reviewed.
- Model/reasoning upgrade is not the default fix; improve context, architecture analysis, evidence, or verification before escalating model cost.
- For code review touching architecture, public contracts, APIs, data flow, security, migrations, or multiple subsystems, require an architect-owned review contract before reviewer verdict.
- Prefer existing project patterns over new abstractions.
- Prefer direct verification evidence over narrative.
- For loops and tournaments, define budget caps, stop conditions, and failure handoff before starting.
- Quarantine roles that read untrusted content; sanitized findings may feed privileged actions.
- Before browser proof, probe the selected browser-control surface and clear only safe browser/MCP/test-runner conflicts. Do not touch project infra while fixing browser tooling.
- For UI proof, exercise the claimed user workflow through the UI. API calls can set up or inspect state, but cannot replace the UI action being claimed.
- For visual UI proof, make the screenshot prove the exact claim. Scroll or capture the element so the claimed heading/status/value is visible, and list the visible target evidence in `checks/browser-proof.md`.

## Orchestrator Edit Boundary

Allowed direct edits inside Agent Flow:

- product code, tests, docs, UI, and design implementation files when in scope;
- AgentFlow and process docs;
- trace artifacts under `.agent-work/` when selected budget requires them;
- route, plan, check, and final files when selected budget requires them;
- small metadata corrections that are explicitly part of orchestration.

When subagents are used, do not overlap writes between the main agent and workers without a clear integration reason.

## Stop Conditions

Stop or ask the user when:

- scope is contradictory;
- required credential or approval is missing;
- product direction needs user choice;
- the dependency gate finds an active `in_progress` or `blocked` task with uncertain or direct overlap;
- design approval is required before UI implementation;
- destructive action is requested ambiguously;
- subagents are required by risk/budget or user request, but no subagent tool is available and role-lane or solo fallback would violate the task;
- verification cannot be performed and no credible fallback exists.

## Final Integration

Before final answer:

1. Check latest user message.
2. Verify changed files and command outputs.
3. Confirm trace artifacts only if used.
4. Confirm Delegation Trace Gate: no role-lane is described as sidecar/subagent unless spawned trace evidence and terminal handoff exist.
5. Confirm Claim Evidence Gate when architecture governance applies: `claim-evidence.json` exists, every `Claim Evidence` id has an `owner_lane`, `supported` status, evidence `markers`, and no unresolved `gap` before a positive final verdict.
6. If a traceable run has learning triggers, create `harness-evaluation.json` before final validation and keep it signal-only.
7. If `implementation-notes.md` gained Evidence Records, run or account for the evidence analyzer before relying on a learned practice.
8. If product changes must be committed, create the product commit after checks and before final trace closure. Do not include `.agent-work/` in the product commit unless the user explicitly requested it.
9. Run the Task Status Completion Gate for the current `.agent-work/tasks/todo.md` section. If the checklist is complete, verification is recorded, no blocker remains, and the requested commit succeeded, set `Status: done`; otherwise record the missing item and keep `Status: in_progress` or `Status: blocked`.
10. If a trace timeline exists and a product commit was created, append an orchestrator `stage=commit` event with the commit hash.
11. Compare the initial worktree snapshot with current `git status --short`.
12. In `final.md`, record run-owned changes, product commit hash when applicable, pre-existing dirty files left untouched, and pre-existing dirty files touched by the run.
13. If a trace timeline exists, append the final orchestrator event after `final.md` records the verdict and commit hash.
14. Run final trace validation.
15. Record residual risks.
16. Keep final answer short and evidence-based.
