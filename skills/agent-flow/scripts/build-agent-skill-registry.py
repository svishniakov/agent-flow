#!/usr/bin/env python3
"""Create or update the Agent Flow skill dependency registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_skill_deps import (
    build_installed_index,
    build_registry,
    default_agents_dir,
    default_plugin_caches,
    default_registry_path,
    default_skill_roots,
    discover_role_skills,
    load_registry,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", type=Path, default=default_agents_dir(), help="Directory with agents/*.md.")
    parser.add_argument("--registry", type=Path, default=default_registry_path(), help="Registry JSON to update.")
    parser.add_argument(
        "--skill-root",
        type=Path,
        action="append",
        default=[],
        help="Skill root to scan. Can be repeated. Defaults to Codex/agents/Claude roots.",
    )
    parser.add_argument(
        "--plugin-cache",
        type=Path,
        action="append",
        default=[],
        help="Plugin cache root to scan. Can be repeated. Defaults to ~/.codex/plugins/cache.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print generated registry without writing.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    agents_dir = args.agents_dir.expanduser().resolve()
    registry_path = args.registry.expanduser().resolve()
    skill_roots = [path.expanduser() for path in args.skill_root] or default_skill_roots()
    plugin_caches = [path.expanduser() for path in args.plugin_cache] or default_plugin_caches()

    try:
        roles = discover_role_skills(agents_dir)
        installed = build_installed_index(skill_roots, plugin_caches)
        existing = load_registry(registry_path)
        registry = build_registry(roles, installed, existing)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1

    content = json.dumps(registry, ensure_ascii=False, indent=2) + "\n"
    if args.dry_run:
        print(content, end="")
        return 0

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(content, encoding="utf-8")
    print(f"wrote: {registry_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
