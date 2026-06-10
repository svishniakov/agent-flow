# Agent Flow Internal Flows

Use internal flows only after the user explicitly invokes Agent Flow at the start of the request. They are not public modes.

Do not use this file as preflight for unprefixed requests. Agent Flow starts only from the user-visible leading prefix.

Agent Flow does not use the separate `brainstorming` skill. If scope is unclear, handle that through intake, route, and the selected internal flow.

## Flow Selection

Choose the smallest flow that can produce a verified result.

Read `workflow-patterns.md` when the selected flow needs a repeatable internal
shape: fan-out, adversarial verification, tournament, quarantine, or loop. These
patterns refine a flow; they are not separate public modes.

| Flow | Use when | Typical output |
| --- | --- | --- |
| `quick-check-flow` | One command, short lookup, tiny explanation | Answer, command output, or brief note |
| `bugfix-flow` | Bug report, failing test, regression, broken workflow | Repro, root cause, fix, regression check |
| `feature-flow` | New product/code capability with bounded scope | Plan, implementation, tests, summary |
| `docs-flow` | PRD, specs, README, design docs, process docs | Draft or updated docs with review notes |
| `design-flow` | UI, UX, Pencil, Figma, screenshots, visual assets | Approved design source, implementation notes, visual QA |
| `ci-release-flow` | CI, deploy, release, package, secret-sensitive pipeline | Logs, fix, verification, residual risk |
| `review-flow` | Code/docs/design review requested by user | Findings first, severity order, file/line refs |
| `initiative-flow` | Small idea must become a finished project outcome | Discovery through final handoff |

## Initiative Flow

Use `initiative-flow` when the user brings a rough idea and expects the agent team to drive it to a complete result.

Sequence:

1. Intake: goal, audience, constraints, success criteria.
2. Product shaping: PRD or brief, out-of-scope, acceptance criteria.
3. Architecture: system boundaries, data flow, risks.
4. Design route if UI/UX is involved.
5. Implementation plan.
6. Product changes by the main agent by default for `light`; bounded workers are allowed for `standard` and `release` when they add value.
7. QA and verification scaled to the selected budget.
8. AI-slop checklist for user-facing surfaces.
9. Reviewer pass for release/high-risk work or explicit request; architecture-sensitive review needs an architect-owned contract first.
10. Final handoff with evidence and risks.

If the user explicitly asks for a full document package, use at least `standard` budget unless scope is tiny. The package should include PRD or brief, architecture notes, design route when UI/UX is involved, implementation plan, checks, and final handoff. Do not create full trace artifacts for `light` budget unless the user explicitly asks for durable artifacts.

Ask approval only when the next step would lock a product/design direction, spend money, change external state, handle secrets, deploy, or expand scope.

## Scope Growth

Unprefixed solo requests do not auto-upgrade into Agent Flow. If any of these appears during solo work, handle it solo when reasonable. If it is too broad or risky, ask the user whether to invoke Agent Flow:

- more than one related step;
- product/code/doc change with regression risk;
- external service or credential;
- independent work that would benefit from subagents;
- user-facing text or UI;
- unclear acceptance criteria.

Inside an Agent Flow-prefixed request, choose the smallest internal flow that covers the expanded scope.

Inside Agent Flow, choose subagent variants only when the selected budget is
`standard` or `release`, or when the user explicitly asked for subagents. Do not
infer authorization from pattern choice for `light` work.
