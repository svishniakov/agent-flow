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
- artifacts index is valid JSON;
- each explicitly used subagent has a handoff;
- checks include command names and results;
- final notes run-owned changes and any pre-existing dirty files that were touched or left untouched;
- final verdict is `ship`, `pass-with-risks`, `blocked`, or `fail`.

## Code Gates

- Build/typecheck/lint/tests run when available and relevant.
- Regression scenario checked for bugs.
- Quick adversarial check run for risky assumptions when no separate verifier was authorized.
- No unrelated refactor.
- No dead code, fake tests, or generic abstraction.
- No user changes reverted.

## Pattern Gates

- Workflow patterns are recipes, not public modes.
- Subagent patterns ran only after explicit subagent authorization.
- Loop-until-done has max iterations, budget cap, stop condition, failure condition, and handoff state.
- Tournament has bracket size or max comparisons, stable rubric, tie-breakers, and winner rationale.
- Fan-out work has deterministic item ownership and synthesis by the orchestrator.
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
- Use `ai-slops-hunter` only when the user explicitly requested subagents.
- If a subagent is used, save handoff to `handoffs/ai-slops-hunter.md`.
- If simulated during traceable work, record findings and fixes in `checks/ai-slop-gate.md`.

## Evidence Rule

Do not say work is complete, fixed, passing, or ready without fresh verification evidence.
