# Agent Flow

[Русская версия](README.ru.md)

Agent Flow is a development skill for Codex Desktop and CLI. The main agent breaks down the task, selects specialists, coordinates changes and verifies the result. The framework works across projects; each project's documentation supplies its product rules.

## Usage

```text
Agent Flow Implement the plan in docs/implementation-plan.md and verify the result.
```

Include `Agent Flow` in the request. `AgentFlow`, `$agent-flow` and `agent-flow` also work. Without a marker, the skill stays inactive. The main agent selects the team and verification scope for the task.

## Main features

| Feature | Purpose |
| --- | --- |
| Planning and coordination | Break down work, assign owners and integrate results |
| Project context | Preserve decisions, active tasks and verification results between stages |
| Architecture review | Agree on module boundaries and constraints before complex changes |
| Implementation verification | Check acceptance criteria, tests and independent reviewer findings |
| Failure recovery | Diagnose failures and retry with a corrected task |
| Scope control | Identify unnecessary dependencies, abstractions and unrelated changes |
| CodeGraph | Find code dependencies, affected areas and related tests |
| Project lessons | Preserve verified findings for later tasks |

Workers receive a bounded task, allowed changes and acceptance criteria. An independent reviewer checks changes before completion. The existing run validator checks delegation results and required verification.

## Agents and models

The profile contains 27 roles: 26 use Astra; reviewer uses Sol. Start the main agent separately with `gpt-6-astra/high`. The `orchestrator` role assists the main agent.

Reasoning controls reasoning depth. The table lists the default and escalation levels. Triggers in each role file determine escalation. The exact model ID stays fixed within a role, including retries. Different roles can use different models.

| Agent | Responsibility | Model | Reasoning | On escalation |
| --- | --- | --- | --- | --- |
| Main agent | Owns the task and accepts the result | `gpt-6-astra` | `high` | Session setting |
| [ai-slops-hunter](skills/agent-flow/agents/ai-slops-hunter.md) | Removes unnecessary code and formulaic text | `gpt-6-astra` | `medium` | `high` |
| [architect](skills/agent-flow/agents/architect.md) | Defines architecture, module boundaries and implementation plans | `gpt-6-astra` | `high` | `xhigh` |
| [backend-worker](skills/agent-flow/agents/backend-worker.md) | Implements server logic, APIs and data access | `gpt-6-astra` | `medium` | `high` |
| [bun-worker](skills/agent-flow/agents/bun-worker.md) | Handles Bun code, dependencies, builds and tests | `gpt-6-astra` | `medium` | `high` |
| [design-asset-generator](skills/agent-flow/agents/design-asset-generator.md) | Creates images and visual assets | `gpt-6-astra` | `medium` | `high` |
| [design-documenter](skills/agent-flow/agents/design-documenter.md) | Maintains DESIGN.md and design requirements | `gpt-6-astra` | `medium` | `high` |
| [design-orchestrator](skills/agent-flow/agents/design-orchestrator.md) | Coordinates design and implementation handoff | `gpt-6-astra` | `high` | `xhigh` |
| [documenter](skills/agent-flow/agents/documenter.md) | Writes plans, specifications, READMEs and documentation | `gpt-6-astra` | `high` | `xhigh` |
| [frontend-worker](skills/agent-flow/agents/frontend-worker.md) | Implements interfaces, styles and client state | `gpt-6-astra` | `medium` | `high` |
| [golang-worker](skills/agent-flow/agents/golang-worker.md) | Implements Go services, CLIs and tests | `gpt-6-astra` | `medium` | `high` |
| [ios-worker](skills/agent-flow/agents/ios-worker.md) | Implements SwiftUI and Apple platform features | `gpt-6-astra` | `medium` | `high` |
| [marketing-growth-strategist](skills/agent-flow/agents/marketing-growth-strategist.md) | Plans positioning, launches and product growth | `gpt-6-astra` | `high` | `xhigh` |
| [orchestrator](skills/agent-flow/agents/orchestrator.md) | Assists the main agent with coordination | `gpt-6-astra` | `medium` | `high` |
| [pencil-designer](skills/agent-flow/agents/pencil-designer.md) | Creates and checks Pencil designs | `gpt-6-astra` | `medium` | `high` |
| [product-manager](skills/agent-flow/agents/product-manager.md) | Defines the problem, value, scope and acceptance criteria | `gpt-6-astra` | `high` | `xhigh` |
| [python-worker](skills/agent-flow/agents/python-worker.md) | Implements Python code, CLIs and data processing | `gpt-6-astra` | `medium` | `high` |
| [qa-verifier](skills/agent-flow/agents/qa-verifier.md) | Reproduces bugs and verifies implementation | `gpt-6-astra` | `high` | `xhigh` |
| [rag-retrieval-engineer](skills/agent-flow/agents/rag-retrieval-engineer.md) | Designs retrieval, RAG and retrieval quality checks | `gpt-6-astra` | `high` | `xhigh` |
| [researcher](skills/agent-flow/agents/researcher.md) | Researches documentation, APIs and existing solutions | `gpt-6-astra` | `medium` | `high` |
| [reviewer](skills/agent-flow/agents/reviewer.md) | Independently reviews results, regressions and test coverage | `gpt-5.6-sol` | `high` | `xhigh` |
| [senior-qa-verifier](skills/agent-flow/agents/senior-qa-verifier.md) | Investigates verification failures and refines test cases | `gpt-6-astra` | `high` | `xhigh` |
| [supervising-architect](skills/agent-flow/agents/supervising-architect.md) | Provides a second architecture review for unresolved blockers | `gpt-6-astra` | `xhigh` | `xhigh` |
| [typescript-worker](skills/agent-flow/agents/typescript-worker.md) | Implements TypeScript/JavaScript code and tests | `gpt-6-astra` | `medium` | `high` |
| [ui-reference-researcher](skills/agent-flow/agents/ui-reference-researcher.md) | Researches interface references and design systems | `gpt-6-astra` | `medium` | `high` |
| [ui-ux-design-director](skills/agent-flow/agents/ui-ux-design-director.md) | Chooses the interface concept and visual direction | `gpt-6-astra` | `high` | `xhigh` |
| [ui-ux-designer](skills/agent-flow/agents/ui-ux-designer.md) | Designs screens, flows and prototypes | `gpt-6-astra` | `medium` | `high` |
| [visual-qa](skills/agent-flow/agents/visual-qa.md) | Checks layouts, responsiveness and DESIGN.md conformance | `gpt-6-astra` | `high` | `xhigh` |

