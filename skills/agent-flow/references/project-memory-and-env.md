# Project Memory And Environment

Document status: done

Agent Flow must not start work blind.

## Required Intake

Before planning, delegation, product edits, infra commands, DB/storage work, browser checks, or local app startup, read the smallest useful set of project context:

- local project instructions such as `AGENTS.md`;
- primary local project memory in `.agent-work/tasks/`, following the current user's Codex instructions, usually `~/.codex/AGENTS.md`:
  - create `.agent-work/tasks/`, `todo.md`, and `lessons.md` for repo tasks when missing;
  - read `lessons.md` and `todo.md` before repo work;
  - read `implementation-notes.md` when global criteria make it relevant;
  - treat `## Evidence Records` in `implementation-notes.md` as structured success, failure, regression, rejected, architecture, and orchestration evidence;
  - update `todo.md` as the current task checklist;
  - close the current `todo.md` task as `Status: done` when its checklist, verification, blockers, and requested commit state satisfy the Task Status Completion Gate;
- project-declared legacy memory such as `docs/tasks/*` only when local project instructions explicitly name it as current memory;
- PRD/spec/design docs named by the user;
- the full related-document list in existing scope per repository, including the source implementation plan, ADR and research; retain it for delegation and final acceptance under `definition-of-done.md`;
- environment docs for infra, Docker, local dev, migrations, test data, and app startup when the task can touch them;
- package scripts or task docs for the commands you plan to run.

If a named PRD/spec is the task source, read it before route/plan. Do not infer scope from file name only.

If `.agent-work/tasks/lessons.md` is missing during a repo task, create the file according to global project memory rules. Do not invent lesson content. Add lesson entries only after a user correction, repeated process failure, or explicit request to record a lesson.

## Dependency Gate

Before planning new feature work, product edits, cross-file implementation, or
delegation, inspect `.agent-work/tasks/todo.md` for existing sections marked
`Status: in_progress` or `Status: blocked`. Ignore the section for the current
request if it was already added as bookkeeping.

`in_progress` and `blocked` are lookup cues, not proof of ongoing work. Check
the found task's scope, remaining requirements and stated blocker. Search later
completion and checks by the same task/plan ID in memory and linked documents
across every named repository. Verify supplied SHA and relevant committed content;
a commit alone does not prove all requirements. Similar titles or a common product
area do not prove a conflict. Read a linked session's actual state when available;
a finished/interrupted session does not prove completion, and unavailable session
listing alone is not a blocker.

Separate old work's state from its relationship to the new task. A completed
document may describe implementation that has not started.

### Historical record correction

For either old status, verified later completion can support a narrow correction
even when old checklist items are unchecked. Match evidence to those requirements;
for `blocked`, separately confirm removal of its stated cause and absence of new
work under that record. Record the task ID, completed scope, verification sources,
repository/SHA when commit was part of delivery, date and correction reason.
Later verified completion takes precedence over earlier pending notes. Preserve
unresolved requirements; when transferred, link their continuation explicitly.

Source verification is sufficient for historical record correction: no new run
or repeat QA/reviewer of the old implementation is required. Do not rewrite old
runs, final messages or evidence. Repeated intake is idempotent: do not add another
closure or return the corrected record to active blockers. This exception does
not waive current implementation's independent acceptance.

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

- `clear`: independence is confirmed. Continue even if old work is unfinished
  or evidence is insufficient to close it; keep that old status truthful.
- `uncertain`: available sources leave a material gap about a specific result
  required by the new task. Stop only that dependent part and name the missing fact.
- `dependent`: confirmed ongoing work changes the same necessary file or contract.
  Stop only the conflicting part and cite actual activity and concrete overlap.

If every active task is `clear`, continue and record that the dependency gate
passed in task memory or trace notes when those artifacts exist.

When `scripts/codegraph.py` is available, the Dependency Gate may call
`python3 scripts/codegraph.py deps` before warning about overlap. Treat the
result as local evidence: cite shared files, symbols, tests, and gaps when they
help the user decide, but keep the orchestrator responsible for the final
classification.

Stale notes, age, unchecked boxes, missing old runs and the word `uncertain` do
not independently stop implementation, delegation or trace setup. Continue the
authorized independent part. Involve the user only after available checks leave
a material dependency unresolved. Explain the task ID, concrete shared file or
required result, missing fact and practical risk. For a real conflict, recommend
waiting, merging into one coordinated run, or explicit agreement on isolated
scope. Record that agreement; never bypass a real conflict by closing old work.

Do not block internal lane sharding, workers, or QA/review lanes that belong to
the same Agent Flow run. The gate protects separate user-launched feature
sessions from silently stepping on each other.

CodeGraph failure is a gap, not a hard replacement for this gate. If the graph
cannot refresh or returns `unknown`, fall back to the manual comparison above
and record the graph failure in trace notes when a run exists.

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

Use Evidence Records for reusable approach evidence. Lessons are direct rules for future behavior; Evidence Records preserve the observed cases that let the analyzer promote, demote, freeze, or reject local practices.
