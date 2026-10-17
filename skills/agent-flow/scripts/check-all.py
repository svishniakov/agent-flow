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
ROLE_PROMPT_INPUT_MARKER = "Use the delegation packet as the source of truth"
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


def run_test_inventory_guard(repo_root: Path, command_steps: list[tuple[str, list[str]]]) -> int:
    """Every test is planned once; removed required tests fail."""
    discovered = {path.resolve() for path in SCRIPTS.glob("test-*.py")}
    for path in (repo_root / "scripts").glob("test-*.py"):
        discovered.add(path.resolve())
    planned = []
    for _, command in command_steps:
        if "-m" in command:
            continue
        for argument in command:
            path = Path(argument)
            if path.name.startswith("test-") and path.suffix == ".py":
                planned.append((ROOT / path).resolve())
    failures = []
    for path in sorted(discovered - set(planned)):
        failures.append(f"test missing from full suite: {path}")
    for path in sorted(set(planned) - discovered):
        failures.append(f"required test missing from checkout: {path}")
    if len(planned) != len(set(planned)):
        failures.append("test scheduled more than once")
    manifest = set((ROOT / "package-files.txt").read_text(encoding="utf-8").splitlines())
    for path in sorted(SCRIPTS.glob("test-*.py")):
        if path.relative_to(ROOT).as_posix() not in manifest:
            failures.append(f"test missing from package manifest: {path.name}")
    if failures:
        print("FAIL test inventory:\n" + "\n".join(failures), file=sys.stderr)
        return 1
    print(f"PASS test inventory: {len(planned)} required test scripts")
    return 0


def run_content_guard(name: str, needle: str, repo_root: Path) -> int:
    print(f"==> {name}")
    matches: list[str] = []
    errors: list[str] = []
    for raw_path in PRODUCT_SEARCH_PATHS:
        path = ROOT / raw_path
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
    if repo_root != ROOT:
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
        legacy_installed = Path(raw_home) / ".codex" / "skills" / "agent-flow"
        skill_root = installed if installed.exists() else legacy_installed
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


