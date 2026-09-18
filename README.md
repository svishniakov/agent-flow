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

## Installation

Choose one of two options.

**Skill via npx:**

```bash
npx skills add https://github.com/svishniakov/agent-flow -a codex -g
python3 ~/.agents/skills/agent-flow/scripts/check-agent-deps.py --post-install
python3 ~/.agents/skills/agent-flow/scripts/sync-codex-agent-config.py --output-dir ~/.codex/agents
```

The second command reports missing skills and guides dependency setup. The third creates the specialist configurations for Codex. Run it again after updating the skill's role files.

**Codex plugin:** download `agent-flow-X.Y.Z-codex-plugin.zip` from
[Releases](https://github.com/svishniakov/agent-flow/releases/latest) and follow the
[plugin installation instructions](skills/agent-flow/docs/en/installation.md#codex-plugin).

[Skill instructions](skills/agent-flow/SKILL.md) · [License](LICENSE)
