#!/usr/bin/env python3
"""Validate Agent Flow role model configuration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_config import AgentConfigError, default_agents_dir, read_frontmatter, validate_role_metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", type=Path, default=default_agents_dir(), help="Directory with role .md files.")
    args = parser.parse_args()

    agents_dir = args.agents_dir.expanduser().resolve()
    if not agents_dir.exists():
        print(f"missing agents directory: {agents_dir}", file=sys.stderr)
        return 1
    if not agents_dir.is_dir():
        print(f"agents path is not a directory: {agents_dir}", file=sys.stderr)
        return 1

    role_files = sorted(agents_dir.glob("*.md"))
    if not role_files:
        print(f"no role files found in: {agents_dir}", file=sys.stderr)
        return 1

    errors: list[str] = []
    for path in role_files:
        try:
            metadata = read_frontmatter(path)
        except AgentConfigError as exc:
            errors.append(str(exc))
            continue
        errors.extend(f"{path}: {error}" for error in validate_role_metadata(path, metadata))

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print(f"validated {len(role_files)} agent configs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
