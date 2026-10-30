# Agent Flow

[Русская версия](README.ru.md)

Agent Flow is a development skill for Codex Desktop and CLI. It breaks tasks into steps, assigns specialists when needed, coordinates changes and checks the result.

## Usage

Include `Agent Flow` in your request:

```text
Agent Flow Implement the agreed plan and verify the result.
```

`AgentFlow`, `$agent-flow` and `agent-flow` also work. Without a marker, the skill stays inactive.

Use it to:

- Implement features and fix bugs.
- Plan changes that need several specialists.
- Review code and check completed work.
- Keep project decisions and context between tasks.

Describe the result you need and any constraints. Include the relevant plan or files when available.

Work happens in the actual checkout. SQLite preserves accepted reports; corrections create new assignments linked to earlier results. Execution status and verification outcome are separate. Tasks that change files require independent review and QA. Final closes the journal using an explicitly selected concluding report.

Architecture and plan reviews use [Architecture Simplicity Guard](https://github.com/svishniakov/arch-simplicity-guard). The architect and independent reviewer run its selector inside their own Codex sessions to receive instructions for their actual models. Blocking simplicity findings are resolved before dependent implementation.

## Installation

Choose one of two options.

**Skill via npx:**

```bash
npx skills add https://github.com/svishniakov/agent-flow -a codex -g
npx skills add https://github.com/svishniakov/arch-simplicity-guard -a codex -g
python3 ~/.agents/skills/agent-flow/scripts/check-agent-deps.py --post-install
python3 ~/.agents/skills/agent-flow/scripts/sync-codex-agent-config.py --output-dir ~/.codex/agents
```

Install the guard dependency, check for other missing skills, then synchronize specialist configurations for Codex. Repeat the synchronization after updating the skill's role files.

**Codex plugin:** download `agent-flow-X.Y.Z-codex-plugin.zip` from
[Releases](https://github.com/svishniakov/agent-flow/releases/latest) and follow the
[plugin installation instructions](skills/agent-flow/docs/en/installation.md#codex-plugin).

## Upgrading

Update the installed skill with the same npx command, then **re-sync the roles —
every time**. Role definitions evolve between releases and new roles are added
over time; Agent Flow proves which role and model produced QA and review
evidence, so outdated synced roles block acceptance until you re-sync and start
a fresh Codex task:

```bash
npx skills add https://github.com/svishniakov/agent-flow -a codex -g
npx skills add https://github.com/svishniakov/arch-simplicity-guard -a codex -g
python3 ~/.agents/skills/agent-flow/scripts/sync-codex-agent-config.py --output-dir ~/.codex/agents
```

If a run reports outdated roles, do not continue that task — sync and start a
new one. Previous tasks are not resumed or migrated after an update. To Do, Lessons and Implementation Notes remain project memory.

[Skill instructions](skills/agent-flow/SKILL.md) · [License](LICENSE)
