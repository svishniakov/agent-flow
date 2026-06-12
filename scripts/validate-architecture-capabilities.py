#!/usr/bin/env python3
"""Validate the Architecture Capability Router registry."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from architecture_capabilities import (
    AGENT_SKILLS_REGISTRY_PATH,
    ARCHITECTURE_CAPABILITY_REGISTRY_PATH,
    ARCHITECTURE_MATRIX_PATH,
    validate_architecture_capability_registry,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=ARCHITECTURE_CAPABILITY_REGISTRY_PATH,
        help="Architecture capability registry JSON.",
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=ARCHITECTURE_MATRIX_PATH,
        help="Architecture Matrix markdown source.",
    )
    parser.add_argument(
        "--agent-skills",
        type=Path,
        default=AGENT_SKILLS_REGISTRY_PATH,
        help="Agent skills registry JSON.",
    )
    parser.add_argument(
        "--allow-partial-matrix-coverage",
        action="store_true",
        help="Do not require every Architecture Matrix facet to be covered.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    capabilities, errors = validate_architecture_capability_registry(
        args.registry.expanduser().resolve(),
        matrix_path=args.matrix.expanduser().resolve(),
        agent_skills_path=args.agent_skills.expanduser().resolve(),
        validate_skills=True,
        require_full_matrix_coverage=not args.allow_partial_matrix_coverage,
    )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"architecture capability registry ok: {args.registry} ({len(capabilities)} capabilities)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
