# Agent Flow Internal Flows

Use internal flows inside `orchestrated`. They are not public modes.

## Flow Selection

Choose the smallest flow that can produce a verified result.

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
6. Product changes by bounded workers or main agent.
7. QA and verification.
8. AI-slop check for user-facing surfaces.
9. Reviewer pass.
10. Final handoff with evidence and risks.

Ask approval only when the next step would lock a product/design direction, spend money, change external state, handle secrets, deploy, or expand scope.

## Scope Growth

Upgrade from `lite` to `orchestrated` when any of these appears:

- more than one related step;
- product/code/doc change with regression risk;
- external service or credential;
- subagent-worthy independent work;
- user-facing text or UI;
- unclear acceptance criteria.

If `/solo` is too risky for one agent, return `blocked-for-solo` with the smallest safe orchestrated route.
