# Harness Evaluation Loop

Harness Evaluation Loop turns validated trace evidence into a structured learning
record. It runs after the architecture, readiness, continuation, mitigation, and
resolution gates have produced persisted artifacts, including Lane Boundary
Evidence Gate failures or recoveries when a worker changed product code outside
`boundary.allowed_paths` or inside `boundary.forbidden_paths`.

The loop is signal-only for the canonical Agent Flow runtime. It writes
`harness-evaluation.json` and Evidence Records proposals for the current
project. It never treats Architecture Matrix, Architecture Capability Router
registry, role prompts, validator guards, or Golden Trace Runs as promotion
targets for project traces.

## Activation

For full traceable runs with `lane-map.json`, `harness-evaluation.json` is
required when any learning trigger is present:

- `continuation-summary.json` exists or `timeline.jsonl` contains
  `blocked-checkpoint` or `continuation`;
- `risk-mitigations.json` or `risk-resolutions.json` exists;
- a resolution attempt records blocked recovery data such as `blocked_lesson`,
  `rollback`, or `forbidden_repeat`;
- worker `architecture_compliance.status` is `drift`;
- an architecture re-check follows drift;
- Verification Readiness records `needs-approval`, `paused-blocked`, `blocked`,
  approval execution, or readiness retry;
- final verdict is `pass-with-risks`, `blocked`, or `fail` while
  `architecture_contract_required=true`.

Runs without learning triggers do not need this artifact. If the artifact exists,
`validate-run.py` still validates it.

## Artifact Shape

`harness-evaluation.json` records:

- `version=1`;
- `status`: `evaluated`, `needs-review`, or `blocked-learning`;
- `learning_triggers`: trigger ids that are present in the run evidence;
- `source_artifacts`: persisted artifacts used for evaluation;
- `findings`: what worked, failed, regressed, or should become an anti-pattern;
- `proposals`: proposed local Evidence Records promotions.

Every finding and proposal id is kebab-case and must be mentioned in final
`Harness Evaluation`. Positive lane-map runs with a learning trigger also require
reviewer `Harness Evaluation Review`.

## Proposal Boundary

Every proposal must use:

- `type=evidence-record`;
- `target=Evidence Records`;
- `status=proposed`;
- `requires_human_approval=false`.

`scripts/promote-harness-evaluation.py` can promote validated findings into the
current project's Project Memory `## Evidence Records` after
`validate-run.py --mode full` passes. Promotion is project-local. Canonical
runtime artifacts are read-only from real-project trace learning.
Canonical runtime artifacts are not promotion targets.

## Reviewer Responsibility

Reviewer checks that each finding and proposal is backed by persisted evidence.
Claims based only on final prose, chat memory, or implied intent are rejected.

Reviewer also rejects evaluation records that:

- cite missing evidence paths;
- reference unselected `architecture_context` facets;
- reference unknown or unselected `architecture_capabilities`;
- mark a proposal as applied;
- target Architecture Matrix, capability registry, role prompts, validator
  guards, or Golden Trace Runs;
- try to promote Lane Boundary Evidence into runtime artifacts instead of
  project-local Evidence Records;
- set `requires_human_approval` to true.

## Output Sections

`final.md` includes:

```md
## Harness Evaluation

- `finding-id`
- `proposal-id`
```

Reviewer handoff includes:

```md
## Harness Evaluation Review

- `finding-id`
- `proposal-id`
```

The content can be brief, but every id must be present so validation can prove
that learning was reviewed instead of left as narrative.
