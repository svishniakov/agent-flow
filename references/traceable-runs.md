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
delegation-summary.json
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
- a successful `subagent` lane also has a terminal handoff trace event with the same lane id and handoff artifact;
- `role-lane` entries do not require a `codex_thread_id`;
- schema v2 positive lane-map runs require `delegation-summary.json` and a final `Delegation Trace` section;
- `Verdict: ship` is rejected while any critical lane is unresolved, failed, blocked, or missing replacement evidence.

## Delegation Trace Gate

For schema v2 lane-map runs with `Verdict: ship` or `Verdict:
pass-with-risks`, `delegation-summary.json` is required at the run root:

```json
{
  "version": 1,
  "subagents_used": false,
  "role_lanes_used": true,
  "subagents": [],
  "role_lanes": [
    {
      "lane_id": "architecture-contract",
      "role": "architect",
      "reason": "Architecture Contract Gate executed as role-lane."
    }
  ],
  "notes": "No spawned subagents were used. Role lanes are not subagent execution."
}
```

`final.md` must include `Delegation Trace` with these canonical lines:
`Subagents Used`, `Role Lanes Used`, `Subagent Lanes`, `Role Lanes`, and
`Subagent Trace Evidence`. If `subagents_used=false`, run-owned narrative files
must not claim sidecar/subagent work. If a real subagent was used, the summary
must point to `agents/<role>/trace.jsonl`, the lane handoff, and the matching
`codex_thread_id`.

Schema v2 adds the Architecture Contract Gate:

- `budget` is required and must be `standard` or `release`;
- `architecture_contract_required` is a boolean;
- `architecture_contract_independent` is a boolean when present;
- `standard` runs with two or more worker lanes (`implementation` or `integration`) require `architecture_contract_required=true`;
- `release` runs require `architecture_contract_required=true`;
- when `architecture_contract_required` is true, a critical `architecture` lane must exist;
- when `architecture_contract_required` is true, `architecture_context` is required and must include `product_context`, `application_surface`, `architecture_pattern`, `stack_runtime`, `risk_gates`, and `verification_gates`;
- each `architecture_context` axis is an array, at least one facet must be selected across all axes, and every facet id must exist under the matching axis in `references/architecture-matrix.md`;
- `validate-run.py` parses allowed Architecture Matrix facets from the markdown source of truth, not from duplicated constants;
- when `architecture_contract_required` is true, `architecture_capabilities` is required and must include a non-empty `selected` array and non-empty `notes`;
- selected `architecture_capabilities` must exist in `registries/architecture-capabilities.json` and must cover every selected `architecture_context` facet;
- Architecture Capability Router uses Soft Skill Binding: registry `recommended_skills` are checked by `validate-architecture-capabilities.py`, but do not block individual runtime validation;
- Architecture Design Mode runs before implementation when `architecture_contract_required=true`;
- Architecture Artifact Authoring Automation can initialize the run with `init-run.py --architecture-gate`, creating agent-authored templates for Architecture Design Brief, Architecture Contract, worker, QA, reviewer, and evidence artifacts;
- generated templates use the exact placeholder marker `TODO(agent):`, and agents must replace it themselves instead of asking the human to fill the artifacts;
- positive final verdicts, `ship` and `pass-with-risks`, are blocked while any architecture artifact referenced from `lane-map.json` still contains `TODO(agent):`;
- every successful critical `architecture` lane must set `architecture_design_brief` to an existing Architecture Design Brief;
- the Architecture Design Brief must include `Problem Shape`, `Selected Matrix Facets`, `System Boundaries`, `Data And State Model`, `Public Interfaces`, `Execution Plan`, `Risk Model`, `Verification Strategy`, `Open Questions`, and `Decision`;
- `Selected Matrix Facets` must include every selected `architecture_context` facet id, and `Decision` must contain exactly one canonical status line: `Status: approved`, `Status: needs-revision`, or `Status: rejected`;
- Architecture Design Brief `Execution Plan` must include every selected `architecture_capabilities` id;
- final `ship` and `pass-with-risks` require `Status: approved`, and successful worker lanes must run after an approved Architecture Design Brief;
- the architecture handoff `Selected Architecture` section must include every selected `architecture_context` facet id and every selected `architecture_capabilities` id;
- final `ship` requires a successful architecture lane with handoff and evidence;
- the successful architecture handoff must include these headings: `Selected Architecture`, `Rejected Alternatives`, `Module Boundaries`, `Data And State Flow`, `Public Contracts`, `Worker Ownership`, `Forbidden Changes`, `QA Gates`, `Reviewer Checklist`, and `Stop Conditions`;
- positive architecture-gated runs with QA and reviewer lanes require Claim Evidence Gate: the architecture handoff `QA Gates` and `Reviewer Checklist` sections must contain `Claim Evidence` ids, and the run root must include `claim-evidence.json`;
- `claim-evidence.json` uses `version=1` and a non-empty `claims` array; every claim id is unique kebab-case and every required contract claim id has a record;
- each claim record names `owner_lane`, `reviewed_by`, owner handoff `section`, `status`, `claim`, `subjects`, and evidence entries with literal `markers`;
- `owner_lane` must be a successful QA or review lane, `reviewed_by` must be a successful review lane, the owner handoff section must mention the claim id, and every marker must appear in the referenced evidence file;
- positive verdicts require `status=supported`; `status=gap` is allowed only for `blocked` or `fail`;
- failed, blocked, or timed-out architecture lanes block `ship`;
- reviewer and QA lanes may pass only after the architecture contract passes;
- when `architecture_contract_independent` is true, the architecture lane must use `subagent` execution with spawned trace evidence.

