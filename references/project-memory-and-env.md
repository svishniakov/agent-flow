# Project Memory And Environment

Agent Flow must not start work blind.

## Required Intake

Before planning, delegation, product edits, infra commands, DB/storage work, browser checks, or local app startup, read the smallest useful set of project context:

- local project instructions such as `AGENTS.md`;
- primary local project memory in `.agent-work/tasks/`:
  - `lessons.md` when present;
  - `todo.md` when present;
  - `implementation-notes.md` when present and relevant;
- project-declared legacy memory such as `docs/tasks/*` only when local project instructions explicitly name it as current memory;
- PRD/spec/design docs named by the user;
- environment docs for infra, Docker, local dev, migrations, test data, and app startup when the task can touch them;
- package scripts or task docs for the commands you plan to run.

If a named PRD/spec is the task source, read it before route/plan. Do not infer scope from file name only.

If `.agent-work/tasks/lessons.md` is missing, do not invent one during normal work. Create or update it only when the user reports a process failure, a repeated blocker appears, or the task explicitly asks to record a lesson.

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

## Delegation Context

Before launching any subagent, the orchestrator must package project memory and env constraints into the delegation packet:

- relevant lessons from `.agent-work/tasks/lessons.md`;
- relevant active task notes from `.agent-work/tasks/todo.md` and `.agent-work/tasks/implementation-notes.md`;
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
