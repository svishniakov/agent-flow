# Agent Flow

Agent Flow is an explicitly invoked Codex skill for routing requests into an agent workflow with traceable runs, delegation, design gates, and verification gates.

Use it by starting the request with `Agent Flow`, `$agent-flow`, `agent-flow`, or `агент-флоу`. Text forms without `$` are case-insensitive. Requests without that prefix stay outside this skill and run solo in the main agent.

Agent Flow does not run a separate brainstorming pre-step. Unclear Agent Flow requests are handled through intake, routing, delegation, and verification inside the skill.

This repository contains process rules and helper scripts. It is not a standalone application.

## Contents

- `SKILL.md` - skill entry point and routing rules.
- `references/` - workflow details for orchestration, delegation, traceable runs, design gates, and Definition of Done checks.
- `scripts/` - optional Python helpers for creating run skeletons, appending timeline events, recording subagent traces, and validating run completeness.
- `agents/openai.yaml` - agent configuration for OpenAI-based usage.

## Validation

There is no build step. Use these checks after editing:

```bash
python3 -m py_compile scripts/*.py
python3 scripts/init-run.py --help
python3 scripts/append-timeline.py --help
python3 scripts/record-agent-trace.py --help
python3 scripts/validate-run.py --help
```

To smoke-test a local traceable run:

```bash
python3 scripts/init-run.py --repo . --slug smoke-test
python3 scripts/record-agent-trace.py --run-dir .agent-work/runs/YYYY-MM-DD-smoke-test --role python-worker --stage smoke --status pass --summary "Smoke event recorded." --next-step "validate"
python3 scripts/validate-run.py --run-dir .agent-work/runs/YYYY-MM-DD-smoke-test --allow-pending --allow-no-check
```

Replace `YYYY-MM-DD` with the date printed by `init-run.py`.

## Subagent Trace Contract

For delegated subagents in a traceable run, `scripts/record-agent-trace.py` is
the required writer. It appends the same role-owned event to run-level
`timeline.jsonl` and `agents/<role>/trace.jsonl`, creates the per-agent
directories, and indexes repeated `--artifact` paths in `artifacts.json`.

## Publishing

GitHub remote setup, releases, and a license are intentionally not included here. Add them only if the repository owner chooses to publish or distribute this skill.
