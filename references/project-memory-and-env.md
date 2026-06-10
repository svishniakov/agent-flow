# Project Memory And Environment

Agent Flow must not start work blind.

## Required Intake

Before planning, delegation, product edits, infra commands, DB/storage work, browser checks, or local app startup, read the smallest useful set of project context:

- local project instructions such as `AGENTS.md`;
- primary local project memory in `.agent-work/tasks/`, following the current user's Codex instructions, usually `~/.codex/AGENTS.md`:
  - create `.agent-work/tasks/`, `todo.md`, and `lessons.md` for repo tasks when missing;
  - read `lessons.md` and `todo.md` before repo work;
  - read `implementation-notes.md` when global criteria make it relevant;
  - update `todo.md` as the current task checklist;
  - close the current `todo.md` task as `Status: done` when its checklist, verification, blockers, and requested commit state satisfy the Task Status Completion Gate;
- project-declared legacy memory such as `docs/tasks/*` only when local project instructions explicitly name it as current memory;
- PRD/spec/design docs named by the user;
- environment docs for infra, Docker, local dev, migrations, test data, and app startup when the task can touch them;
- package scripts or task docs for the commands you plan to run.

If a named PRD/spec is the task source, read it before route/plan. Do not infer scope from file name only.

If `.agent-work/tasks/lessons.md` is missing during a repo task, create the file according to global project memory rules. Do not invent lesson content. Add lesson entries only after a user correction, repeated process failure, or explicit request to record a lesson.

## Dependency Gate

Before planning new feature work, product edits, cross-file implementation, or
delegation, inspect `.agent-work/tasks/todo.md` for existing sections marked
`Status: in_progress` or `Status: blocked`. Ignore the section for the current
request if it was already added as bookkeeping.

Before dependency classification, normalize stale completed sections:

- if a section is `Status: in_progress`, every checklist item is checked,
  `Review:` records verification, and no blocker remains, update that section
  to `Status: done` and do not use it as a blocker;
- if every checklist item is checked but verification, review, approval, or
  commit evidence is missing, keep it active and classify it as `uncertain`;
- if a product commit is recorded as the task result, the current task section
  must be updated after the commit and before final handoff.

If the new request names a PRD, spec, design source, issue, or task document,
read that source before dependency classification. The gate must compare active
work with the real requested scope, not only with the prompt wording.

For each active task, compare it with the new request across practical surfaces:

- files, packages, generated artifacts, and tests;
- API contracts, shared types, routes, events, queues, and background jobs;
- DB/storage schema, migrations, seed data, and external integrations;
- UI flows, design sources, user-facing copy, and visual assets;
- infra, environment, deploy, release, and CI paths;
- acceptance criteria and product decisions.

Classify every active task:

- `clear`: no meaningful overlap with the new request.
- `uncertain`: possible overlap, stale active notes, incomplete ownership, or
  too little context to prove independence.
- `dependent`: active work may affect the new request, or the new request may
  affect the active work.

If every active task is `clear`, continue and record that the dependency gate
passed in task memory or trace notes when those artifacts exist.

If any task is `uncertain` or `dependent`, stop before implementation,
delegation, or trace setup. The user-facing warning must include:

- active task title and status;
- likely shared surface or missing context;
- practical risk, such as rework, conflicting contracts, broken tests, or
  inconsistent UX;
- recommendation to wait for the active feature to finish and restart from that
  result.

The orchestrator may continue only after the user explicitly accepts the risk.
If continuing, record the override, isolate scope, and avoid writes to the
active task's likely ownership unless the user chose to merge the work into one
coordinated Agent Flow run.

Do not block internal lane sharding, workers, or QA/review lanes that belong to
the same Agent Flow run. The gate protects separate user-launched feature
sessions from silently stepping on each other.

## Infra Guard

Default posture: existing project infra already exists.

Do not start a parallel local service just because a DB, object store, queue, browser, or app endpoint is unavailable.

Forbidden without explicit user approval or a clearly documented project command for the current repo:

- starting a new local Postgres, MinIO, Qdrant, Redis, queue, or model service;
- `docker compose up`, `down`, `recreate`, or volume reset;
- DB recreate, bucket cleanup, destructive seed reset, or test data wipe;
- changing ports or env to route around an existing service;
- installing or launching alternate infra outside the project docs.

Allowed discovery:

- read env examples and project docs;
- inspect package scripts;
- check container status with read-only/status commands;
- inspect logs when needed;
- run documented migration/test commands against the existing dev environment.

If existing infra is down or inconsistent:

1. Record the exact missing dependency.
2. Report a blocker.
3. Ask whether to start or repair the existing project dev stack.

Do not silently provision a second stack.

## Browser Control Guard

Before browser checks, screenshots, visual proof, or local UI smoke, choose one browser-control surface for the task and probe it before running the long check:

- Chrome DevTools;
- Playwright MCP;
- Browser Use or in-app browser;
- local Playwright through shell with Google Chrome.

The probe must verify that the tool answers, can open the target, the selected browser channel exists, and the profile/debug port/user-data-dir is not locked.

If the probe finds a locked profile, occupied debug port, stale MCP process, stuck browser, or stuck test-runner, clean up only that browser-control conflict by PID/process name/path and repeat the probe. Do not start a long smoke/browser proof on top of unavailable tooling.

Cleanup must not stop or reset project infra: Docker Compose, Postgres, MinIO, Qdrant, model gateway, backend/frontend dev servers, volumes, DB, and buckets require explicit approval or a documented project command for that exact action.

If cleanup is unsafe, use one clean isolated browser profile/user-data-dir for the selected surface. If that also fails, record the exact blocker instead of cascading through multiple fallback tools.

Browser proof quality rule:

- A screenshot must show the exact UI element or state being claimed.
- If the target is off-screen, hidden inside a scroll container, or outside the first viewport, scroll it into view or capture an element-level screenshot.
- Record the visible target evidence in `checks/browser-proof.md`, including the expected text/status/value and the screenshot artifact path.
- DOM/API checks can support the proof, but they do not replace a screenshot that visually contains the target.

## Delegation Context

Before launching any subagent, the orchestrator must package project memory and env constraints into the delegation packet:

- relevant lessons from `.agent-work/tasks/lessons.md`;
- relevant active task notes from `.agent-work/tasks/todo.md` and `.agent-work/tasks/implementation-notes.md`;
- dependency gate outcome, including active task conflicts or explicit user override;
- named PRD/spec/design source;
- current repo and expected existing infra;
- allowed commands;
- forbidden infra actions;
- expected verification path;
- dirty worktree warning.

Workers must not run Agent Flow, re-route the task, start infra, reset services, or widen scope unless the packet explicitly allows it.

## Lesson Updates

After a user correction or repeated process failure, update `.agent-work/tasks/lessons.md` with:

- concrete failure pattern;
- rule that prevents recurrence;
- project-specific command or doc path when known.

Keep lessons short. Do not store secrets, tokens, private URLs, or raw logs.
