# Budgets

Agent Flow optimizes for verified outcome with the smallest useful process.

## Default

Default to `light`.

Escalate only when risk, scope, or user request makes the extra process useful.

## Light

Use for small and medium solo work:

- short bugfixes;
- bounded feature changes in one layer;
- focused docs edits;
- analysis and planning;
- local checks that do not need durable artifact history.

Rules:

- no subagents;
- no `.agent-work/runs/`;
- no full manifest/route/plan bundle;
- `.agent-work/tasks/` still follows global project memory rules from the current user's Codex instructions, usually `~/.codex/AGENTS.md`;
- main agent may edit product files;
- quick adversarial checks are allowed when one skeptical pass improves evidence;
- final answer includes changed files, checks, and residual risks when relevant.

## Standard

Use when evidence will help review, continuation, or rollback:

- multi-file feature across more than one layer;
- bugfix with non-obvious regression risk;
- UI work needing screenshots;
- docs/spec updates that must remain traceable;
- long investigation with decisions worth preserving.
- large PRD or cross-layer work where subagents add useful independent evidence and release-level risk is not present.

Rules:

- solo unless delegation adds clear value;
- subagents are allowed when work can be split into narrow independent lanes, review, or QA evidence;
- workflow patterns may be recorded in `run.md` or `checks.md` when they explain the evidence;
- Lane Sharding may use `lane-map.json` when durable lane evidence is useful;
- compact trace preferred: `run.md`, `checks.md`, `final.md`, plus artifacts that prove the result;
- full trace is optional, not default.

## Release

Use when failure cost is high:

- release gates;
- deploy/CI/package publishing;
- auth, payments, secrets, external services;
- security-sensitive changes;
- high-stakes data migration;
- user explicitly asks for full trace.
- large PRD work with cross-system, security, data, release, or migration risk.

Rules:

- full traceable run;
- fresh checks before completion;
- explicit residual risks;
- orchestrator should consider architect, QA, reviewer, and worker lanes by default;
- subagents may be skipped only when a concrete reason makes solo safer and sufficient;
- code review that touches architecture, public contracts, APIs, data flow, security, migrations, or multiple subsystems requires an architect-owned review contract before reviewer verdict;
- loops, tournaments, and fan-out work require budget caps and stop conditions;
- Lane Sharding requires `lane-map.json`; critical lanes must be covered before `ship`;
- no final `ship` verdict unless acceptance checks passed.

## Escalation

Escalate from `light` to `standard` or `release` only for a concrete reason. Record that reason briefly when trace artifacts are created.

Do not escalate because a task "feels important" or because Agent Flow was invoked.
