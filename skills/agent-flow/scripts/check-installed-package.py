#!/usr/bin/env python3
"""Check the selected Agent Flow package and Codex role configuration before use."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import tomllib

from package_distribution import PackageError, check_package, require

ROOT = Path(__file__).resolve().parents[1]


def check_runtime(root):
    """Exercise the installed journal in a disposable project, without account access."""
    with tempfile.TemporaryDirectory(prefix="agent-flow-package-smoke-") as temporary:
        base = Path(temporary).resolve()
        project = base / "project"
        project.mkdir()
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
                       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        codex_home = base / "codex"
        (codex_home / "sessions").mkdir(parents=True)
        thread_id = "00000000-0000-4000-8000-000000000001"
        (codex_home / "sessions" / (thread_id + ".jsonl")).write_text(
            json.dumps({"type": "session_meta", "payload": {"id": thread_id, "cwd": str(project)}}) + "\n",
            encoding="utf-8")
        environment["CODEX_HOME"] = str(codex_home)
        git = subprocess.run(["git", "init", "-q", "--template=", str(project)], env=environment,
                             capture_output=True, text=True, timeout=30)
        require(git.returncode == 0, "installed package runtime smoke failed: " + git.stderr)
        requirements = project / "plan.md"
        requirements.write_text("# Package smoke\n\n## Критерии приёмки\n\n- Package can initialize a task.\n", encoding="utf-8")
        imported = subprocess.run([sys.executable, "-B", "-c", "import journal_structure; import workspace_scope"],
                                  cwd=root / "scripts", env=environment, capture_output=True, text=True, timeout=30)
        require(imported.returncode == 0, "installed package runtime smoke failed: " + imported.stderr)
        command = [sys.executable, "-B", str(root / "scripts/init-run.py"), "--repo", str(project),
                   "--slug", "package-smoke", "--summary", "Verify installed startup",
                   "--root-agent-id", thread_id,
                   "--requirements", str(requirements), "--task-heading", "Package smoke",
                   "--operation-id", "package-smoke-init"]
        result = subprocess.run(command, cwd=project, env=environment,
                                capture_output=True, text=True, timeout=30)
        require(result.returncode == 0, "installed package runtime smoke failed: " +
                shlex.join(command) + "\n" + result.stdout + result.stderr)


def check_role_overrides(project, codex_home, expected):
    paths = {codex_home / "config.toml", Path("/etc/codex/config.toml")}
    paths.update(codex_home.glob("*.config.toml"))
    paths.update(path / ".codex/config.toml" for path in (project, *project.parents))
    roles = {Path(name).stem for name in expected}
    for path in sorted(paths):
        require(not path.is_symlink(), f"Codex config symlink requires explicit resolution: {path}")
        if not path.exists():
            continue
        require(not any(parent.is_symlink() for parent in path.parents), f"Codex config directory symlink: {path}")
        with path.open("rb") as stream:
            config = tomllib.load(stream)
        profiles = config.get("profiles", {})
        require(isinstance(profiles, dict), f"invalid Codex profiles table: {path}")
        layers = [config, *profiles.values()]
        for layer in layers:
            require(isinstance(layer, dict), f"invalid Codex config layer: {path}")
            agents = layer.get("agents", {})
            require(isinstance(agents, dict), f"invalid Codex agents table: {path}")
            conflicts = sorted(roles.intersection(agents))
            require(not conflicts, f"explicit role overrides in {path}: {', '.join(conflicts)}; preserve and resolve them before delegation from the selected package")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-only", action="store_true", help="Check archive integrity and disposable journal startup without account or role setup.")
    parser.add_argument("--roles-dir", type=Path, help="Selected Codex agents directory; defaults to CODEX_HOME/agents.")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="Project root whose local role overrides must agree.")
    parser.add_argument("--dependencies", action="store_true", help="Also report core skill dependencies, without installing them.")
    args = parser.parse_args()
    try:
        result = check_package(ROOT)
        check_runtime(ROOT)
        result["runtime_smoke"] = "passed"
        if not args.package_only:
            script = ROOT / "scripts/sync-codex-agent-config.py"
            spec = importlib.util.spec_from_file_location("agent_flow_sync", script)
            sync = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sync)
            roles_dir = args.roles_dir or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "agents"
            expected = sync.build_files(ROOT / "agents")
            codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            check_role_overrides(args.project.absolute(), codex_home, expected)
            command = [sys.executable, str(script), "--output-dir", str(roles_dir)]
            if sync.write_files(roles_dir, expected, check=True):
                raise PackageError("role setup required: " + shlex.join(command))
            local = args.project.absolute() / ".codex/agents"
            if local.absolute() != roles_dir.absolute():
                require(not any(path.is_symlink() for path in (local, *local.parents)), f"project role directory symlink conflicts with selected package: {local}")
                for name, content in expected.items():
                    path = local / name
                    require(not path.is_symlink(), f"project role symlink conflicts with selected package: {path}")
                    if path.exists():
                        require(path.is_file() and path.read_text() == content, f"project role differs from selected package: {path}; preserve it and resolve the override before delegation")
            result["roles_dir"] = str(roles_dir)
            result["roles"] = len(expected)
        if args.dependencies:
            command = [sys.executable, str(ROOT / "scripts/check-agent-deps.py"), "--scope", "core", "--install-plan"]
            completed = subprocess.run(command, check=False)
            require(completed.returncode == 0, "dependency checker failed: " + shlex.join(command))
            result["dependency_report"] = "reported; resolve missing skills needed by the selected roles before delegation"
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (PackageError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Agent Flow setup: {exc}\n")


if __name__ == "__main__":
    main()