## Verification Readiness Gate

When `architecture_contract_required=true` and worker lanes exist, schema v2
requires Verification Readiness Gate before implementation:

- `lane-map.json` records `verification_readiness` with `artifact` pointing to
  `verification-readiness.json` and `lanes` listing readiness lanes;
- readiness lanes use `type=qa`, `role=qa-verifier`, `critical=true`, and run
  after the approved Architecture Design Brief but before worker lanes;
- `verification-readiness.json` uses `version=1` and status `ready`,
  `needs-approval`, `paused-blocked`, or `blocked`;
- readiness attempts cover every selected `risk_gates` and
  `verification_gates` facet from `architecture_context`;
- unknown, unselected, duplicate, wrong-axis, or missing readiness facets fail
  validation;
- `ready` requires all gate records to be ready and no blockers;
- `needs-approval` requires pending `approval_requests` and no successful worker
  lanes;
- approval requests can list only documented safe commands, a source document,
  manual instruction, affected gates, and `resume_phrase`;
- when the user approves, the agent records `approval_executions` with evidence,
  then repeats readiness before starting workers;
- when the user declines, status becomes `paused-blocked`, final verdict must be
  `blocked`, no successful workers may exist, and `final.md` must include the
  manual instruction plus `resume_phrase=Готово`;
- successful worker lanes must run after the latest `ready` readiness lane;
- positive final verdicts require the latest readiness status to be `ready`;
- post-worker QA records `verification_results` in lane-map and a handoff
  section named `Verification Gate Results`;
- a QA lane may pass only when `verification_results.status=pass`; blocked
  required verification forces QA `blocked` and prevents positive final verdicts.

## Continuation Gate

When a same-run continuation resumes after a blocked checkpoint, do not rewrite
the old order as if the new gate had existed from the start. If `timeline.jsonl`
contains `stage=blocked-checkpoint` or `stage=continuation` and the final verdict
is `ship` or `pass-with-risks`, schema v2 requires `continuation-summary.json`.

`continuation-summary.json` uses `version=1` and records:

- `status`: `resumed-ready`, `resumed-blocked`, or `resumed-fail`;
- `previous_checkpoint` with the `blocked-checkpoint` `lane_id`, `verdict=blocked`,
  and a snapshot path such as
  `artifacts/checkpoints/orchestrator-blocked-checkpoint/final.md`;
- `resolved_blockers` with kebab-case ids, concrete resolution text, and evidence;
- `readiness_lane` for the ready Verification Readiness lane;
- `historical_worker_lanes` for worker lanes completed before the blocked
  checkpoint or before the new readiness gate existed;
- `new_worker_lanes` for worker lanes run after continuation readiness became
  ready;
- `revalidated_lanes` for historical worker lanes rechecked after readiness;
- `qa_recheck_lane`, `reviewer_recheck_lane`, and notes.

