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

## Upgrading

Update the installed skill with the same npx command, then **re-sync the roles —
every time**. Role definitions evolve between releases and new roles are added
over time; Agent Flow proves which role and model produced QA and review
evidence, so outdated synced roles block acceptance until you re-sync and start
a fresh Codex task:

```bash
npx skills add https://github.com/svishniakov/agent-flow -a codex -g
python3 ~/.agents/skills/agent-flow/scripts/sync-codex-agent-config.py --output-dir ~/.codex/agents
```

If a run reports outdated roles, do not continue that task — sync and start a
new one. Tasks started before the sync stay bound to the old roles.

Notable breaking changes:

- **0.3.0**: journals written by 0.1.x/0.2.x are archival. There is no
  migration; finish or abandon old runs and start new ones.
- **0.4.0**: the helper scripts `append-timeline.py`, `record-lane-boundary.py`
  and `record-handoff-state.py` were removed; their functions moved into the
  unified journal writer and worker conclusion calls.
- **0.5.0**: audit sections in handoffs must declare covered ids through the
  `covers` field; phrase-matching acceptance was removed.
- **0.6.0**: distribution and development tooling slimmed down; only the Codex
  plugin ZIP is published.

[Skill instructions](skills/agent-flow/SKILL.md) · [License](LICENSE)
