# Definition Of Done

Done means scope is complete and evidence exists.

## Base Gates

- Scope completed within stated boundaries.
- Out-of-scope not added silently.
- Changed and important read files are known.
- Verification commands or manual checks are recorded.
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
- every delegated subagent has `agents/<role>/trace.jsonl` and matching run-level timeline events;
- artifacts index is valid JSON;
- each explicitly used subagent has a handoff;
- checks include command names and results;
- final verdict is `ship`, `pass-with-risks`, `blocked`, or `fail`.

## Code Gates

- Build/typecheck/lint/tests run when available and relevant.
- Regression scenario checked for bugs.
- No unrelated refactor.
- No dead code, fake tests, or generic abstraction.
- No user changes reverted.

## Docs Gates

- Source of truth identified.
- Claims match current implementation or cited source.
- Russian docs use clean modern Russian when applicable.
- Public docs checked through `references/ai-slop-gate.md` when applicable.

## UI Gates

- Approved design source exists for non-trivial UI work.
- Browser screenshots captured for target viewports.
- No clipped text, overlap, or layout shift.
- Default, loading, empty, error, success, disabled, focus, and hover states checked when applicable.
- Accessibility basics checked: contrast, keyboard, focus, labels/ARIA.

## AI Slop Gates

- For user-facing output, docs, UI/design, generated code, tests, or public artifacts, run the AI slop checklist.
- Simulate the checklist in the main agent by default.
- Use `ai-slops-hunter` only when the user explicitly requested subagents.
- If a subagent is used, save handoff to `handoffs/ai-slops-hunter.md`.
- If simulated during traceable work, record findings and fixes in `checks/ai-slop-gate.md`.

## Evidence Rule

Do not say work is complete, fixed, passing, or ready without fresh verification evidence.