Continuation validation is timeline-based, not wave-only:

- the previous checkpoint must exist in `timeline.jsonl` as
  `stage=blocked-checkpoint`;
- the readiness lane must have a successful timeline event after the checkpoint;
- every successful worker, readiness, QA, and reviewer lane in a positive
  continuation must have a timeline event with matching `lane_id`;
- worker events before the ready readiness lane are allowed only as declared
  `historical_worker_lanes`, and those lanes must be listed in
  `revalidated_lanes`;
- worker events after the blocked checkpoint but before ready readiness fail;
- new worker events after ready readiness must be listed in `new_worker_lanes`;
- positive continuation requires final `Continuation Summary`, QA
  `Continuation Revalidation`, and reviewer `Continuation Review` sections
  covering every resolved blocker id and every historical or new worker lane id.

## Harness Evaluation Loop

Harness Evaluation Loop turns validated trace evidence into
`harness-evaluation.json`. It is required for full traceable lane-map runs when a
learning trigger exists:

- continuation evidence: `continuation-summary.json`, `blocked-checkpoint`, or
  `continuation` timeline stage;
- risk evidence: `risk-mitigations.json` or `risk-resolutions.json`;
- blocked resolution evidence: blocked attempts, `blocked_lesson`, `rollback`,
  or `forbidden_repeat`;
- architecture evidence: worker `architecture_compliance.status=drift` or an
  architecture re-check after drift;
- readiness evidence: `needs-approval`, `paused-blocked`, `blocked`, approval
  execution, or readiness retry in `verification-readiness.json`;
- final `pass-with-risks`, `blocked`, or `fail` when
  `architecture_contract_required=true`.

`harness-evaluation.json` uses `version=1` and records:

- `status`: `evaluated`, `needs-review`, or `blocked-learning`;
- `learning_triggers` that must match real persisted triggers in the run;
- `source_artifacts` with existing evidence paths;
- non-empty `findings` and `proposals` for `evaluated` and `needs-review`;
- `blocked_reason` and evidence when status is `blocked-learning`;
- proposal `status=proposed` and `requires_human_approval=true`.

Every finding and proposal id must be kebab-case, unique within its array, backed
by existing evidence, and mentioned in final `Harness Evaluation`. Findings may
reference selected `architecture_context` facets and selected
`architecture_capabilities`, but unselected or unknown references fail
validation.

Positive lane-map runs with a learning trigger require reviewer
`Harness Evaluation Review` covering every finding and proposal id. The loop is
signal-only: it can propose Evidence Records, Architecture Matrix changes,
capability registry changes, role prompt updates, validator guards, or Golden
Trace Runs, but it never applies those changes automatically.

Schema v2 also enforces Architecture Execution Control when
`architecture_contract_required=true`:

- successful `implementation` and `integration` lanes must include
  `architecture_compliance` with `status`, `contract_sections`, `notes`, and
  optional `recheck_lane`;
- `architecture_compliance.status` must be `compliant` or `drift`;
- `contract_sections` must name existing Architecture Contract sections;
- Architecture Context Propagation requires
  `architecture_compliance.matrix_facets` as a non-empty selected
  `architecture_context` subset for each successful worker lane;
- successful worker handoffs must include `Architecture Compliance`;
- worker `Architecture Compliance` sections must include every facet id declared
  in `architecture_compliance.matrix_facets`;
- `compliant` worker lanes must not set `recheck_lane`;
- architecture drift blocks `ship` until `recheck_lane` points to a later,
  successful, critical `architecture` lane with contract handoff and evidence;
- when worker lanes exist, final `ship` requires successful QA and reviewer lanes;
- QA must pass after workers and any architecture re-check, and QA handoff must
  include `Architecture Invariants` with every selected `risk_gates` and
  `verification_gates` facet;
- reviewer must pass after architecture, workers, and QA, and reviewer handoff
  must include `Architecture Matrix Mismatches` and `Contract Drift` covering
  every selected `architecture_context` facet and selected
  `architecture_capabilities` id.

Mitigation Gate applies to every traceable run with `Verdict: pass-with-risks`:

