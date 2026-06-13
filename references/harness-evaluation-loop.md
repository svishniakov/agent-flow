# Harness Evaluation Loop

Harness Evaluation Loop turns validated trace evidence into a structured learning
record. It runs after the architecture, readiness, continuation, mitigation, and
resolution gates have produced persisted artifacts.

The loop is signal-only in v1. It writes `harness-evaluation.json` and proposals
that require human approval. It never edits Architecture Matrix, Architecture
Capability Router registry, role prompts, golden traces, or project memory
automatically.

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
- `proposals`: proposed changes that still require human approval.

Every finding and proposal id is kebab-case and must be mentioned in final
`Harness Evaluation`. Positive lane-map runs with a learning trigger also require
reviewer `Harness Evaluation Review`.

## Proposal Boundary

Every proposal must use `status=proposed` and
`requires_human_approval=true`.

Allowed proposal targets include Evidence Records, Architecture Matrix,
Architecture Capability Router, verification gates, role prompts, validator
guards, and Golden Trace Runs. A proposal is not an applied change. Promotion to
Evidence Records or any runtime rule update happens in a separate, explicit
human-approved step.

## Reviewer Responsibility

Reviewer checks that each finding and proposal is backed by persisted evidence.
Claims based only on final prose, chat memory, or implied intent are rejected.

Reviewer also rejects evaluation records that:

- cite missing evidence paths;
- reference unselected `architecture_context` facets;
- reference unknown or unselected `architecture_capabilities`;
- mark a proposal as applied;
- set `requires_human_approval=false`.

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
