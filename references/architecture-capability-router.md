# Architecture Capability Router

Architecture Capability Router maps selected Architecture Matrix facets to
architecture capabilities. It is an operational routing layer, not a project
profile catalog and not a skill catalog.

Flow:

```text
architecture_context -> architecture_capabilities -> Architecture Design Brief -> Architecture Contract -> QA/reviewer control
```

## Source Of Truth

Canonical capability ids live in `registries/architecture-capabilities.json`.
The registry maps each capability to:

- `matrix_facets`: Architecture Matrix facet ids covered by the capability;
- `recommended_skills`: soft skill bindings for dependency checks and role
  preparation;
- `contract_sections`: Architecture Contract sections where the capability
  should affect boundaries, public contracts, QA gates, or reviewer checks.

Soft Skill Binding means skills are soft bindings. Missing or stale skill links
fail registry quality checks, but do not block an individual traceable run in
`validate-run.py`.
Architecture Matrix remains the stable taxonomy; do not add skill hints or
capability routing sections to `references/architecture-matrix.md`.

## Lane Map Shape

When `architecture_contract_required=true`, lane-map schema v2 also records:

```json
"architecture_capabilities": {
  "selected": ["backend-service-architecture", "modular-monolith-architecture"],
  "notes": "Short reason why these capabilities cover selected architecture_context."
}
```

Rules:

- `selected` is a non-empty array of known capability ids;
- `notes` is a non-empty string;
- selected capabilities must cover every selected `architecture_context` facet;
- Architecture Design Brief `Execution Plan` includes every selected capability
  id;
- Architecture Contract `Selected Architecture` includes every selected
  capability id;
- reviewer `Architecture Matrix Mismatches` and `Contract Drift` cover every
  selected capability id when reviewer control applies.

Engineering Simplicity Gate uses the selected `architecture_capabilities` only
as citation context for retained dependency or abstraction. Do not add a
separate simplicity capability, Matrix facet, or registry entry for it.

## Role Responsibilities

The orchestrator selects `architecture_context`, then selects the smallest
capability set that covers it. Do not select capabilities from repository names,
customer names, local paths, or one-off labels.

The architect uses selected capabilities to shape `Execution Plan`, module
boundaries, public contracts, forbidden changes, QA gates, reviewer checklist,
and stop conditions.

QA verifies behavior and architecture invariants produced by selected
capabilities. Reviewer reports capability mismatches and contract drift
explicitly, even when none are found.
