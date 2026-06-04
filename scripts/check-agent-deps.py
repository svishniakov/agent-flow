#!/usr/bin/env python3
"""Check optional Agent Flow role skills against the local environment."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


FRONTMATTER_BOUNDARY = "---"
SKILL_FILE = "SKILL.md"
DEFAULT_REPORT = "agent-deps-report.json"


@dataclass
class RoleSkills:
    name: str
    path: Path
    skills: list[str]


@dataclass
class SkillHit:
    locations: set[str] = field(default_factory=set)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_agents_dir() -> Path:
    return repo_root() / "agents"


def default_skill_roots() -> list[Path]:
    home = Path.home()
    return [
        home / ".codex" / "skills",
        home / ".agents" / "skills",
        home / ".claude" / "skills",
    ]


def default_plugin_caches() -> list[Path]:
    return [Path.home() / ".codex" / "plugins" / "cache"]


def parse_frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_BOUNDARY:
        return {}

    try:
        end_index = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == FRONTMATTER_BOUNDARY
        )
    except StopIteration:
        raise ValueError(f"{path}: missing closing frontmatter marker")

    metadata: dict[str, str] = {}
    for line_number, line in enumerate(lines[1:end_index], start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"{path}:{line_number}: invalid frontmatter line")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"{path}:{line_number}: empty frontmatter key")
        metadata[key] = raw_value.strip()
    return metadata


def strip_yaml_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def split_inline_list(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]

    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False

    for char in value:
        if escape:
            current.append(char)
            escape = False
            continue
        if char == "\\" and quote:
            current.append(char)
            escape = True
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            current.append(char)
            continue
        if char == "," and quote is None:
            item = strip_yaml_quotes("".join(current).strip())
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)

    item = strip_yaml_quotes("".join(current).strip())
    if item:
        items.append(item)
    return items


def discover_role_skills(agents_dir: Path) -> list[RoleSkills]:
    roles: list[RoleSkills] = []
    for path in sorted(agents_dir.glob("*.md")):
        metadata = parse_frontmatter(path)
        role_name = strip_yaml_quotes(metadata.get("name", "")).strip() or path.stem
        skills = split_inline_list(metadata.get("skills", ""))
        roles.append(RoleSkills(name=role_name, path=path, skills=skills))
    return roles


def read_skill_name(skill_file: Path) -> str | None:
    try:
        metadata = parse_frontmatter(skill_file)
    except (OSError, ValueError):
        return None
    name = metadata.get("name")
    if not name:
        return None
    return strip_yaml_quotes(name).strip() or None


def add_hit(index: dict[str, SkillHit], alias: str | None, location: Path) -> None:
    if not alias:
        return
    normalized = alias.strip()
    if not normalized:
        return
    index.setdefault(normalized, SkillHit()).locations.add(str(location))


def skill_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if root.is_file():
        return [root] if root.name == SKILL_FILE else []
    return sorted(root.rglob(SKILL_FILE))


def index_skill_root(root: Path, index: dict[str, SkillHit]) -> None:
    root = root.expanduser()
    for skill_file in skill_files(root):
        skill_dir = skill_file.parent
        add_hit(index, skill_dir.name, skill_file)
        add_hit(index, read_skill_name(skill_file), skill_file)


def infer_plugin_slug(cache_root: Path, skill_file: Path) -> str | None:
    try:
        parts = skill_file.relative_to(cache_root).parts
    except ValueError:
        return None
    if "skills" not in parts:
        return None
    skills_index = parts.index("skills")
    if skills_index >= 2:
        return parts[skills_index - 2]
    if skills_index >= 1:
        return parts[skills_index - 1]
    return None


def index_plugin_cache(cache_root: Path, index: dict[str, SkillHit]) -> None:
    cache_root = cache_root.expanduser()
    for skill_file in skill_files(cache_root):
        skill_dir = skill_file.parent
        name = read_skill_name(skill_file)
        plugin_slug = infer_plugin_slug(cache_root, skill_file)

        add_hit(index, skill_dir.name, skill_file)
        add_hit(index, name, skill_file)
        if plugin_slug:
            for alias in {skill_dir.name, name}:
                if alias:
                    add_hit(index, f"{plugin_slug}:{alias}", skill_file)
                    add_hit(index, f"{plugin_slug}::{alias}", skill_file)


def build_installed_index(skill_roots: list[Path], plugin_caches: list[Path]) -> dict[str, SkillHit]:
    index: dict[str, SkillHit] = {}
    for root in skill_roots:
        index_skill_root(root, index)
    for cache in plugin_caches:
        index_plugin_cache(cache, index)
    return index


def build_report(roles: list[RoleSkills], installed: dict[str, SkillHit]) -> dict[str, Any]:
    skill_roles: dict[str, set[str]] = {}
    for role in roles:
        for skill in role.skills:
            skill_roles.setdefault(skill, set()).add(role.name)

    skill_rows: list[dict[str, Any]] = []
    for skill in sorted(skill_roles):
        hit = installed.get(skill)
        skill_rows.append(
            {
                "name": skill,
                "status": "installed" if hit else "missing",
                "locations": sorted(hit.locations) if hit else [],
                "roles": sorted(skill_roles[skill]),
            }
        )

    role_rows: list[dict[str, Any]] = []
    for role in sorted(roles, key=lambda item: item.name):
        missing = sorted(skill for skill in role.skills if skill not in installed)
        role_rows.append(
            {
                "name": role.name,
                "path": str(role.path),
                "skills": sorted(role.skills),
                "missing_skills": missing,
            }
        )

    installed_count = sum(1 for row in skill_rows if row["status"] == "installed")
    missing_count = sum(1 for row in skill_rows if row["status"] == "missing")
    return {
        "summary": {
            "total": len(skill_rows),
            "installed": installed_count,
            "missing": missing_count,
        },
        "skills": skill_rows,
        "roles": role_rows,
    }


def print_human_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print(
        "Optional agent skills: "
        f"{summary['total']} total, {summary['installed']} installed, {summary['missing']} missing."
    )

    missing = [row for row in report["skills"] if row["status"] == "missing"]
    installed = [row for row in report["skills"] if row["status"] == "installed"]

    if installed:
        print(f"\nInstalled: {len(installed)}")

    if missing:
        print("\nMissing:")
        for row in missing:
            print(f"- {row['name']} ({', '.join(row['roles'])})")
    else:
        print("\nMissing: none")

    print("\nUse --json for full details. No optional skills are installed by this checker.")


def print_missing_by_role(report: dict[str, Any]) -> None:
    rows = [row for row in report["roles"] if row["missing_skills"]]
    if not rows:
        print("No roles have missing optional skills.")
        return
    print("Missing by role:")
    for row in rows:
        print(f"- {row['name']}: {', '.join(row['missing_skills'])}")


def save_json_report(report: dict[str, Any], output_path: Path) -> None:
    output_path = output_path.expanduser().resolve()
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved: {output_path}")


def run_prompt(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print(
        "Optional agent skills: "
        f"{summary['total']} total, {summary['installed']} installed, {summary['missing']} missing."
    )
    print("[1] Core only (default)")
    print("[2] Show missing by role")
    print("[3] Save JSON report")

    choice = input("Choice: ").strip() or "1"
    if choice == "1":
        print("Core install unchanged. No optional skills installed.")
        return
    if choice == "2":
        print_missing_by_role(report)
        return
    if choice == "3":
        raw_path = input(f"Report path [{DEFAULT_REPORT}]: ").strip() or DEFAULT_REPORT
        save_json_report(report, Path(raw_path))
        return
    print(f"unknown choice: {choice}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", type=Path, default=default_agents_dir(), help="Directory with agents/*.md.")
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
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--prompt", action="store_true", help="Show interactive post-install prompt.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when optional skills are missing.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    agents_dir = args.agents_dir.expanduser().resolve()
    skill_roots = [path.expanduser() for path in args.skill_root] or default_skill_roots()
    plugin_caches = [path.expanduser() for path in args.plugin_cache] or default_plugin_caches()

    if not agents_dir.exists():
        print(f"agents dir not found: {agents_dir}", file=sys.stderr)
        return 1

    try:
        roles = discover_role_skills(agents_dir)
    except (OSError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1

    installed = build_installed_index(skill_roots, plugin_caches)
    report = build_report(roles, installed)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.prompt:
        run_prompt(report)
    else:
        print_human_report(report)

    if args.strict and report["summary"]["missing"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
