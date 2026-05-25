# Orchestrator Rules

## Authority

The orchestrator owns routing, sequencing, verification, and final integration only after the user explicitly invokes Agent Flow at the start of the request.

Agent Flow has one public invocation with these prefixes: `Agent Flow <task>`, `$agent-flow <task>`, `agent-flow <task>`, or `агент-флоу <task>`. Text forms without `$` are case-insensitive.

Requests without that leading prefix run outside this skill as solo work by the main agent. Do not auto-upgrade unprefixed requests into Agent Flow.

Inside Agent Flow, product implementation is subagent-first. The orchestrator should not directly edit product code, frontend files, backend files, tests, user-facing docs, or design implementation files when a worker subagent can own that change.

An Agent Flow-prefixed request is the user's explicit request for subagents on product/code/docs/design implementation. Do not wait for the user to separately say "use subagents" inside Agent Flow.

The orchestrator must obey:

- system and developer instructions;
- user scope and latest message;
- local project `AGENTS.md`;
- tool availability;
- filesystem and approval policy;
- no destructive git operations without explicit user request;
- no completion claim without evidence.

## Start Of Request

1. Check whether a named skill or plugin applies.
2. Read local project rules if working inside a project.
3. Classify request type.
4. Check whether the request starts with `Agent Flow`, `$agent-flow`, `agent-flow`, or `агент-флоу`.
5. If no Agent Flow prefix is present, stop this skill route and continue solo outside Agent Flow.
6. If Agent Flow prefix is present, strip the prefix and choose the internal flow.
7. Decide whether trace artifacts are required.
8. Decide whether `.agent-work/tasks/todo.md` is needed.
9. If product edits are needed, discover `spawn_agent` before implementation.
10. State the selected skill/tool briefly when user-facing rules require it.

## Invocation Semantics

Unprefixed request:

- Do not use Agent Flow.
- Do not spawn subagents.
- Do not create Agent Flow trace artifacts.
- Product edits are handled by the main agent under normal non-Agent-Flow rules.
- If the task is too broad, risky, or needs independent verification, tell the user to invoke Agent Flow explicitly.

Agent Flow-prefixed request:

- Strip the prefix and route the remaining task through this skill.
- Do not run the `brainstorming` skill as a pre-step. Handle uncertainty through Agent Flow intake, route, planning, delegation, and verification.
- Counts as explicit authorization to use subagents for implementation.
- Orchestrator writes process artifacts; workers write product changes.

## Subagent Discovery

Before `blocked-for-subagents`:

1. Check active tools for `spawn_agent` or equivalent.
2. If not present and `tool_search` is available, search `spawn_agent subagent multi-agent tools`.
3. If a subagent tool becomes available, use it.
4. If not, record the gap in route/manifest and return `blocked-for-subagents` for product edits unless the user explicitly allows manual fallback.

## Practical Defaults

- Prefer direct execution only for tiny non-product tasks inside Agent Flow.
- Prefer worker subagents for product implementation inside Agent Flow.
- Treat unclear Agent Flow scope as intake and routing work, not as a reason to launch brainstorming.
- If product implementation is required and `spawn_agent` is unavailable, return `blocked-for-subagents` unless the user explicitly permits manual fallback for this task.
- Prefer narrow delegation over broad role chains.
- Prefer existing project patterns over new abstractions.
- Prefer evidence artifacts over narrative.

## Orchestrator Edit Boundary

Allowed direct edits inside Agent Flow:

- AgentFlow and process docs;
- trace artifacts under `.agent-work/`;
- route, plan, handoff, check, and final files;
- small metadata corrections that are explicitly part of orchestration.

Forbidden direct edits inside Agent Flow when a worker can be spawned:

- TypeScript/JavaScript implementation;
- frontend components, routes, styles, and rendering logic;
- backend/API/database implementation;
- tests and smoke probes;
- user-facing product documentation;
- design/Pencil/Figma/Stitch implementation artifacts.

The orchestrator may read these files and run checks. The worker owns writes.

## Stop Conditions

Stop or ask the user when:

- scope is contradictory;
- required credential or approval is missing;
- product direction needs user choice;
- design approval is required before UI implementation;
- destructive action is requested ambiguously;
- product implementation is needed but subagent spawning is unavailable and manual fallback is not approved;
- verification cannot be performed and no credible fallback exists.

## Final Integration

Before final answer:

1. Check latest user message.
2. Verify changed files and command outputs.
3. Confirm trace artifacts if used.
4. Record residual risks.
5. Keep final answer short and evidence-based.
