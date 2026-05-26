# Traceable Runs

Traceable runs store evidence for work that needs durable review history.

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
handoffs/
checks/
artifacts/
artifacts.json
timeline.jsonl
final.md
```

Runs that use explicitly requested subagents additionally include per-agent traces and owned artifact directories:

```text
agents/
  <role>/
    trace.jsonl
artifacts/
  agents/
    <role>/
```

## Local Ignore Rule

Agent artifacts must not enter product commits. Prefer `.git/info/exclude` for `.agent-work/` unless the user explicitly approves `.gitignore`.

## File Purposes

- `manifest.md`: goal, scope, invocation, flow, agents, blockers, status, verdict.
- `context.md`: source request, relevant docs, assumptions.
- `route.md`: chosen flow and skipped roles with reasons.
- `plan.md`: checkable plan and ownership.
- `definition-of-done.md`: task-specific gates.
- `decisions.md`: decisions and reasons.
- `handoffs/`: one file per explicitly used subagent.
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

Validation remains backward compatible with older no-subagent runs: `agents/` is not required. If `agents/<role>/` exists, `trace.jsonl` is required there. Agent trace files are checked with the same minimum event schema as `timeline.jsonl`, and every agent trace event must also be present in the run-level timeline.

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

For full trace, update `manifest.md` and `timeline.jsonl` after intake, route, plan, explicit delegation, handoff, verification, review, blocker, fix, and final.

Use `scripts/append-timeline.py` for run-level orchestrator events. Use
`scripts/record-agent-trace.py` for subagent events so the same event appears in
the run-level timeline and in `agents/<role>/trace.jsonl`.

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
  --stable-agent-name "Мышарик" \
  --stable-agent-slug mysharik \
  --stage handoff \
  --status pass \
  --summary "Python worker completed helper changes and verification." \
  --next-step "orchestrator review" \
  --artifact handoffs/python-worker.md
```

Pass each owned artifact with repeated `--artifact` flags. The helper indexes
those paths in `artifacts.json` with `role`, `stable_agent_name`,
`stable_agent_slug`, `source: agent-trace`, and timestamp metadata. Repeated
records for the same `role` and `path` update the existing artifact entry instead
of duplicating it. If `artifacts.json` is a top-level array, the helper writes a
top-level array back. If it is an object with an `artifacts` array, the helper
updates that field and preserves the object shape.