def git_tracked_paths_under(path: Path) -> set[str]:
    relative = path.relative_to(ROOT).as_posix()
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", f"{relative}/**"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
    except OSError as exc:
        raise CheckError(f"Cannot run git ls-files: {exc}") from exc
    if result.returncode:
        raise CheckError(f"git ls-files failed (exit {result.returncode}): {result.stderr.strip()}")
    return {line for line in result.stdout.splitlines() if line}


def run_golden_trace_artifacts_guard() -> int:
    print("==> golden trace artifacts packaging guard")
    data = json.loads(GOLDEN_TRACE_MANIFEST.read_text(encoding="utf-8"))
    failures: list[str] = []
    for case in data.get("cases", []):
        if case.get("mode", "full") != "full":
            continue
        raw_path = case.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        run_dir = (GOLDEN_TRACE_MANIFEST.parent / raw_path).resolve()
        artifacts_dir = run_dir / "artifacts"
        try:
            tracked_paths = git_tracked_paths_under(artifacts_dir)
        except CheckError as exc:
            print(f"FAIL golden trace artifacts packaging guard: {exc}", file=sys.stderr)
            return 1
        if not artifacts_dir.is_dir() or not tracked_paths:
            failures.append(raw_path)
    if failures:
        print("FAIL golden trace artifacts packaging guard", file=sys.stderr)
        print("Each full golden trace must track at least one file under artifacts/.", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}/artifacts", file=sys.stderr)
        return 1
    print("PASS golden trace artifacts packaging guard")
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
        marker_count = text.count(ROLE_PROMPT_INPUT_MARKER)
        if marker_count != 1:
            failures.append(f"{path.relative_to(ROOT)}: expected one delegation source-of-truth marker, found {marker_count}")
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
    command_steps = [
        ("distribution archive fixtures", [sys.executable, str(repo_root / "scripts/test-build-distributions.py")]),
        ("numbered skill release fixtures", [sys.executable, str(repo_root / "scripts/test-skill-release.py")]),
        ("Git index and hook fixtures", [sys.executable, str(repo_root / "scripts/test-check-index.py")]),
        ("py_compile scripts", [sys.executable, "-m", "py_compile", *python_files]),
        ("repository check fixtures", [sys.executable, "scripts/test-check-all.py"]),
        ("check environment fixtures", [sys.executable, "scripts/test-check-environment.py"]),
        ("task facts fixtures", [sys.executable, "scripts/test-task-facts.py"]),
        ("agent config fixtures", [sys.executable, "scripts/test-agent-config.py"]),
        ("validate-agent-config fixtures", [sys.executable, "scripts/test-validate-agent-config.py"]),
        ("Codex agent config sync fixtures", [sys.executable, "scripts/test-sync-codex-agent-config.py"]),
        ("role catalog fixtures", [sys.executable, "scripts/test-validate-role-catalog.py"]),
        ("updater fixtures", [sys.executable, "scripts/test-update-agent-flow-skill.py"]),
        ("agent config validation", [sys.executable, "scripts/validate-agent-config.py"]),
        ("role catalog validation", [sys.executable, "scripts/validate-role-catalog.py"]),
        ("agent skill registry validation", [sys.executable, "scripts/validate-agent-skill-registry.py"]),
        ("architecture capability registry fixtures", [sys.executable, "scripts/test-validate-architecture-capabilities.py"]),
        ("architecture capability registry validation", [sys.executable, "scripts/validate-architecture-capabilities.py"]),
        ("rule consistency", [sys.executable, "scripts/validate-rule-consistency.py"]),
        ("rule consistency fixtures", [sys.executable, "scripts/test-validate-rule-consistency.py"]),
        ("validate-run CLI", [sys.executable, "scripts/validate-run.py", "--help"]),
        ("init-run fixtures", [sys.executable, "scripts/test-init-run.py"]),
        ("evidence record analyzer fixtures", [sys.executable, "scripts/test-analyze-evidence-records.py"]),
        ("harness evaluation promotion fixtures", [sys.executable, "scripts/test-promote-harness-evaluation.py"]),
        ("handoff state fixtures", [sys.executable, "scripts/test-handoff-state.py"]),
        ("lane boundary fixtures", [sys.executable, "scripts/test-lane-boundary.py"]),
        ("lane fixture tests", [sys.executable, "scripts/test-validate-run-lanes.py"]),
        ("QA/reviewer evidence fixtures", [sys.executable, "scripts/test-verification-evidence.py"]),
        ("journal IO fixtures", [sys.executable, "scripts/test-journal-io.py"]),
        ("journal storage failures", [sys.executable, "scripts/test-journal-storage.py"]),
        ("journal recovery", [sys.executable, "-B", "scripts/test-journal-recovery.py"]),
        ("journal lifecycle", [sys.executable, "-B", "scripts/test-journal-lifecycle.py"]),
        ("task workspace full coverage", [sys.executable, "scripts/test-task-workspace.py"]),
        ("procedure completion fixtures", [sys.executable, "scripts/test-procedure-completion.py"]),
        ("golden trace runs", [sys.executable, "scripts/test-golden-traces.py"]),
        ("git diff hygiene", ["git", "-C", str(repo_root), "diff", "--check"]),
    ]
    content_steps = [
        ("personal path guard", "/Users/" + "ucnlejumper"),
        ("old README/docs check block guard", "python3 -m py_compile " + "scripts/*.py"),
        ("old architecture profile term guard", "Architecture " + "Profiles"),
        ("old architecture profile file guard", "architecture-" + "profiles"),
        ("project crm profile guard", "profile" + "-" + "crm"),
        ("project agentflow profile guard", "profile" + "-" + "agentflow"),
        ("project child profile guard", "profile" + "-" + "child"),
        ("project local browser profile guard", "profile" + "-" + "local-browser"),
        ("old admin backoffice surface guard", "admin-" + "backoffice-ui"),
        ("old landing waitlist surface guard", "landing-" + "waitlist"),
        ("old browser extension dashboard surface guard", "browser-extension-" + "local-dashboard"),
        ("old ios app surface guard", "`ios-" + "app`"),
        ("old macos utility surface guard", "`macos-" + "utility`"),
        ("old harness matrix proposal guard", "Architecture Matrix " + "changes"),
        ("old harness capability proposal guard", "capability registry " + "changes"),
        ("old harness prompt proposal guard", "role prompt " + "updates"),
        ("old harness golden proposal guard", "Golden Trace Runs, but it never"),
        ("old harness slash proposal guard", "Matrix/capability/validator/prompt/golden-trace"),
        ("old harness approval true guard", "requires_human_approval=" + "true"),
        ("old harness human-approved guard", "human-" + "approved changes"),
        ("engineering simplicity schema v3 json guard", '"schema_version": ' + "3"),
        ("engineering simplicity schema v3 assignment guard", "schema_version=" + "3"),
        ("engineering simplicity new lane type json guard", '"type": "' + "engineering-simplicity" + '"'),
        ("engineering simplicity new lane type compact guard", '"type":"' + "engineering-simplicity" + '"'),
        ("old subagent downgrade role-lane guard", "continue with " + "role lanes or solo checks"),
        ("old subagent fallback solo guard", "fallback " + "to solo"),
        ("old reviewer unavailable fallback guard", "if unavailable, use " + "role-lane review"),
    ]

    failures = 0
    if run_test_inventory_guard(repo_root, command_steps):
        return 1
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
    for name, needle in content_steps:
        if run_content_guard(name, needle, repo_root):
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