The main agent has no separate role file with an escalation setting. Its starting level, `high`, is defined in [SKILL.md](skills/agent-flow/SKILL.md). Other agents take their settings from the [role files](skills/agent-flow/agents); instructions from those files are passed into Codex configurations.

## Latest changes

Revision `6f6c078`, September 9, 2026:

- Updated models and prompts for the Astra/Sol profile.
- Set `documenter` and `qa-verifier` to default `high`, escalating to `xhigh`.
- Added a guard against changing the model within a role. Reasoning escalation remains available.
- Restored the workflow from `3d95caf`: routing, delegation, verification and recovery attempts.
- Removed the added mandatory `model-settings.json` and capture requirements for `thread/start` and `turn/start`. Existing work logs remain.

[Current migration decision](skills/agent-flow/docs/implementation/impl-007-astra-sol-rollout.md).

## Installation and configuration

Global installation for Codex:

```bash
npx skills add https://github.com/svishniakov/agent-flow -a codex -g
python3 ~/.agents/skills/agent-flow/scripts/check-agent-deps.py --post-install
python3 ~/.agents/skills/agent-flow/scripts/sync-codex-agent-config.py --output-dir ~/.codex/agents
```

The dependency check reports missing skills. The generator creates or updates 27 role configurations. Run it again after changing role files.

The skill package lives in `skills/agent-flow/`. A local installation symlink must point to that directory.

## Repository checks

Run from the repository root:

```bash
python3 scripts/validate-agent-config.py
python3 scripts/validate-role-catalog.py
python3 scripts/sync-codex-agent-config.py --output-dir ~/.codex/agents --check
python3 scripts/check-all.py
```

The full suite requires dependencies from [requirements-codegraph.txt](skills/agent-flow/requirements-codegraph.txt). Expected result: `PASS all Agent Flow checks`.

## Documentation

- [Skill instructions](skills/agent-flow/SKILL.md)
- [Delegation rules](skills/agent-flow/references/delegation.md)
- [Definition of done](skills/agent-flow/references/definition-of-done.md)
- [Work logs and validation](skills/agent-flow/references/traceable-runs.md)
- [CodeGraph](skills/agent-flow/docs/adr/adr-001-codegraph.md)
- [License](LICENSE)
