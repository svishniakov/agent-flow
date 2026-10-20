#!/usr/bin/env python3
"""Run the Agent Flow repository validation suite."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from check_environment import EnvironmentError, check_environment


class CheckError(RuntimeError):
    pass


def find_repo_root(start: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start, text=True, capture_output=True, check=False,
        )
    except OSError as exc:
        raise CheckError(f"Cannot inspect Git repository: {exc}") from exc
    if result.returncode:
        raise CheckError(f"Git repository required (exit {result.returncode}): {result.stderr.strip()}")
    repo_root = Path(result.stdout.strip()).resolve()
    if (repo_root / "skills/agent-flow").resolve() != start.resolve() or not (repo_root / "scripts/check-index.py").is_file():
        raise CheckError(f"Not the Agent Flow source repository: {repo_root}")
    return repo_root


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
GOLDEN_TRACE_MANIFEST = ROOT / "testdata" / "golden-traces" / "manifest.json"
ROLE_PROMPT_CONTRACT = "references/traceable-runs.md"
PRODUCT_SEARCH_PATHS = [
    "README.md",
    "README.ru.md",
    "SKILL.md",
    "docs",
    "references",
    "scripts",
    "agents",
    "registries",
    "testdata",
]
REQUIRED_SKILLS_CLI_DIRS = [
    "agents",
    "references",
    "registries",
    "scripts",
    "testdata",
    "docs",
]
ROOT_TEST_SCRIPTS = (
    "test-build-plugin.py",
    "test-check-index.py",
    "test-skill-release.py",
)
TEST_EXTRA_ARGS = {
    "test-journal-lifecycle.py": ["-B"],
}



def run_step(name: str, command: list[str]) -> int:
    print(f"==> {name}")
    try:
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    except OSError as exc:
        print(f"FAIL {name}: {exc}", file=sys.stderr)
        return 1
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        print(f"FAIL {name}: exit {result.returncode}", file=sys.stderr)
        return result.returncode
    output = result.stdout + "\n" + result.stderr
    if re.search(r"(?im)^\s*skip(?:ped)?\b|\bskipped=[1-9]\d*\b", output):
        print(f"FAIL {name}: an obligatory check was skipped", file=sys.stderr)
        return 1
    print(f"PASS {name}")
    return 0


def run_content_guard(name: str, needle: str, repo_root: Path, *, paths: list[Path] | None = None) -> int:
    print(f"==> {name}")
    matches: list[str] = []
    errors: list[str] = []
    search_paths = [ROOT / raw_path for raw_path in PRODUCT_SEARCH_PATHS] if paths is None else paths
    for path in search_paths:
        if path.is_dir():
            candidates = [candidate for candidate in path.rglob("*") if candidate.is_file()]
        elif path.exists():
            candidates = [path]
        else:
            candidates = []
        for candidate in candidates:
            if candidate == Path(__file__).resolve():
                continue
            if candidate.suffix not in {".md", ".py", ".json", ".yaml", ".yml"}:
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            except OSError as exc:
                errors.append(f"{candidate}: {exc}")
                continue
            if needle in text:
                matches.append(str(candidate.relative_to(ROOT)))
    if paths is None and repo_root != ROOT:
        for path in (repo_root / "README.md", repo_root / "README.ru.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                errors.append(f"{path}: {exc}")
                continue
            if needle in text:
                matches.append(str(path.relative_to(repo_root)))
    if matches:
        print(f"FAIL {name}: found forbidden text '{needle}'", file=sys.stderr)
        for match in matches:
            print(f"- {match}", file=sys.stderr)
    if errors:
        print(f"FAIL {name}: cannot read files", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
    if matches or errors:
        return 1
    print(f"PASS {name}")
    return 0


def run_readme_markdown_guard(repo_root: Path) -> int:
    print("==> README Markdown-only guard")
    forbidden = [
        "<p",
        "<picture",
        "<source",
        "<img",
        "<div",
        "<h1",
        "<h2",
        "<h3",
        "</",
        ".svg",
        "img.shields.io",
    ]
    failures: list[str] = []
    for name in ["README.md", "README.ru.md"]:
        path = repo_root / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(f"{path}: {exc}")
            continue
        for needle in forbidden:
            if needle in text:
                failures.append(f"{name}: {needle}")
    if failures:
        print("FAIL README Markdown-only guard", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("PASS README Markdown-only guard")
    return 0


def run_skills_cli_layout_guard(repo_root: Path) -> int:
    print("==> Skills CLI layout guard")
    failures: list[str] = []
    if repo_root != ROOT and (repo_root / "SKILL.md").exists():
        failures.append("root SKILL.md must not exist; it shadows skills/agent-flow/SKILL.md")
    if not (ROOT / "SKILL.md").is_file():
        failures.append("skills/agent-flow/SKILL.md is missing")
    if not (ROOT / "LICENSE").is_file():
        failures.append("skills/agent-flow/LICENSE is missing")
    for dirname in REQUIRED_SKILLS_CLI_DIRS:
        if not (ROOT / dirname).is_dir():
            failures.append(f"skills/agent-flow/{dirname}/ is missing")
    if failures:
        print("FAIL Skills CLI layout guard", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("PASS Skills CLI layout guard")
    return 0


def run_skills_cli_discovery_guard(repo_root: Path) -> int:
    print("==> Skills CLI discovery guard")
    result = subprocess.run(
        ["skills", "add", str(repo_root), "--list"],
        cwd=repo_root,
        env={**os.environ, "DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    output = result.stdout + result.stderr
    count_match = re.search(r"Found\s+(\d+)\s+skill", output)
    exact_skill_match = re.search(r"(?m)^\s*(?:│\s*)?agent-flow\s*$", output)
    if result.returncode or not count_match or count_match.group(1) != "1" or not exact_skill_match:
        print("FAIL Skills CLI discovery guard", file=sys.stderr)
        print(output, file=sys.stderr)
        return 1
    print("PASS Skills CLI discovery guard")
    return 0


def run_skills_cli_install_guard(repo_root: Path) -> int:
    print("==> Skills CLI clean install guard")
    with tempfile.TemporaryDirectory(prefix="agent-flow-skills-home-") as raw_home:
        env = os.environ.copy()
        env["HOME"] = raw_home
        env.update(DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
        result = subprocess.run(
            [
                "skills",
                "add",
                str(repo_root),
                "--skill",
                "agent-flow",
                "-a",
                "codex",
                "-g",
                "-y",
            ],
            cwd=repo_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        output = result.stdout + result.stderr
        installed = Path(raw_home) / ".agents" / "skills" / "agent-flow"
        codex_installed = Path(raw_home) / ".codex" / "skills" / "agent-flow"
        skill_root = installed if installed.exists() else codex_installed
        missing = ["SKILL.md"] if not (skill_root / "SKILL.md").is_file() else []
        missing.extend(dirname for dirname in REQUIRED_SKILLS_CLI_DIRS if not (skill_root / dirname).is_dir())
        if result.returncode or missing:
            print("FAIL Skills CLI clean install guard", file=sys.stderr)
            if missing:
                print("Missing installed paths: " + ", ".join(missing), file=sys.stderr)
            print(output, file=sys.stderr)
            return 1
    print("PASS Skills CLI clean install guard")
    return 0


def run_golden_trace_artifacts_guard() -> int:
    print("==> golden trace fixture guard")
    try:
        data = json.loads(GOLDEN_TRACE_MANIFEST.read_text(encoding="utf-8"))
        cases = data["cases"]
        if not isinstance(cases, list) or not cases:
            raise ValueError("golden trace cases must be a non-empty array")
        root = GOLDEN_TRACE_MANIFEST.parent.resolve()
        for case in cases:
            path = root / case["path"]
            if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
                raise ValueError(f"missing or unsafe golden trace fixture: {case['path']}")
            json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAIL golden trace fixture guard: {exc}", file=sys.stderr)
        return 1
    print("PASS golden trace fixture guard")
    return 0


def run_retired_blocking_rules_guard() -> int:
    print("==> retired blocking rules guard")
    failures: list[str] = []
    # These retired rules caused false dependency stops and repeat acceptance of history.
    forbidden = (
        "stale notes => uncertain => stop",
        "stop with a close-or-verify warning",
        "uncertain`: possible overlap, stale active notes",
        "If any active task is `dependent` or `uncertain`, stop before",
        "If any task is `uncertain` or `dependent`, stop before",
        "Treat uncertain dependency overlap as a stop condition",
        "If an active task has uncertain or direct overlap, stop",
        "Do not continue past an uncertain or direct active-task dependency",
        "нормализация статуса не обходит эту процедуру",
        "asks to verify or close it before new work",
        "просит проверить или закрыть её перед новой работой",
    )
    paths = [ROOT / "SKILL.md", ROOT / "agents/orchestrator.md"]
    paths.extend((ROOT / "references").glob("*.md"))
    paths.extend(ROOT / "docs" / language / "agent-flow.md" for language in ("en", "ru"))
    for path in paths:
        text = " ".join(path.read_text(encoding="utf-8").split())
        for needle in forbidden:
            if needle in text:
                failures.append(f"{path.relative_to(ROOT)}: retired rule {needle!r}")
    if failures:
        print("FAIL retired blocking rules guard", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("PASS retired blocking rules guard")
    return 0


def run_role_prompt_dedup_guard() -> int:
    print("==> role prompt deduplication guard")
    failures: list[str] = []
    for path in sorted((ROOT / "agents").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        guidance = re.findall(r"(?ms)^## Execution guidance\s*\n(.*?)(?=^## |\Z)", text)
        marker_count = sum(section.count(ROLE_PROMPT_CONTRACT) for section in guidance)
        if len(guidance) != 1 or marker_count != 1:
            failures.append(f"{path.relative_to(ROOT)}: execution guidance must reference the shared report contract once, found {marker_count}")
        if "Delegation packet must include:" in text:
            failures.append(f"{path.relative_to(ROOT)}: repeats the canonical delegation packet contract")
    if failures:
        print("FAIL role prompt deduplication guard", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("PASS role prompt deduplication guard")
    return 0


def main() -> int:
    try:
        repo_root = find_repo_root(ROOT)
    except CheckError as exc:
        print(f"FAIL repository preflight: {exc}", file=sys.stderr)
        print("Run check-all.py from an Agent Flow source Git checkout. "
              "For an installed skill, check dependencies with: "
              f"python3 {SCRIPTS / 'check-agent-deps.py'} --scope core", file=sys.stderr)
        return 1
    try:
        versions = check_environment()
    except EnvironmentError as exc:
        print(f"FAIL required environment: {exc}", file=sys.stderr)
        return 1
    print("Required environment: " + json.dumps(versions, sort_keys=True))
    python_files = sorted(str(path.relative_to(ROOT)) for path in SCRIPTS.glob("*.py"))
    test_steps = [
        (path.name, [sys.executable, *TEST_EXTRA_ARGS.get(path.name, []), f"scripts/{path.name}"])
        for path in sorted(SCRIPTS.glob("test-*.py"))
    ]
    command_steps = [
        *[(name, [sys.executable, str(repo_root / "scripts" / name)]) for name in ROOT_TEST_SCRIPTS],
        ("py_compile scripts", [sys.executable, "-m", "py_compile", *python_files]),
        *test_steps,
        ("agent config validation", [sys.executable, "scripts/validate-agent-config.py"]),
        ("role catalog validation", [sys.executable, "scripts/validate-role-catalog.py"]),
        ("agent skill registry validation", [sys.executable, "scripts/validate-agent-skill-registry.py"]),
        ("architecture capability registry validation", [sys.executable, "scripts/validate-architecture-capabilities.py"]),
        ("rule consistency", [sys.executable, "scripts/validate-rule-consistency.py"]),
        ("validate-run CLI", [sys.executable, "scripts/validate-run.py", "--help"]),
        ("git diff hygiene", ["git", "-C", str(repo_root), "diff", "--check"]),
    ]
    content_steps = [
        ("old subagent downgrade role-lane guard", "continue with " + "role lanes or solo checks"),
        ("old subagent fallback solo guard", "fallback " + "to solo"),
        ("old reviewer unavailable fallback guard", "if unavailable, use " + "role-lane review"),
    ]
    instruction_paths = [ROOT / "SKILL.md"]
    for directory in ("agents", "references", "docs/en", "docs/ru"):
        instruction_paths.extend((ROOT / directory).glob("*.md"))

    failures = 0
    if run_golden_trace_artifacts_guard():
        failures += 1
    if run_skills_cli_layout_guard(repo_root):
        failures += 1
    if run_skills_cli_discovery_guard(repo_root):
        failures += 1
    if run_skills_cli_install_guard(repo_root):
        failures += 1
    for name, command in command_steps:
        if run_step(name, command):
            failures += 1
    if run_content_guard("personal path guard", "/Users/" + "ucnlejumper", repo_root):
        failures += 1
    for name, needle in content_steps:
        if run_content_guard(name, needle, repo_root, paths=instruction_paths):
            failures += 1
    if run_readme_markdown_guard(repo_root):
        failures += 1
    if run_retired_blocking_rules_guard():
        failures += 1
    if run_role_prompt_dedup_guard():
        failures += 1

    if failures:
        print(f"FAILED {failures} check(s)", file=sys.stderr)
        return 1
    print("PASS all Agent Flow checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
