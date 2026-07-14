#!/usr/bin/env python3
"""Validate Agent Flow role model configuration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_config import AgentConfigError, default_agents_dir, read_frontmatter, validate_role_metadata


FORBIDDEN_IDENTITY_CONFIG_KEYS = {
    "model",
    "reasoning_effort",
    "service_tier",
    "escalation_model",
    "escalation_reasoning_effort",
    "escalation_service_tier",
    "escalation_triggers",
    "tools",
    "skills",
}


def default_identities_path(agents_dir: Path) -> Path:
    return agents_dir / "agent-identities.json"


def validate_identities(path: Path, role_names: list[str]) -> list[str]:
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return [f"{path}: {exc}"]
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON: {exc}"]

    agents = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(agents, list):
        return [f"{path}: field 'agents' must be an array"]

    seen: set[str] = set()
    for index, entry in enumerate(agents):
        label = f"{path}: agents[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        role = entry.get("role")
        if not isinstance(role, str) or not role:
            errors.append(f"{label} missing role")
            continue
        if role in seen:
            errors.append(f"{path}: duplicate identity role: {role}")
        seen.add(role)
        if entry.get("stable_agent_slug") != role:
            errors.append(f"{label} stable_agent_slug must match role")
        stable_name = entry.get("stable_agent_name")
        if not isinstance(stable_name, str) or not stable_name.strip():
            errors.append(f"{label} missing stable_agent_name")
        forbidden_keys = sorted(FORBIDDEN_IDENTITY_CONFIG_KEYS & entry.keys())
        if forbidden_keys:
            errors.append(f"{label} must not contain runtime config keys: {', '.join(forbidden_keys)}")

    expected = sorted(role_names)
    actual = sorted(seen)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        stale = sorted(set(actual) - set(expected))
        if missing:
            errors.append(f"{path}: missing identities for roles: {', '.join(missing)}")
        if stale:
            errors.append(f"{path}: stale identities without role files: {', '.join(stale)}")
    return errors


def validate_readme_role_counts(repo_root: Path, role_count: int) -> list[str]:
    errors: list[str] = []
    for name in ["README.md", "README.ru.md"]:
        path = repo_root / name
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            continue
        russian_role_prefix = f"{role_count} \u0440\u043e\u043b"
        if f"{role_count} roles" not in text and russian_role_prefix not in text:
            errors.append(f"{path}: visible role count does not match actual role count {role_count}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", type=Path, default=default_agents_dir(), help="Directory with role .md files.")
    parser.add_argument(
        "--identities",
        type=Path,
        default=None,
        help="Agent identities JSON. Defaults to agents/agent-identities.json.",
    )
    parser.add_argument(
        "--skip-readme-counts",
        action="store_true",
        help="Skip README role-count drift checks.",
    )
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
    role_names: list[str] = []
    for path in role_files:
        role_names.append(path.stem)
        try:
            metadata = read_frontmatter(path)
        except AgentConfigError as exc:
            errors.append(str(exc))
            continue
        errors.extend(f"{path}: {error}" for error in validate_role_metadata(path, metadata))

    identities_path = (args.identities or default_identities_path(agents_dir)).expanduser().resolve()
    errors.extend(validate_identities(identities_path, role_names))

    if not args.skip_readme_counts:
        errors.extend(validate_readme_role_counts(agents_dir.parent, len(role_names)))

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print(f"validated {len(role_files)} agent configs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
