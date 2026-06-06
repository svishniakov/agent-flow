#!/usr/bin/env python3
"""Check Agent Flow role skills and print optional install guidance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_skill_deps import (
    SCOPES,
    TARGETS,
    build_installed_index,
    build_report,
    default_agents_dir,
    default_plugin_caches,
    default_registry_path,
    default_skill_roots,
    discover_role_skills,
    load_registry,
    missing_rows,
    print_install_plan,
    run_guided_install,
)


DEFAULT_REPORT = "agent-deps-report.json"


def print_human_report(report: dict) -> None:
    summary = report["summary"]
    print(
        "Agent skills: "
        f"{summary['total']} total, {summary['installed']} installed, {summary['missing']} missing "
        f"(scope={summary['scope']}, target={summary['target']})."
    )

    installed = [row for row in report["skills"] if row["status"] == "installed"]
    missing = missing_rows(report)

    if installed:
        print(f"\nInstalled: {len(installed)}")

    if missing:
        print("\nMissing:")
        for row in missing:
            print(f"- {row['name']} [{row.get('tier', 'optional')}] ({', '.join(row['roles'])})")
    else:
        print("\nMissing: none")

    print("\nUse --install-plan for install instructions. No skills are installed unless --guided-install is used.")


def print_missing_by_role(report: dict) -> None:
    rows = [row for row in report["roles"] if row["missing_skills"]]
    if not rows:
        print("No roles have missing skills in this scope.")
        return
    print("Missing by role:")
    for row in rows:
        print(f"- {row['name']}: {', '.join(row['missing_skills'])}")


def save_json_report(report: dict, output_path: Path) -> None:
    output_path = output_path.expanduser().resolve()
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved: {output_path}")


def build_reports(roles: list, installed: dict, registry: dict | None, target: str) -> dict[str, dict]:
    reports = {
        "core": build_report(roles, installed, registry, scope="core", target=target),
        "full": build_report(roles, installed, registry, scope="full", target=target),
    }
    reports.update(
        {
            f"role:{role.name}": build_report(roles, installed, registry, scope=f"role:{role.name}", target=target)
            for role in roles
        }
    )
    return reports


def choose_target(default_target: str) -> str | None:
    raw_target = input(f"Target: global|project [auto: {default_target}]: ").strip() or default_target
    if raw_target == "auto":
        raw_target = default_target
    if raw_target not in {"global", "project"}:
        print(f"unknown target: {raw_target}", file=sys.stderr)
        return None
    return raw_target


def reload_report(roles: list, registry: dict | None, scope: str, target: str, skill_roots: list[Path], plugin_caches: list[Path]) -> dict:
    installed = build_installed_index(skill_roots, plugin_caches)
    return build_report(roles, installed, registry, scope=scope, target=target)


def print_recheck(report: dict) -> None:
    summary = report["summary"]
    print(
        "Recheck: "
        f"{summary['installed']} installed, {summary['missing']} missing "
        f"(scope={summary['scope']}, target={summary['target']})."
    )


def prompt_report(
    roles: list,
    installed: dict,
    registry: dict | None,
    target: str,
    skill_roots: list[Path],
    plugin_caches: list[Path],
    post_install: bool = False,
) -> int:
    reports = build_reports(roles, installed, registry, target)
    installed_target = reports["core"]["summary"]["target"]
    core_missing = reports["core"]["summary"]["missing"]
    full_missing = reports["full"]["summary"]["missing"]
    heading = "Agent Flow post-install dependency wizard" if post_install else "Agent Flow dependency wizard"
    print(heading)
    print(f"Agent Flow installed: {installed_target}")
    print(f"Missing core skills: {core_missing}")
    print(f"Missing full skills: {full_missing}")
    print("")
    if core_missing:
        print("Recommendation: install core skills now.")
    else:
        print("Recommendation: core skills are already installed.")
    print("")
    print("[1] Install core skills (recommended)")
    print("[2] Install full skill set")
    print("[3] Show core install plan only")
    print("[4] Show full install plan only")
    print("[5] Skip for now")
    print("[6] Save JSON report")

    choice = input("Choice: ").strip() or "1"
    if choice in {"1", "2", "3", "4"}:
        selected_target = choose_target(installed_target)
        if selected_target is None:
            return 1
        selected_reports = build_reports(roles, installed, registry, selected_target)
    if choice == "1":
        result = run_guided_install(selected_reports["core"])
        if result == 0:
            print_recheck(reload_report(roles, registry, "core", selected_target, skill_roots, plugin_caches))
        return result
    if choice == "2":
        result = run_guided_install(selected_reports["full"])
        if result == 0:
            print_recheck(reload_report(roles, registry, "full", selected_target, skill_roots, plugin_caches))
        return result
    if choice == "3":
        print_install_plan(selected_reports["core"])
        return 0
    if choice == "4":
        print_install_plan(selected_reports["full"])
        return 0
    if choice == "5":
        print("Skipped.")
        return 0
    if choice == "6":
        raw_path = input(f"Report path [{DEFAULT_REPORT}]: ").strip() or DEFAULT_REPORT
        save_json_report(reports["full"], Path(raw_path))
        return 0
    print(f"unknown choice: {choice}", file=sys.stderr)
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", type=Path, default=default_agents_dir(), help="Directory with agents/*.md.")
    parser.add_argument("--registry", type=Path, default=default_registry_path(), help="Skill registry JSON.")
    parser.add_argument(
        "--scope",
        default="full",
        help="Dependency scope: core, full, or role:<role-slug>.",
    )
    parser.add_argument(
        "--target",
        default="auto",
        choices=sorted(TARGETS),
        help="Install target used for install plans.",
    )
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
    parser.add_argument("--install-plan", action="store_true", help="Print install instructions for missing skills.")
    parser.add_argument(
        "--guided-install",
        action="store_true",
        help="Run allowlisted install commands after explicit confirmation. Manual/prompt entries are printed only.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--prompt", action="store_true", help="Show interactive post-install prompt.")
    parser.add_argument("--post-install", action="store_true", help="Alias for --prompt with onboarding wording.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when selected-scope skills are missing.")
    return parser.parse_args()


def valid_scope(scope: str) -> bool:
    return scope in SCOPES or scope.startswith("role:")


def main() -> int:
    args = parse_args()
    if not valid_scope(args.scope):
        print(f"invalid scope: {args.scope}", file=sys.stderr)
        return 1

    agents_dir = args.agents_dir.expanduser().resolve()
    skill_roots = [path.expanduser() for path in args.skill_root] or default_skill_roots()
    plugin_caches = [path.expanduser() for path in args.plugin_cache] or default_plugin_caches()

    if not agents_dir.exists():
        print(f"agents dir not found: {agents_dir}", file=sys.stderr)
        return 1

    try:
        roles = discover_role_skills(agents_dir)
        registry = load_registry(args.registry.expanduser().resolve())
        installed = build_installed_index(skill_roots, plugin_caches)
        report = build_report(roles, installed, registry, scope=args.scope, target=args.target)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.prompt or args.post_install:
        return prompt_report(
            roles,
            installed,
            registry,
            args.target,
            skill_roots,
            plugin_caches,
            post_install=args.post_install,
        )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.guided_install:
        result = run_guided_install(report)
        if result == 0:
            print_recheck(reload_report(roles, registry, args.scope, args.target, skill_roots, plugin_caches))
        return result
    elif args.install_plan:
        print_install_plan(report)
    else:
        print_human_report(report)

    if args.strict and report["summary"]["missing"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
