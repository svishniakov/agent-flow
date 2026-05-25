# Delegation

Use subagents for product implementation only inside an explicitly invoked Agent Flow request. The orchestrator routes, briefs, reviews, and verifies; workers write product changes.

An Agent Flow-prefixed request is standing explicit user authorization for subagents on product/code/docs/design implementation. This applies even if the current user message does not repeat "use subagents".

Unprefixed requests are solo work. Do not launch subagents for them.

If `spawn_agent` is unavailable and product changes are required, return `blocked-for-subagents` unless the user explicitly permits manual fallback for that task.

Before treating `spawn_agent` as unavailable, discover it through active tools and `tool_search` when available.

## Budget Gate

For product/code/docs/design implementation inside Agent Flow, delegation is mandatory when `spawn_agent` is available.

For optional sidecar work, delegate when all are true:

- task is independent;
- ownership is narrow;
- context can be packaged clearly;
- output can be verified;
- result does not block immediate orchestrator progress;
- cost is justified by risk or parallelism.

Do not delegate ritual roles.

## Product Ownership

Assign product edits to workers, not the orchestrator:

- TypeScript/JavaScript: `typescript-worker`, `frontend-worker`, or `backend-worker`.
- UI implementation: `frontend-worker` after design gate.
- API/database/service work: `backend-worker`.
- Tests and smoke probes: worker or QA verifier with explicit ownership.
- User-facing docs: `documenter` or docs worker.

Use disjoint write sets when multiple workers run in parallel. Tell every worker they are not alone in the codebase and must not revert edits made by others.

## Delegation Packet

Every packet includes:

- role;
- stable identity if available;
- goal;
- 3-7 sentence task context;
- project repo;
- artifact root;
- run directory;
- handoff path;
- files to read first;
- allowed changes;
- forbidden changes;
- relevant skills/plugins;
- expected artifact;
- verification commands;
- Definition of Done gates;
- artifact registration needs;
- no `.agent-work/` commit rule;
- handoff format.

## Stable Identities

If `/Users/ucnlejumper/.codex/agent-identities.json` exists, look up the role before launching a subagent. If identity is missing, use role name as temporary slug and record the gap in route or manifest.

If `/Users/ucnlejumper/.codex/agents/<role>.md` exists, read it before launching a subagent and include the role instructions in the packet.

## Handoff Format

Subagent handoff must include:

- role and stable identity;
- what was completed;
- files read and changed;
- commands run and important outputs;
- what is not done;
- decisions made;
- residual risks;
- DoD status: `pass`, `pass-with-risks`, `fail`, or `blocked`;
- artifacts created or updated;
- what the next actor should read.

When a run directory exists, save handoff to `handoffs/<role>.md`.

## Per-Agent Trace Contract

When a run directory exists, every delegated subagent must have timeline presence
owned by that role. Use `scripts/record-agent-trace.py` instead of
`scripts/append-timeline.py` for subagent events.

The orchestrator records a delegation event before or immediately after sending
the handoff packet. The worker records at least one terminal event when handing
work back: `handoff`, `blocked`, or `fail`. Longer work may add intermediate
`active` or `verification` events.

Each call writes the same event to the run-level `timeline.jsonl` and to
`agents/<role>/trace.jsonl`. The helper also creates
`artifacts/agents/<role>/` and indexes repeated `--artifact` paths in
`artifacts.json` with subagent owner metadata.

This is mandatory for delegated subagents. A handoff file without a matching
role-owned timeline event is an incomplete traceable run.

## Integration

The orchestrator must verify subagent work independently. Agent reports are not evidence by themselves.
