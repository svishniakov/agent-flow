# AgentFlow

<p align="center">
  <a href="README.md">RU</a> · <a href="README.en.md"><b>EN</b></a>
</p>

<p align="center">
  <img src="docs/assets/readme/agent-flow-hero.svg" alt="AgentFlow: orchestrator, shared memory, agents, verification" width="100%">
</p>

<h1 align="center">Skill for controlled agent work</h1>

<p align="center">
  shared memory · orchestrator · 25 roles · model/reasoning per agent · skills per role
</p>

<p align="center">
  <img alt="25 roles" src="https://img.shields.io/badge/roles-25-cc7d5e?style=flat-square">
  <img alt="138 skills" src="https://img.shields.io/badge/skills-138-2d2d2b?style=flat-square">
  <img alt="explicit prefix" src="https://img.shields.io/badge/start-explicit%20prefix-cc7d5e?style=flat-square">
  <img alt="license Apache 2.0" src="https://img.shields.io/badge/license-Apache--2.0-2d2d2b?style=flat-square">
</p>

## Contract

AgentFlow runs only when the request starts with:

```text
Agent Flow <task>
$agent-flow <task>
agent-flow <task>
```

Without the prefix, the skill is not used.

The prefix does not authorize subagents. Delegation needs a separate explicit request in the same task: `use subagents`, `spawn a subagent`, `multi-agent review`.

## Contents

| Component | Purpose |
| --- | --- |
| `.agent-work/tasks/` | shared memory: todo, lessons, implementation notes, checks |
| `agents/*.md` | subagent roles and machine-readable config |
| `model` | role model |
| `reasoning_effort` | role reasoning level |
| `escalation_triggers` | conditions for stronger config |
| `skills` | skills required by a role |
| `registries/agent-skills.json` | install metadata for role skills |
| `references/` | budgets, flows, delegation, traceable runs, Definition of Done |
| `scripts/` | resolver, validators, trace helpers, dependency checker |

## Roles

`agents/` contains 25 roles. Each role has narrow ownership, its own skills, and its own model/reasoning settings.

Example roles:

- `architect`
- `reviewer`
- `qa-verifier`
- `researcher`
- `frontend-worker`
- `backend-worker`
- `typescript-worker`
- `python-worker`
- `ios-worker`
- `visual-qa`

## Install

```bash
git clone https://github.com/svishniakov/agent-flow.git ~/.codex/skills/agent-flow
python3 ~/.codex/skills/agent-flow/scripts/check-agent-deps.py --post-install
```

`--post-install` shows missing skills and recommends the `core` set. It does not install silently.

## Checks

```bash
python3 scripts/check-agent-deps.py
python3 scripts/check-agent-deps.py --scope core
python3 scripts/check-agent-deps.py --scope role:typescript-worker
python3 scripts/check-agent-deps.py --strict
```

Skill install plan:

```bash
python3 scripts/check-agent-deps.py --scope core --install-plan
python3 scripts/check-agent-deps.py --scope full --install-plan --target project
python3 scripts/check-agent-deps.py --scope core --guided-install
```

Repo checks:

```bash
python3 -m py_compile scripts/*.py
python3 scripts/validate-agent-config.py
python3 scripts/validate-agent-skill-registry.py
python3 scripts/check-agent-deps.py --scope core
python3 scripts/init-run.py --help
python3 scripts/append-timeline.py --help
python3 scripts/record-agent-trace.py --help
python3 scripts/validate-run.py --help
```

## Examples

Solo:

```text
Agent Flow Read the repository, project memory, and README. Return active tasks, blockers, next actions, and risks. Do not change anything.
```

Bugfix:

```text
Agent Flow Investigate this bug: <description>. Find the cause, make the smallest fix, run checks, and return changed files and risks.
```

Subagents:

```text
Agent Flow Use subagents for an independent review of the current implementation. Split work by role and merge findings into one result.
```

## License

Apache 2.0. See [LICENSE](LICENSE).
