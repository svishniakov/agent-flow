#!/usr/bin/env python3
"""Resolve spawn_agent model settings for one Agent Flow role."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_config import (
    AgentConfigError,
    default_agents_dir,
    read_frontmatter,
    resolve_role_path,
    role_config,
    role_instructions,
    validate_role_metadata,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, help="Role slug, for example typescript-worker.")
    parser.add_argument(
        "--trigger",
        action="append",
        default=[],
        help="Escalation trigger for this task. Can be passed multiple times.",
    )
    parser.add_argument("--agents-dir", type=Path, default=default_agents_dir(), help="Directory with role .md files.")
    parser.add_argument("--include-instructions", action="store_true", help="Include the role instructions used by Codex config sync.")
    args = parser.parse_args()

    try:
        role_path = resolve_role_path(args.agents_dir.expanduser().resolve(), args.role)
        metadata = read_frontmatter(role_path)
    except AgentConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    errors = validate_role_metadata(role_path, metadata)
    if errors:
        for error in errors:
            print(f"{role_path}: {error}", file=sys.stderr)
        return 1

    try:
        config = role_config(metadata, args.role, args.trigger)
        if args.include_instructions:
            config["developer_instructions"] = role_instructions(role_path, metadata)
    except AgentConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(config, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
