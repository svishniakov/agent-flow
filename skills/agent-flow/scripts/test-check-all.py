#!/usr/bin/env python3
"""Regression checks for repository detection and validation diagnostics."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("check-all.py")


def load_checks(path: Path):
    spec = importlib.util.spec_from_file_location("check_all_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RepositoryChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="check-all-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve() / "repo"
        self.package = self.repo / "skills/agent-flow"
        (self.package / "scripts").mkdir(parents=True)
        (self.repo / "scripts").mkdir()
        (self.repo / "scripts/check-index.py").write_text("# source entrypoint\n")
        shutil.copy2(SCRIPT, self.package / "scripts/check-all.py")
        self.git("init")
        for name in ("README.md", "README.ru.md"):
            (self.repo / name).write_text("# Fixture\n")
        self.checks = load_checks(self.package / "scripts/check-all.py")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, text=True, capture_output=True, check=True)

    def capture(self, fn, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = fn(*args)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_import_does_not_inspect_git(self):
        with patch("subprocess.run", side_effect=AssertionError("Git called during import")):
            load_checks(self.package / "scripts/check-all.py")

    def test_source_root_and_symlink(self):
        self.assertEqual(self.checks.find_repo_root(self.package), self.repo)
        link = self.repo / "package-link"
        link.symlink_to(self.package, target_is_directory=True)
        self.assertEqual(self.checks.find_repo_root(link), self.repo)

    def test_missing_readme_does_not_change_root(self):
        (self.repo / "README.md").unlink()
        self.assertEqual(self.checks.find_repo_root(self.package), self.repo)

    def test_worktree_git_file(self):
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
        worktree = self.repo.parent / "worktree"
        self.git("worktree", "add", "--detach", str(worktree), "HEAD")
        self.assertTrue((worktree / ".git").is_file())
        self.assertEqual(self.checks.find_repo_root(worktree / "skills/agent-flow"), worktree)

    def test_archive_and_installed_package_refuse_before_commands(self):
        for location in ("archive/skills/agent-flow", "standalone/agent-flow", "consumer/.agents/skills/agent-flow"):
            with self.subTest(location=location):
                package = self.repo.parent / location
                (package / "scripts").mkdir(parents=True)
                shutil.copy2(SCRIPT, package / "scripts/check-all.py")
                if location.startswith("consumer"):
                    consumer = self.repo.parent / "consumer"
                    subprocess.run(["git", "init"], cwd=consumer, capture_output=True, check=True)
                    (consumer / "README.md").write_text("# Unrelated project\n")
                module = load_checks(package / "scripts/check-all.py")
                with patch.object(module, "run_step") as steps, \
                     patch.object(module, "run_skills_cli_discovery_guard") as cli, \
                     patch.object(module, "run_skills_cli_install_guard"):
                    code, out, err = self.capture(module.main)
                self.assertEqual(code, 1)
                self.assertIn("FAIL repository preflight", err)
                self.assertIn("check-agent-deps.py --scope core", err)
                self.assertNotIn("PASS all", out)
                steps.assert_not_called()
                cli.assert_not_called()

    def test_missing_git_and_broken_git_file(self):
        with patch.object(self.checks.subprocess, "run", side_effect=FileNotFoundError("git unavailable")):
            code, _, err = self.capture(self.checks.main)
        self.assertEqual(code, 1)
        self.assertIn("git unavailable", err)
        shutil.rmtree(self.repo / ".git")
        (self.repo / ".git").write_text("gitdir: /does-not-exist/check-all-fixture\n")
        code, _, err = self.capture(self.checks.main)
        self.assertEqual(code, 1)
        self.assertIn("FAIL repository preflight", err)

    def test_readme_failure_does_not_hide_second_readme(self):
        path = self.repo / "README.md"
        for kind in ("missing", "directory", "unreadable"):
            with self.subTest(kind=kind):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
                if kind == "directory":
                    path.mkdir()
                if kind == "unreadable":
                    path.write_text("# Fixture\n")
                (self.repo / "README.ru.md").write_text("# Fixture\n")
                read_text = Path.read_text

                def read(candidate, *args, **kwargs):
                    if candidate == path and kind == "unreadable":
                        raise PermissionError("read denied")
                    return read_text(candidate, *args, **kwargs)

                with patch.object(Path, "read_text", read):
                    clean_code, _, clean_err = self.capture(self.checks.run_content_guard, "fixture", "<div", self.repo)
                    self.assertEqual(clean_code, 1)
                    self.assertIn("README.md", clean_err)
                    (self.repo / "README.ru.md").write_text("<div>bad</div>\n")
                    code, _, err = self.capture(self.checks.run_readme_markdown_guard, self.repo)
                    content_code, _, content_err = self.capture(self.checks.run_content_guard, "fixture", "<div", self.repo)
                self.assertEqual(code, 1)
                self.assertIn("README.md", err)
                self.assertIn("README.ru.md", err)
                self.assertEqual(content_code, 1)
                self.assertIn("README.ru.md", content_err)

    def test_content_guard_checks_active_markdown_and_preserves_backup(self):
        directory = self.package / "docs"
        directory.mkdir()
        marker = "/Users/" + "ucnlejumper"
        backup = directory / "historical.md.bak"
        original = ("Historical instruction " + marker + "\n").encode()
        backup.write_bytes(original)
        code, _, _ = self.capture(self.checks.run_content_guard, "personal path fixture", marker, self.repo)
        self.assertEqual(code, 0)
        (directory / "active.md").write_bytes(original)
        code, _, errors = self.capture(self.checks.run_content_guard, "personal path fixture", marker, self.repo)
        self.assertEqual(code, 1)
        self.assertIn("active.md", errors)
        self.assertEqual(backup.read_bytes(), original)

    def test_main_continues_after_readme_failure(self):
        (self.repo / "README.md").unlink()
        names = ("run_step", "run_skills_cli_layout_guard", "run_skills_cli_discovery_guard",
                 "run_skills_cli_install_guard",
                 "run_golden_trace_artifacts_guard", "run_content_guard")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(self.checks, "check_environment", return_value={}))
            for name in names:
                stack.enter_context(patch.object(self.checks, name, return_value=0))
            later = stack.enter_context(patch.object(self.checks, "run_retired_blocking_rules_guard", return_value=0))
            stack.enter_context(patch.object(self.checks, "run_role_prompt_dedup_guard", return_value=0))
            code, _, err = self.capture(self.checks.main)
        self.assertEqual(code, 1)
        self.assertIn("FAILED 1 check(s)", err)
        later.assert_called_once()

    def test_retired_guard_accepts_documents_without_ceremony_and_rejects_blocking_rule(self):
        for relative in ("SKILL.md", "agents/orchestrator.md", "references/runtime.md",
                         "docs/en/agent-flow.md", "docs/ru/agent-flow.md"):
            path = self.package / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Current concise instructions.\n")
        code, output, errors = self.capture(self.checks.run_retired_blocking_rules_guard)
        self.assertEqual(code, 0, errors)
        self.assertIn("PASS retired blocking rules guard", output)
        # Neither old readiness phrases nor literals in source/fixtures are required.
        self.assertFalse((self.package / "scripts/init-run.py").exists())
        orchestrator = self.package / "agents/orchestrator.md"
        orchestrator.write_text("If any active task is `dependent` or `uncertain`,\nstop before new work.\n")
        code, _, errors = self.capture(self.checks.run_retired_blocking_rules_guard)
        self.assertEqual(code, 1)
        self.assertIn("agents/orchestrator.md: retired rule", errors)

    def test_runtime_suites_remain_planned_and_failures_propagate(self):
        guards = ("run_skills_cli_layout_guard", "run_skills_cli_discovery_guard", "run_skills_cli_install_guard",
                  "run_golden_trace_artifacts_guard", "run_content_guard",
                  "run_retired_blocking_rules_guard", "run_role_prompt_dedup_guard")
        scripts = self.package / "scripts"
        for name in ("test-alpha.py", "test-beta.py", "test-journal-recovery.py"):
            (scripts / name).write_text("print('fixture')\n")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(self.checks, "check_environment", return_value={}))
            for name in guards:
                stack.enter_context(patch.object(self.checks, name, return_value=0))
            steps = stack.enter_context(patch.object(self.checks, "run_step",
                side_effect=lambda name, command: int(name == "test-beta.py")))
            code, _, errors = self.capture(self.checks.main)
        calls = steps.call_args_list
        planned = [call.args[0] for call in calls]
        commands = [call.args[1] for call in calls]
        for script in ("test-build-distributions.py", "test-check-index.py", "test-skill-release.py"):
            self.assertTrue(any(command[-1].endswith("scripts/" + script) for command in commands), script)
        discovered = [name for name in planned if name in {"test-alpha.py", "test-beta.py", "test-journal-recovery.py"}]
        self.assertEqual(discovered, ["test-alpha.py", "test-beta.py", "test-journal-recovery.py"])
        recovery = commands[planned.index("test-journal-recovery.py")]
        self.assertIn("-B", recovery)
        self.assertNotIn("-B", commands[planned.index("test-alpha.py")])
        self.assertTrue(any(command[:2] == [calls[0].args[1][0], "-m"] for command in commands), "py_compile step")
        self.assertEqual(code, 1)
        self.assertIn("FAILED 1 check(s)", errors)

    def test_excluded_tests_are_not_planned(self):
        (self.package / "scripts/test-included.py").write_text("print('fixture')\n")
        (self.package / "scripts/test-excluded.py").write_text("print('fixture')\n")
        guards = ("run_skills_cli_layout_guard", "run_skills_cli_discovery_guard", "run_skills_cli_install_guard",
                  "run_golden_trace_artifacts_guard", "run_content_guard",
                  "run_retired_blocking_rules_guard", "run_role_prompt_dedup_guard")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(self.checks, "check_environment", return_value={}))
            for name in guards:
                stack.enter_context(patch.object(self.checks, name, return_value=0))
            stack.enter_context(patch.object(self.checks, "EXCLUDED_TESTS", ("test-excluded.py",)))
            steps = stack.enter_context(patch.object(self.checks, "run_step", return_value=0))
            code, _, errors = self.capture(self.checks.main)
        planned = [call.args[0] for call in steps.call_args_list]
        self.assertIn("test-included.py", planned)
        self.assertNotIn("test-excluded.py", planned)
        self.assertEqual(code, 0, errors)

    def test_missing_environment_stops_before_running_any_check(self):
        with patch.object(self.checks, "check_environment", side_effect=self.checks.EnvironmentError("codex missing")), \
             patch.object(self.checks, "run_step") as run:
            code, _, errors = self.capture(self.checks.main)
        self.assertEqual(code, 1)
        self.assertIn("codex missing", errors)
        run.assert_not_called()

    def test_successful_exit_does_not_hide_skipped_checks(self):
        for output in ("SKIP sandbox: no codex\n", "OK (skipped=1)\n", "Skipped browser\n"):
            with self.subTest(output=output), patch.object(self.checks.subprocess, "run", return_value=
                    subprocess.CompletedProcess([], 0, output, "")):
                code, _, errors = self.capture(self.checks.run_step, "fixture", ["fixture"])
                self.assertEqual(code, 1)
                self.assertIn("skipped", errors)

    def test_no_skips_and_command_failures(self):
        with patch.object(self.checks.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "OK (skipped=0)", "")):
            self.assertEqual(self.capture(self.checks.run_step, "fixture", ["fixture"])[0], 0)
        with patch.object(self.checks.subprocess, "run", side_effect=FileNotFoundError("missing tool")):
            self.assertEqual(self.capture(self.checks.run_step, "fixture", ["fixture"])[0], 1)

    def test_artifacts_must_be_tracked_and_git_errors_stay_errors(self):
        traces = self.package / "testdata/golden-traces"
        artifacts = traces / "valid/fixture/artifacts"
        artifacts.mkdir(parents=True)
        (traces / "manifest.json").write_text(json.dumps({"cases": [{"path": "valid/fixture", "mode": "full"}]}))
        sentinel = artifacts / ".gitkeep"
        sentinel.touch()
        code, _, _ = self.capture(self.checks.run_golden_trace_artifacts_guard)
        self.assertEqual(code, 1)
        self.git("add", ".")
        code, _, _ = self.capture(self.checks.run_golden_trace_artifacts_guard)
        self.assertEqual(code, 0)
        self.git("rm", "--cached", str(sentinel))
        code, _, _ = self.capture(self.checks.run_golden_trace_artifacts_guard)
        self.assertEqual(code, 1)
        error = subprocess.CompletedProcess(["git"], 128, "", "fatal: index unavailable")
        with patch.object(self.checks.subprocess, "run", return_value=error):
            code, _, err = self.capture(self.checks.run_golden_trace_artifacts_guard)
        self.assertEqual(code, 1)
        self.assertIn("index unavailable", err)
        self.assertNotIn("- valid/fixture/artifacts", err)
        self.assertNotIn("must track", err)


if __name__ == "__main__":
    unittest.main()
