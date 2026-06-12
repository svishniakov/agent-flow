# Definition Of Done

Done means scope is complete and evidence exists.

## Base Gates

- Scope completed within stated boundaries.
- Out-of-scope not added silently.
- Changed and important read files are known.
- Verification commands or manual checks are recorded.
- If a workflow pattern was used, its stop condition and verification evidence are recorded.
- Agent artifacts are outside product commits.
- Residual risks are recorded.
- Final verdict is clear.

## Project Memory Sync

Before final handoff for repo tasks:

- update `.agent-work/tasks/todo.md` with final status and verification;
- promote relevant `Project memory handoff` items from subagent artifacts, if subagents were used;
- add confirmed lessons to `.agent-work/tasks/lessons.md`;
- record important decisions, tradeoffs, constraints, evidence links, and follow-up in `.agent-work/tasks/implementation-notes.md` when global criteria make notes relevant.

When a task creates reusable process evidence, record it under `## Evidence Records` instead of burying it in prose. Evidence Records cover success, failure, regression, rejected, Architecture Attempt, Architecture Failure, and Orchestration Failure cases so local learning can compare problem class plus approach.

## Task Status Completion Gate

Before final handoff for any repo task, audit the current `.agent-work/tasks/todo.md` section:

- if every checklist item is checked, verification is recorded, no blocker remains, and any requested product commit succeeded, set `Status: done`;
- if a product commit was created for the task, update the current task section after the commit with commit and check evidence before final handoff;
- if every checklist item is checked but verification, review, approval, or commit evidence is missing, do not call the task done; keep `Status: in_progress` or `Status: blocked` and record the missing evidence;
- if work is intentionally deferred, leave an unchecked item or explicit blocker so the dependency gate has an honest active task to read.

A task section must not remain `Status: in_progress` only because the agent forgot to flip the status after successful verification or commit.

## Traceable Gates

For compact `standard` trace:

- `run.md`, `checks.md`, and `final.md` exist;
- checks include command names and results;
- residual risks are recorded.

For full `release` trace:

- run directory exists;
- `.agent-work/` is locally ignored when inside git repo;
- manifest, route, plan, checks, final files are present;
- timeline is valid JSONL;
- timeline records the actual workflow order, with final successful verification/checks after the last orchestrator implementation/fix;
- if the run creates a product commit, timeline has an orchestrator `stage=commit` event with the commit hash after successful checks and before the final event;
- timeline has exactly one final orchestrator event;
- initial and final worktree states are recorded when the run edits a git repo;
- every delegated subagent has `agents/<role>/trace.jsonl` and matching run-level timeline events;
- when Lane Sharding is used, `lane-map.json` is valid and every critical lane is covered by evidence or a valid replacement lane;
- when the Architecture Contract Gate is required, `lane-map.json` uses schema v2, records `budget`, includes a critical `architecture` lane, and blocks `ship` until that lane has handoff and evidence with the required contract sections;
- when Architecture Matrix facets apply, the architecture handoff records selected facets and the QA/reviewer gates cover facet-driven invariants;
- when Architecture Execution Control applies, worker lanes record `Architecture Compliance`, architecture drift has architect re-check before `ship`, QA records `Architecture Invariants`, and reviewer records `Architecture Matrix Mismatches` plus `Contract Drift`;
- artifacts index is valid JSON;
- each delegated subagent has a handoff;
- checks include command names and results;
- final notes run-owned changes and any pre-existing dirty files that were touched or left untouched;
- final verdict is `ship`, `pass-with-risks`, `blocked`, or `fail`.

## Code Gates

- Build/typecheck/lint/tests run when available and relevant.
- Regression scenario checked for bugs.
- Quick adversarial check run for risky assumptions when no separate verifier was authorized.
- Code review touching architecture, public contracts, APIs, data flow, security, migrations, or multiple subsystems has an architect-owned review contract and reviewer verdict against it.
- Standard traceable runs with at least two worker lanes and all release traceable runs used `architecture_contract_required=true`.
- Architecture Execution Control blocked `ship` until worker `Architecture Compliance`, QA `Architecture Invariants`, reviewer `Contract Drift`, and any architecture drift re-check were covered.
- Architecture Approval Gate reviewed any rejected, regressed, or uncertain architecture attempt before retrying implementation.
- Local Best Practice auto gate was used only for an analyzer-confirmed local practice with clear context, no matching `Do not reuse when`, no external write, and fresh verification.
- regression demotion froze or demoted any practice that failed after reuse.
- Model/reasoning upgrade is not the default fix; context, architecture contract, evidence, and verification must be improved before escalating cost.
- No unrelated refactor.
- No dead code, fake tests, or generic abstraction.
- No user changes reverted.

## Pattern Gates

- Workflow patterns are recipes, not public modes.
- Subagent patterns ran only under `standard` or `release`, or after explicit user request.
- Loop-until-done has max iterations, budget cap, stop condition, failure condition, and handoff state.
- Tournament has bracket size or max comparisons, stable rubric, tie-breakers, and winner rationale.
- Fan-out work has deterministic item ownership and synthesis by the orchestrator.
- Lane Sharding has a valid lane map; no final `ship` while a critical lane is unresolved, failed, blocked, or timed out without replacement.
- Adversarial verification checks evidence against a rubric and records unresolved objections.
- Quarantined workers that read untrusted content did not perform privileged actions.

## Docs Gates

- Source of truth identified.
- Claims match current implementation or cited source.
- Russian docs use clean modern Russian when applicable.
- Public docs checked through `references/ai-slop-gate.md` when applicable.

## UI Gates

- Approved design source exists for non-trivial UI work.
- Browser screenshots captured for target viewports.
- Browser-control availability was probed before the long check; locked profiles, occupied debug ports, stale MCP/browser/test-runner processes, or unsafe cleanup were recorded.
- Browser proof screenshot visibly contains the exact UI target being claimed: heading/label, key state, status, error, table row, modal, overlay, or changed value.
- If the UI target is below the fold, hidden by a panel, inside a scroll container, or clipped in the first viewport, scroll it into view or capture an element-level screenshot. Do not claim browser proof from a screenshot where the target is not visible.
- `checks/browser-proof.md` records target evidence as concrete visible text/states and maps each target to screenshot artifact path(s).
- If browser proof finds the target only through DOM/API inspection but the screenshot does not show it, record the result as `pass-with-risks` or `proof-gap`, not `pass`.
- No clipped text, overlap, or layout shift.
- Default, loading, empty, error, success, disabled, focus, and hover states checked when applicable.
- Accessibility basics checked: contrast, keyboard, focus, labels/ARIA.
- User workflow proof uses the real UI for the workflow being claimed: click/type/select/save/reload through the app. Direct API calls are allowed for setup, diagnostics, or cleanup, but they do not prove a UI interaction unless the result is also exercised through the UI.
- If UI proof uses API shortcuts for setup or cleanup, record exactly which steps were API-only and do not claim those steps as browser-verified UI behavior.

## AI Slop Gates

- For user-facing output, docs, UI/design, generated code, tests, or public artifacts, run the AI slop checklist.
- Simulate the checklist in the main agent by default.
- Use `ai-slops-hunter` only when the selected budget permits subagents and the added check is worthwhile.
- If a subagent is used, save handoff to `handoffs/ai-slops-hunter.md`.
- If simulated during traceable work, record findings and fixes in `checks/ai-slop-gate.md`.

## Evidence Rule

Do not say work is complete, fixed, passing, or ready without fresh verification evidence.

Evidence Records do not replace verification. They make repeated decisions auditable and allow local practices to promote, demote, or become anti-patterns based on observed outcomes.