- `risk-mitigations.json` is required at the run root;
- `risk-mitigations.json` must use `version=1` and a non-empty `risks` array;
- each risk id must be unique kebab-case;
- each risk status must be `identified`;
- each risk category must be one of `verification-gap`, `architecture-drift`, `incomplete-implementation`, `test-gap`, `security-risk`, `data-risk`, `ux-risk`, `dependency-risk`, `release-risk`, or `unknown`;
- each risk records non-empty `detected_by`, `problem`, `impact`, `affected_scope`, `evidence`, `next_gate`, and `owner_lane`;
- every evidence path must exist, and `next_gate` must be `resolution`;
- `final.md` must include `Risk Mitigations` and every risk id;
- when `lane-map.json` exists, `detected_by` and `owner_lane` must reference existing lane ids;
- when `lane-map.json` exists, a successful reviewer lane must include `Risk Mitigation Review` and every risk id.

Resolution Gate follows Mitigation Gate for every traceable run with `Verdict: pass-with-risks`:

- `risk-resolutions.json` is required at the run root;
- `risk-resolutions.json` must use `version=1` and a non-empty `resolutions` array;
- every resolution `risk_id` must match an identified risk from `risk-mitigations.json`;
- every identified risk must have exactly one resolution record for `pass-with-risks`;
- resolution status must be `fixed`, `mitigated`, or `contained` for `pass-with-risks`;
- `unresolved` is allowed only for `blocked` or `fail`;
- `resolution_type` must be one of `code-change`, `test-added`, `evidence-added`, `scope-contained`, `architecture-recheck`, `config-change`, `docs-corrected`, or `not-resolved`;
- each resolution records non-empty `owner_lane`, `resolution`, `evidence`, `verification`, `verified_by`, and `reviewed_by`;
- every evidence path must exist;
- `final.md` must include `Risk Resolutions` and every risk id;
- when `lane-map.json` exists, `owner_lane`, `verified_by`, and `reviewed_by` must reference existing lane ids;
- when `lane-map.json` exists, `verified_by` must be a successful QA lane and `reviewed_by` must be a successful review lane;
- when lane waves are present, the order is `owner_lane <= verified_by <= reviewed_by`;
- when `lane-map.json` exists, QA handoff must include `Risk Resolution Verification` and every risk id;
- when `lane-map.json` exists, reviewer handoff must include `Risk Resolution Review` and every risk id.

Blocked Resolution Gate is part of Resolution Gate and uses the same `risk-resolutions.json` artifact:

- flat resolution records remain valid when no blocked recovery is needed;
- when `attempts` is present, attempt numbers must be contiguous from `1`, at most three attempts are allowed, and statuses must be `fixed`, `mitigated`, `contained`, `blocked`, or `unresolved`;
- every blocked attempt records `blocked_lesson`, `forbidden_repeat`, `rollback`, `blocked_reason`, evidence, verification, `verified_by`, and `reviewed_by`;
- `rollback.status=not-possible` is allowed only for final `blocked` or `fail`;
- attempt 1 blocked requires a Blocked Recovery Path with Senior QA `Senior QA Test Design Review`, then architect `Resolution Architect Review`; attempt 2 owner lane must run after that architect lane;
- attempt 2 blocked requires `Supervising Architect Review`; attempt 3 owner lane must run after that supervising architect lane;
- Senior QA recovery lanes use `type=qa`, `role=senior-qa-verifier`, successful status, and handoff coverage for the risk id;
- architect recovery lanes use `type=architecture`, `role=architect`, `critical=false`, successful status, `Resolution Architect Review`, and the worker instruction;
- supervising architect recovery lanes use `type=architecture`, `role=supervising-architect`, `critical=false`, successful status, `Supervising Architect Review`, and the final retry instruction or final blocker;
- a third blocked attempt forces final `blocked` or `fail`; it cannot close as `pass-with-risks`.

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
  --stage spawned \
  --status active \
  --codex-thread-id <thread-id> \
  --summary "Spawned python-worker for backend-cli." \
  --next-step "handoff"
```

Then record the terminal handoff:

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
  --artifact handoffs/backend-cli.md
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

`validate-run.py` fails agent traces that look like subagents but have no
spawned event with `codex_thread_id`, no terminal handoff, or a narrative
sidecar/subagent claim without trace evidence. This is intentional: the trace
must distinguish real parallel/delegated execution from role-labeled
orchestration.
