# Agent Flow: skill overview

Document status: done

Agent Flow is a Codex skill that helps move a complex task from the user request to a verified result. It does not activate automatically. The user must put one of these invocation markers anywhere in the prompt: `Agent Flow`, `AgentFlow`, `$agent-flow`, or `agent-flow`.

The main idea is simple: the user invokes Agent Flow with one explicit marker, and the orchestrator chooses the right route. It keeps the task bounded, checks active project work for dependencies, gathers the needed context, switches internal budgets under the hood, decides whether subagents are useful, does the work, and verifies the result before the final response.

Supported target is Codex with OpenAI models. Claude Code, Cursor, Hermes, and other hosts are outside this package scope.

## Purpose

Agent Flow is useful when a task is larger than one short answer or one mechanical edit. It helps to:

- parse the request and choose the right workflow without making the user pick a mode;
- avoid extra process when direct work is enough;
- read project memory and local rules before changes;
- stop a new feature when active project work can affect it;
- avoid touching infrastructure without need;
- separate ordinary solo work from tasks with trace artifacts;
- prepare a delegation packet for subagents when the task shape justifies delegation;
- apply Architecture Matrix facets when product type, application surface, stack, risk, or verification constraints change the architecture contract;
- keep evidence: checks, handoffs, timeline, risks, and final status;
- avoid claiming the work is done without fresh verification.

## Activation

The skill activates when the latest user message contains one of these markers:

- `Agent Flow <task>`
- `AgentFlow <task>`
- `$agent-flow <task>`
- `agent-flow <task>`

If the marker is absent, the request stays outside Agent Flow. Codex then works in normal solo mode, without trace artifacts and without automatic routing through this skill.

## Core rules

- Agent Flow is not a preflight for every request.
- A project `AGENTS.md` cannot force Agent Flow on.
- Do not run a separate brainstorming flow before Agent Flow.
- Users do not choose budgets and do not need to explicitly request subagents.
- The orchestrator decides from context whether to keep the task solo or use subagents.
- Before new work, the orchestrator verifies actual dependencies and later evidence. Old `in_progress` or `blocked` status alone does not stop a new task.
- Prepare final statuses of related documents before hashing and QA/reviewer. After current acceptance, any requested commit and final validation, close task records in every affected repository.
- Trace artifacts are created only when justified by risk, an internal routing decision, or a direct user request.
- `.agent-work/` must not be included in product commits.

## Dependency Gate

Dependency Gate protects separate feature sessions from stepping on each other. At the start of new feature work, the orchestrator reads project memory and checks active tasks marked `in_progress` or `blocked`.

Before blocking, compare old scope, remaining requirements and blocker with the new request. Look up later evidence by task/plan ID across linked repositories, verify commits and checks, and inspect linked session state when available. A SHA or finished session alone does not prove completion; unavailable session listing alone does not block work.

Correct a confirmed completed record narrowly, even if old boxes are unchecked; for `blocked`, also confirm removal of its cause. Record sources, date and reason without a new run or repeat acceptance of old implementation. Repeat intake must not duplicate closure. Unproven old work keeps its truthful status while independent new work proceeds.

Stop only the part with confirmed ongoing changes to a necessary shared file/contract, or a specific required result still unproven after available checks. Name concrete evidence and the missing fact before asking the user. Continue authorized independent work.

The user can still continue by explicitly accepting the recorded risk. Another option is to merge the work into one coordinated Agent Flow run. Internal lanes inside one Agent Flow run are not blocked by this gate.

## Document and task completion

At intake, list related documents in every affected repository, including the source plan. Finish scope/checks, prepare final statuses before hashing and QA/reviewer, and include complete document bytes in `result_files`. `Document status: done` means the text is complete. A separate `Implementation status: not_started`, `in_progress`, `blocked` or `done` describes execution; preserve ADR decision states. A plan commit does not complete its future implementation.

Acceptance covers status fields; editing them later requires renewed acceptance. For an authorized commit, inspect staged statuses, create scoped commits, and inspect documents with `git show` in each repository. Record actual SHA in memory/timeline, run fresh final validation, then close related task records. Commit or validator failure keeps delivery incomplete. Without a commit request, completion does not require one. Unrelated documents and historical runs stay unchanged. Details: [Definition of Done](../../references/definition-of-done.md).

## Internal flows

Agent Flow chooses the smallest suitable flow:

- `quick-check-flow` - one short answer, command, or check;
- `bugfix-flow` - reproduction, fix, regression check;
- `feature-flow` - bounded implementation of a new capability;
- `docs-flow` - documentation, PRD, README, specification;
- `design-flow` - UI/UX, design documents, Pencil/Figma/Stitch;
- `ci-release-flow` - CI, release, deploy, external services;
- `review-flow` - review of code, plan, or readiness;
- `initiative-flow` - a small path from idea to finished result.

## Traceable runs

For high-risk work, release gates, CI/deploy, external services, or a direct user request, Agent Flow can create a run directory in `.agent-work/runs/YYYY-MM-DD-task-slug/`.

