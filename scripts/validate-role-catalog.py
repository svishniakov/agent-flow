#!/usr/bin/env python3
"""Validate Agent Flow role catalog coverage."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from agent_config import ROLE_PATTERN, default_agents_dir


ROLE_HEADING = re.compile(r"^### ([a-z0-9][a-z0-9-]*)$")
REQUIRED_FIELDS = {
    "Status": ("active", "specialized"),
    "Use when": None,
    "Do not use when": None,
    "Overlap notes": None,
}


@dataclass(frozen=True)
class CatalogRole:
    name: str
    fields: dict[str, str]


def default_catalog_path() -> Path:
    return Path(__file__).resolve().parents[1] / "references" / "role-catalog.md"


def role_names(agents_dir: Path) -> list[str]:
    return sorted(path.stem for path in agents_dir.glob("*.md"))


def parse_catalog(path: Path) -> tuple[list[CatalogRole], list[str]]:
    errors: list[str] = []
    roles: list[CatalogRole] = []
    current_role: str | None = None
    current_fields: dict[str, str] = {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], [f"{path}: {exc}"]

    def flush() -> None:
        if current_role is not None:
            roles.append(CatalogRole(current_role, dict(current_fields)))

    for number, line in enumerate(lines, start=1):
        match = ROLE_HEADING.match(line)
        if match:
            flush()
            current_role = match.group(1)
            current_fields = {}
            continue
        if current_role is None:
            continue
        for field in REQUIRED_FIELDS:
            prefix = f"{field}:"
            if line.startswith(prefix):
                value = line[len(prefix):].strip()
                if field in current_fields:
                    errors.append(f"{path}:{number}: duplicate field '{field}' for role {current_role}")
                current_fields[field] = value

    flush()
    return roles, errors


def validate_catalog(path: Path, agents_dir: Path) -> list[str]:
    errors: list[str] = []
    expected = role_names(agents_dir)
    roles, parse_errors = parse_catalog(path)
    errors.extend(parse_errors)

    seen: set[str] = set()
    for role in roles:
        if not ROLE_PATTERN.fullmatch(role.name):
            errors.append(f"{path}: invalid role slug in catalog: {role.name}")
        if role.name in seen:
            errors.append(f"{path}: duplicate catalog role: {role.name}")
        seen.add(role.name)
        for field, allowed in REQUIRED_FIELDS.items():
            value = role.fields.get(field)
            if not value:
                errors.append(f"{path}: role {role.name} missing field '{field}'")
                continue
            if allowed is not None and value not in allowed:
                allowed_values = ", ".join(allowed)
                errors.append(f"{path}: role {role.name} invalid {field}: {value} (expected one of: {allowed_values})")

    actual = sorted(seen)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        stale = sorted(set(actual) - set(expected))
        if missing:
            errors.append(f"{path}: missing catalog entries for roles: {', '.join(missing)}")
        if stale:
            errors.append(f"{path}: stale catalog entries without role files: {', '.join(stale)}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", type=Path, default=default_agents_dir(), help="Directory with agents/*.md.")
    parser.add_argument("--catalog", type=Path, default=default_catalog_path(), help="Role catalog Markdown file.")
    args = parser.parse_args()

    agents_dir = args.agents_dir.expanduser().resolve()
    catalog = args.catalog.expanduser().resolve()

    if not agents_dir.exists():
        print(f"agents dir not found: {agents_dir}", file=sys.stderr)
        return 1
    if not catalog.exists():
        print(f"role catalog not found: {catalog}", file=sys.stderr)
        return 1

    errors = validate_catalog(catalog, agents_dir)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print(f"role catalog ok: {catalog}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
