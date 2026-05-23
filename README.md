# Agent Flow

Agent Flow is a Codex skill for routing requests into the smallest useful workflow: quick checks, solo work, orchestrated work, traceable runs, delegation, and verification gates.

This repository contains process rules and helper scripts. It is not a standalone application.

## Contents

- `SKILL.md` - skill entry point and routing rules.
- `references/` - workflow details for orchestration, delegation, traceable runs, design gates, and Definition of Done checks.
- `scripts/` - optional Python helpers for creating run skeletons, appending timeline events, and validating run completeness.
- `agents/openai.yaml` - agent configuration for OpenAI-based usage.

## Validation

There is no build step. Use these checks after editing:

```bash
python3 -m py_compile scripts/*.py
python3 scripts/init-run.py --help
python3 scripts/append-timeline.py --help
python3 scripts/validate-run.py --help
```

To smoke-test a local traceable run:

```bash
python3 scripts/init-run.py --repo . --slug smoke-test
python3 scripts/validate-run.py --run-dir .agent-work/runs/YYYY-MM-DD-smoke-test --allow-pending --allow-no-check
```

Replace `YYYY-MM-DD` with the date printed by `init-run.py`.

## Publishing

GitHub remote setup, releases, and a license are intentionally not included here. Add them only if the repository owner chooses to publish or distribute this skill.