It usually contains:

- `manifest.md`;
- `context.md`;
- `route.md`;
- `plan.md`;
- `checks/`;
- `handoffs/`;
- `artifacts/`;
- `timeline.jsonl`;
- `final.md`.

The timeline records the real order of work. If a product commit was created, the commit event is recorded after successful checks and before the final event. Trace artifacts remain local memory and are not added to the product commit unless the user explicitly asks for that.

## Lane Sharding

For large PRDs or release work, Agent Flow can split work into implementation, integration, architecture, QA, and review lanes. This is an internal workflow pattern, not a public user mode.

In a traceable run, `lane-map.json` becomes the machine-readable source of truth. Markdown files such as `checks/coverage-matrix.md` remain human-readable summaries. Before final handoff, `validate-run.py` checks `lane-map.json` and rejects `Verdict: ship` when a critical lane has no evidence or valid replacement lane.

Schema v2 requires `budget`. Release runs and standard runs with two or more worker lanes must set `architecture_contract_required=true`. The critical `architecture` lane must pass with handoff, evidence, and the required contract sections before QA or review can pass.

When product or stack constraints matter, the orchestrator selects Architecture Matrix facets from `references/architecture-matrix.md` before the architecture lane writes its contract. The architect cites those facets and turns their constraints into concrete boundaries, forbidden changes, QA gates, and reviewer checklist items.

## Subagents

The repository contains bundled role files in `agents/<role>.md` and stable identities in `agents/agent-identities.json`. These files let others clone the repository from GitHub and use Agent Flow without author-local paths.

Subagents are used only when both conditions are true:

- the orchestrator decided they are needed for verification, research, review, or parallel work;
- the Codex environment provides a subagent/spawn tool.

If the tool is unavailable, role files can be used as a solo checklist or role lane, but that is not subagent execution. The final answer should state that downgrade.

Subagent dependencies are described in `registries/agent-skills.json`. Role files in `agents/*.md` state which skills a role needs; the registry stores tiers, roles, target paths, prompts, and install instructions.

Role frontmatter intentionally uses a narrow format: one-line `key: value` entries and inline lists such as `[Read, Write]`. Full-line comments and YAML-like inline comments are allowed; multiline YAML remains unsupported, which keeps validation predictable.

Install Agent Flow through Skills CLI:

```bash
npx skills add https://github.com/svishniakov/agent-flow
```

For an explicit Codex/global install:

```bash
npx skills add https://github.com/svishniakov/agent-flow -a codex -g
python3 ~/.agents/skills/agent-flow/scripts/check-agent-deps.py --post-install
```

The post-install check reports missing `core` and `full` skills. It does not
install extra skills:

- `core` means skills without which a role loses its main capability: browser, QA, test, find-skills, language/runtime/toolchain, and key design/plugin skills;
- `full` means every skill referenced by `agents/*.md`, including niche, paid, or plugin-gated dependencies.

Target selects where the skill should be installed:

- `global` means the user's global environment, usually `~/.agents/skills`;
- `project` means the current project, usually `.agents/skills`.

There is no silent dependency install. `--guided-install` is an explicit manual
mode: it executes only allowlisted `git`/`local` registry commands and only
after the user confirms with `yes`. `plugin`, `prompt`, and `manual` entries are
not executed: the checker prints instructions, the official prompt, or a note to
enable a plugin.

Skills CLI install plus the post-install check is the install health path. Full
repository validation is a developer check and may need extra dependencies.

## Update

For Skills CLI installs:

```bash
npx skills update agent-flow -g
```

Use the bundled updater only for legacy git/symlink installs:

```bash
python3 ~/.agents/skills/agent-flow/scripts/update-agent-flow-skill.py --dry-run
python3 ~/.agents/skills/agent-flow/scripts/update-agent-flow-skill.py
```

`--dry-run` fetches the remote and reports whether the checkout is clean, behind, ahead, or diverged. A real update only fast-forwards a clean checkout. To discard local edits or divergent commits, rerun with explicit `--overwrite`.

By default the updater runs `scripts/check-agent-deps.py --scope core`, which
uses only the installed skill dependency checker. Pass `--full-check` to run
`scripts/check-all.py` after update. Pass `--skip-check` to skip the
post-update check.

## Checks

After changing the repository, install CodeGraph parser dependencies before the
full developer suite:

```bash
python3 -m pip install -r skills/agent-flow/requirements-codegraph.txt
python3 scripts/check-all.py
python3 scripts/check-agent-deps.py --scope core
python3 scripts/validate-role-catalog.py
```

`check-all.py` should finish with `PASS all Agent Flow checks`.

To check runtime files for Russian text:

```bash
rg -n '\p{Cyrillic}' skills/agent-flow/SKILL.md skills/agent-flow/agents skills/agent-flow/references skills/agent-flow/scripts skills/agent-flow/LICENSE
```

Russian text is allowed only in `README.ru.md` and `skills/agent-flow/docs/ru/`.
