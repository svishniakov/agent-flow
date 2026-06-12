# Traceable Runs

Traceable runs store evidence for work that needs durable review history.

`.agent-work/tasks/` is project memory and follows the current user's Codex instructions, usually `~/.codex/AGENTS.md`, for all repo tasks. This file governs only `.agent-work/runs/` trace artifacts.

Do not create a traceable run for `light` budget work.

## Location

Use the current project repo:

```text
<project-repo>/.agent-work/runs/YYYY-MM-DD-task-slug/
```

Use the local date for `YYYY-MM-DD`; Agent Flow runs locally and should not default to UTC.

Do not reuse an existing run directory for a new task. `scripts/init-run.py` rejects date/slug collisions unless `--reuse` is passed deliberately.

If no project repo exists, either skip trace for simple local skill work or create a local run directory only when it adds value. Do not initialize git or change `.gitignore` unless user asked.

## Compact Structure

For `standard` budget, prefer a compact trace:

```text
run.md
checks.md
final.md
artifacts/
```

Use this when the user needs continuity and evidence, but not a full release record.

## Full Structure

For `release` budget or explicit full-trace requests, include:

```text
manifest.md
context.md
route.md
plan.md
definition-of-done.md
decisions.md
lane-map.json
handoffs/
checks/
artifacts/
artifacts.json
timeline.jsonl
final.md
```

Runs that use delegated subagents additionally include per-agent traces and owned artifact directories:

```text
agents/
  <role>/
    trace.jsonl
artifacts/
  agents/
    <role>/
```

Runs that use Lane Sharding add `lane-map.json` as the machine-readable source
of truth and usually add `checks/coverage-matrix.md` as a human-readable
summary. Create those skeletons with `scripts/init-run.py --with-lanes`.

## Local Ignore Rule

Agent artifacts must not enter product commits. Prefer `.git/info/exclude` for `.agent-work/` unless the user explicitly approves `.gitignore`.

## Product Commits And Local Trace

Trace artifacts are local audit memory. They are not part of the product commit by default.

When a traceable run creates a product commit, use this order:

1. finish implementation;
2. run the required checks;
3. create the product commit with only in-scope product/docs changes;
4. update the current `.agent-work/tasks/todo.md` section with commit/check evidence and set `Status: done` when the Task Status Completion Gate is satisfied;
5. append a run-local `stage=commit` orchestrator event with the commit hash;
6. write or update `final.md` with the commit hash, evidence and risks;
7. append the single final orchestrator timeline event;
8. run `scripts/validate-run.py --run-dir <run-dir>`.

Do not create a second commit just to include `.agent-work/` trace changes. The
timeline records the product commit hash locally after the product commit
succeeds.

## Worktree Hygiene

At traceable run intake, capture the project worktree state before edits:

```bash
git status --short
```

Record it in `context.md` or `route.md` under `Initial worktree snapshot`.
If the worktree is dirty, classify the files before making product changes:

- pre-existing unrelated changes;
- pre-existing files that this run must touch;
- new files expected from this run;
- generated artifacts that must not be committed.

Do not hide dirty state in the final report. `final.md` must include a short
worktree section for traceable implementation runs:

- initial dirty files, if any;
- run-owned changed files;
- pre-existing dirty files left untouched;
- pre-existing dirty files touched by the run and why;
- untracked/generated files that should not be committed.

If a run touches a file that was already dirty at intake, say so explicitly in
`final.md`. This keeps commit hygiene reviewable and prevents Agent Flow from
silently mixing user work with run-owned changes.

## File Purposes

- `manifest.md`: goal, scope, invocation, flow, agents, blockers, status, verdict.
- `context.md`: source request, relevant docs, assumptions.
- `route.md`: chosen flow and skipped roles with reasons.
- `plan.md`: checkable plan and ownership.
- `definition-of-done.md`: task-specific gates.
- `decisions.md`: decisions and reasons.
- `lane-map.json`: machine-readable lane map for Lane Sharding runs.
- `handoffs/`: one file per delegated subagent.
- `checks/`: commands, QA, visual diff, review notes.
- `artifacts/`: screenshots, logs, exports, generated assets.
- `artifacts/agents/<role>/`: generated assets, logs, and evidence owned by one subagent.
- `artifacts.json`: machine-readable artifact index. Prefer the top-level array
  shape for new runs; helpers also preserve the object shape
  `{ "artifacts": [...] }` when an existing run uses it.
- `timeline.jsonl`: one JSON event per significant stage.
- `agents/<role>/trace.jsonl`: one JSON event per significant stage for that subagent.
- `final.md`: final result, evidence, residual risks.

## Validation

Run `scripts/validate-run.py --run-dir <run-dir>` before final handoff. By default it fails pending verdicts, missing check files, invalid JSON, and incomplete timeline events. Use `--allow-pending` or `--allow-no-check` only for early structural checks, not final handoff.

Validation remains backward compatible with no-subagent runs: `agents/` is not required. If `agents/<role>/` exists, `timeline.jsonl` and `trace.jsonl` are required. Agent trace files are checked with the same minimum event schema as `timeline.jsonl`, and every agent trace event must also be present in the run-level timeline.

If `lane-map.json` exists, validation also checks the Lane Sharding contract:

- `schema_version` is `1` or `2` and `lanes` is an array;
- lane ids are unique;
- lane type, execution mode, and status use allowed values;
- allowed lane types include `architecture`, `implementation`, `integration`, `qa`, and `review`;
- successful critical lanes point to existing handoff and evidence artifacts;
- a timed-out critical lane points to an existing replacement lane whose status is `pass` or `pass-with-risks`;
- a `subagent` lane with active or successful status has a matching spawned trace event with `codex_thread_id`;
- `role-lane` entries do not require a `codex_thread_id`;
- `Verdict: ship` is rejected while any critical lane is unresolved, failed, blocked, or missing replacement evidence.

Schema v2 adds the Architecture Contract Gate:

- `architecture_contract_required` is a boolean;
- `architecture_contract_independent` is a boolean when present;
- when `architecture_contract_required` is true, a critical `architecture` lane must exist;
- final `ship` requires a successful architecture lane with handoff and evidence;
- failed, blocked, or timed-out architecture lanes block `ship`;
- reviewer and QA lanes may pass only after the architecture contract passes;
- when `architecture_contract_independent` is true, the architecture lane must use `subagent` execution with spawned trace evidence.

For final handoff, `validate-run.py` also requires:

- exactly one `Verdict:` field in `final.md`;
- exactly one valid final verdict value: `ship`, `pass-with-risks`, `blocked`, or `fail`;
- exactly one run-level `timeline.jsonl` final event when a timeline exists or any agent trace exists;
- the last timeline event must be `stage=final` and `role=orchestrator`.
- timeline timestamps must be non-decreasing in file order;
- if the timeline contains orchestrator `implementation` or `fix` events, the
  final successful orchestrator `verification` or `checks` event must come after
  the last such implementation/fix event.
- if `final.md` declares a product commit hash, `timeline.jsonl` must contain a
  matching orchestrator `stage=commit` event before the final event;
- if an orchestrator `stage=commit` event exists, it must come after the last
  successful orchestrator `verification` or `checks` event and before the final
  event.

For compact traces, `timeline.jsonl` is optional only when there are no role/agent traces. If a compact run records timeline or agent traces, the final timeline event rule applies.

## Timeline Event Minimum

```json
{
  "timestamp": "2026-05-22T12:00:00+03:00",
  "stage": "verification",
  "role": "qa-verifier",
  "stable_agent_name": "qa-verifier",
  "stable_agent_slug": "qa-verifier",
  "status": "pass",
  "summary": "Build and regression tests passed.",
  "artifacts": [],
  "next_step": "final review"
}
```

## Trace Updates

For full trace, update `manifest.md` and `timeline.jsonl` after intake, route, plan, delegation, handoff, verification, review, blocker, fix, and final.

Use `scripts/append-timeline.py` for run-level orchestrator events. Use
`scripts/record-agent-trace.py` for subagent events so the same event appears in
the run-level timeline and in `agents/<role>/trace.jsonl`.

Keep timeline events in real workflow order. Do not batch-write route,
implementation, verification, and final at the end with guessed timestamps. If
checks are rerun after a fix, append a new verification/checks event after the
fix. The final timeline should make the actual sequence readable without
opening chat history.

Exactly one final orchestrator event is mandatory before final handoff:

If a product commit was created, append the commit event first:

```bash
python3 scripts/append-timeline.py \
  --run-dir <run-dir> \
  --stage commit \
  --role orchestrator \
  --stable-agent-name orchestrator \
  --stable-agent-slug orchestrator \
  --status pass \
  --summary "Committed product changes as <hash>." \
  --commit-hash <hash> \
  --next-step "write final.md and validate run"
```

```bash
python3 scripts/append-timeline.py \
  --run-dir <run-dir> \
  --stage final \
  --role orchestrator \
  --stable-agent-name orchestrator \
  --stable-agent-slug orchestrator \
  --status pass \
  --summary "Final checks passed and final.md recorded the verdict." \
  --next-step "handoff to user" \
  --artifact final.md \
  --artifact checks.md
```

## Per-Agent Trace Events

Every subagent that receives a delegation packet gets a first-class trace path:

```text
agents/<role>/trace.jsonl
```

The helper creates `agents/<role>/` and `artifacts/agents/<role>/` when needed:

```bash
python3 scripts/record-agent-trace.py \
  --run-dir <run-dir> \
  --role python-worker \
  --execution-mode subagent \
  --lane-id backend-cli \
  --wave 2 \
  --critical \
  --stable-agent-name "Python Worker" \
  --stable-agent-slug python-worker \
  --stage handoff \
  --status pass \
  --summary "Python worker completed helper changes and verification." \
  --next-step "orchestrator review" \
  --artifact handoffs/python-worker.md
```

Pass each owned artifact with repeated `--artifact` flags. The helper indexes
those paths in `artifacts.json` with `role`, `stable_agent_name`,
`stable_agent_slug`, `execution_mode`, `source: agent-trace`, and timestamp metadata. Repeated
records for the same `role` and `path` update the existing artifact entry instead
of duplicating it. If `artifacts.json` is a top-level array, the helper writes a
top-level array back. If it is an object with an `artifacts` array, the helper
updates that field and preserves the object shape.

## Subagent Vs Role Lane

Do not call a role lane a subagent unless an actual subagent/spawn tool was used.

- Actual spawned subagent: record `--execution-mode subagent`, include a `stage=spawned` event with `--codex-thread-id`, then record the terminal handoff/blocked/fail event.
- Role lane without a spawned runtime: record `--execution-mode role-lane`, or keep it as an orchestrator note outside `agents/<role>/`. Its output is a scoped role review, not subagent execution.

`validate-run.py` fails agent traces that look like subagents but have no spawned event with `codex_thread_id`. This is intentional: the trace must distinguish real parallel/delegated execution from role-labeled orchestration.
