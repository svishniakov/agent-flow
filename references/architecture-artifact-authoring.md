# Architecture Artifact Authoring Automation

Architecture Artifact Authoring Automation makes architecture artifacts agent-authored by default. The human does not fill the Architecture Design Brief, Architecture Contract, worker compliance, QA invariants, or reviewer sections by hand.

## Init Run

Use `scripts/init-run.py --architecture-gate` only with `--with-lanes`, `--budget`, `--architecture-context-json`, `--architecture-capabilities`, and any worker lanes needed for the run.

Example:

```bash
python3 scripts/init-run.py \
  --repo <repo> \
  --slug <slug> \
  --with-lanes \
  --architecture-gate \
  --budget standard \
  --architecture-context-json '<json>' \
  --architecture-capabilities backend-service-architecture,modular-monolith-architecture \
  --worker-lane worker-a:implementation:typescript-worker
```

The command creates `lane-map.json` schema v2 plus:

- `handoffs/architecture-design.md`;
- `handoffs/architecture-contract.md`;
- `verification-readiness.json`;
- `claim-evidence.json`;
- `handoffs/verification-readiness.md`;
- one worker handoff per `--worker-lane`;
- `handoffs/qa-behavior.md`;
- `handoffs/review-contract.md`;
- matching `checks/*.md` evidence files.

## Agent Placeholder

Generated architecture templates use exactly one placeholder marker:

```md
TODO(agent):
```

The marker means the responsible agent must replace the text with run-specific evidence, decisions, or verification notes before a positive final verdict.

## Runtime Rule

When `architecture_contract_required=true`, `validate-run.py` blocks `ship` and `pass-with-risks` if any architecture lane artifact still contains `TODO(agent):`.

The scan covers Architecture Design Brief, Architecture Contract, worker handoffs, QA handoff, reviewer handoff, and lane evidence files referenced from `lane-map.json`.

`blocked` and `fail` may keep placeholders so the run can honestly end when design or implementation did not reach an approved state.

## Role Ownership

- Orchestrator chooses `architecture_context` and `architecture_capabilities`, creates the skeleton with `--architecture-gate`, and keeps lanes blocked until placeholders are gone.
- Architect fills Architecture Design Brief and Architecture Contract before workers start.
- QA fills the pre-worker Verification Readiness Gate artifact, including `verification_readiness`, `needs-approval`, `paused-blocked`, `approval_requests`, `approval_executions`, `resume_phrase`, and later `Verification Gate Results` coverage.
- Workers fill their own `Architecture Compliance` and `Engineering Simplicity` handoff sections, then update lane-map `architecture_compliance.engineering_simplicity`. They fix now if fixable; Simplicity Gate is not a reporting gate.
- QA fills `Architecture Invariants` from selected `risk_gates`, `verification_gates`, and contract QA gates.
- QA and reviewer fill Claim Evidence Gate data in `claim-evidence.json`: each `Claim Evidence` id gets an `owner_lane`, `supported` or `gap`, subjects, evidence paths, and literal `markers`.
- Reviewer fills `Architecture Matrix Mismatches` and `Contract Drift` for every selected facet and capability; `Contract Drift` must cover Engineering Simplicity, reject reporting-only closure, and mention fixed worker lane ids.

No role should ask the human to write these sections manually.
