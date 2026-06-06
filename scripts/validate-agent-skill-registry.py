#!/usr/bin/env python3
"""Validate the Agent Flow skill dependency registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_skill_deps import (
    default_agents_dir,
    default_registry_path,
    discover_role_skills,
    load_registry,
    validate_registry,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", type=Path, default=default_agents_dir(), help="Directory with agents/*.md.")
    parser.add_argument("--registry", type=Path, default=default_registry_path(), help="Registry JSON to validate.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        roles = discover_role_skills(args.agents_dir.expanduser().resolve())
        registry = load_registry(args.registry.expanduser().resolve())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1

    if registry is None:
        print(f"missing registry: {args.registry}", file=sys.stderr)
        return 1

    errors = validate_registry(roles, registry)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print(f"registry ok: {args.registry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
