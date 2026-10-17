#!/usr/bin/env python3
"""Validate Agent Flow role catalog coverage."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from agent_config import default_agents_dir


ROLE_HEADING = re.compile(r"^### ([a-z0-9][a-z0-9-]*)$")


def default_catalog_path() -> Path:
    return Path(__file__).resolve().parents[1] / "references" / "role-catalog.md"


def role_names(agents_dir: Path) -> list[str]:
    return sorted(path.stem for path in agents_dir.glob("*.md"))


def catalog_names(path: Path) -> tuple[list[str], list[str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], [f"{path}: {exc}"]
    errors: list[str] = []
    names: list[str] = []
    seen: set[str] = set()
    for line in lines:
        match = ROLE_HEADING.match(line)
        if not match:
            continue
        name = match.group(1)
        if name in seen:
            errors.append(f"{path}: duplicate catalog role: {name}")
        seen.add(name)
        names.append(name)
    return names, errors


def validate_catalog(path: Path, agents_dir: Path) -> list[str]:
    names, errors = catalog_names(path)
    expected = role_names(agents_dir)
    actual = sorted(set(names))
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
