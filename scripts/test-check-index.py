#!/usr/bin/env python3
"""Real Git fixtures for index isolation; full-suite acceptance is run separately."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPTS = Path(__file__).resolve().parent


class IndexHookTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="agent-flow-hook-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        self.temporary = self.root / "temporary"
        self.temporary.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_") and key != "AGENT_FLOW_PRE_COMMIT_RUNNING"}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", TMPDIR=str(self.temporary),
                        PYTHONDONTWRITEBYTECODE="1")
        self.git("init", "-q", "--template=")
        self.git("config", "user.name", "Hook Test")
        self.git("config", "user.email", "hook@localhost")
        (self.repo / "scripts").mkdir()
        for name in ("check-index.py", "install-pre-commit.py"):
            shutil.copyfile(SCRIPTS / name, self.repo / "scripts" / name)
        self.write("file with spaces.txt", "good\n")
        self.write("remove.txt", "remove\n")
        self.write("rename.txt", "rename\n")
        self.checker("from pathlib import Path\nassert Path('file with spaces.txt').read_text() == 'good\\n'\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture baseline")

    def command(self, args, *, env=None, check=True):
        result = subprocess.run(args, cwd=self.repo, env=env or self.env,
                                capture_output=True, text=True, timeout=45)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def git(self, *args, check=True):
        return self.command(["git", *args], check=check)

    def write(self, name, text):
        (self.repo / name).write_text(text)

    def checker(self, text):
        self.write("scripts/check-all.py", text)

    def install(self, *args, check=True):
        return self.command([sys.executable, str(self.repo / "scripts/install-pre-commit.py"), *args], check=check)

    def state(self):
        files = {}
        for path in self.repo.rglob("*"):
            if ".git" in path.relative_to(self.repo).parts:
                continue
            if path.is_symlink():
                files[str(path.relative_to(self.repo))] = ("link", os.readlink(path))
            elif path.is_file():
                files[str(path.relative_to(self.repo))] = (path.stat().st_mode & 0o777, path.read_bytes())
        index = (self.repo / ".git/index").read_bytes()
        entries = self.git("ls-files", "--stage", "-z").stdout
        return files, index, entries

    def assert_clean_temp(self):
        self.assertEqual(list(self.temporary.iterdir()), [])

    def test_staged_error_blocks_real_commit_despite_unstaged_fix(self):
        self.install()
        self.write("file with spaces.txt", "bad\n")
        self.git("add", "file with spaces.txt")
        self.write("file with spaces.txt", "good\n")
        before = self.state()
        head = self.git("rev-parse", "HEAD").stdout
        result = self.git("commit", "-qm", "must fail", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AssertionError", result.stderr)
        after = self.state()
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[2], after[2])
        self.assertEqual(head, self.git("rev-parse", "HEAD").stdout)
        self.assert_clean_temp()

    def test_correct_index_commits_despite_unstaged_error_and_operations(self):
        self.install()
        self.checker("from pathlib import Path\n"
                     "assert Path('file with spaces.txt').read_text() == 'good\\n'\n"
                     "assert Path('new file.txt').read_text() == 'added\\n'\n"
                     "assert not Path('remove.txt').exists()\n"
                     "assert not Path('rename.txt').exists()\n"
                     "assert Path('renamed file.txt').read_text() == 'rename\\n'\n")
        self.write("new file.txt", "added\n")
        self.git("rm", "remove.txt")
        self.git("mv", "rename.txt", "renamed file.txt")
        self.git("add", "scripts/check-all.py", "new file.txt")
        self.write("file with spaces.txt", "unstaged error\n")
        self.write("new file.txt", "unstaged change\n")
        self.write("untracked.txt", "private\n")
        before = self.state()
        result = self.git("commit", "-qm", "index only")
        self.assertIn("PASS staged full suite", result.stdout + result.stderr)
        after = self.state()
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[2], after[2])
        self.assertEqual(self.git("show", "HEAD:new file.txt").stdout, "added\n")
        self.assert_clean_temp()

    def test_direct_hook_preserves_exact_index_bytes_on_success_and_failure(self):
        self.install()
        for content, expected in (("good\n", 0), ("bad\n", 1)):
            with self.subTest(content=content):
                self.write("file with spaces.txt", content)
                self.git("add", "file with spaces.txt")
                before = self.state()
                result = self.command([str(self.repo / ".git/hooks/pre-commit")], check=False)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertEqual(before, self.state())
                self.assert_clean_temp()

    def test_interrupt_preserves_exact_index_and_files(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig):
                cue = self.root / f"ready-{sig}"
                self.checker(f"from pathlib import Path\nimport time\nPath({str(cue)!r}).write_text('ready')\ntime.sleep(60)\n")
                self.git("add", "scripts/check-all.py")
                before = self.state()
                process = subprocess.Popen([sys.executable, str(self.repo / "scripts/check-index.py")],
                                           cwd=self.repo, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 20
                    while not cue.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.03)
                    self.assertTrue(cue.exists(), "checker did not start")
                    process.send_signal(sig)
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 128 + sig, stdout + stderr)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
                self.assertEqual(before, self.state())
                self.assert_clean_temp()

    def test_no_checkout_filters_or_export_ignore(self):
        self.write(".gitattributes", '*.txt export-ignore text eol=crlf\n')
        self.git("add", ".gitattributes")
        self.checker("from pathlib import Path\nassert Path('file with spaces.txt').read_bytes() == b'good\\n'\n")
        self.git("add", "scripts/check-all.py")
        self.command([sys.executable, str(self.repo / "scripts/check-index.py")])
        self.assert_clean_temp()

    def test_real_commit_interrupted_preserves_index_entries_and_files(self):
        self.install()
        cue = self.root / "commit-started"
        self.checker(f"from pathlib import Path\nimport time\nPath({str(cue)!r}).write_text('ready')\ntime.sleep(60)\n")
        self.git("add", "scripts/check-all.py")
        before = self.state()
        head = self.git("rev-parse", "HEAD").stdout
        process = subprocess.Popen(["git", "commit", "-qm", "interrupted"], cwd=self.repo, env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 20
            while not cue.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.03)
            self.assertTrue(cue.exists(), "commit hook did not start")
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=10)
            self.assertNotEqual(process.returncode, 0)
            deadline = time.monotonic() + 5
            while list(self.temporary.iterdir()) and time.monotonic() < deadline:
                time.sleep(0.03)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        after = self.state()
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[2], after[2])
        self.assertEqual(head, self.git("rev-parse", "HEAD").stdout)
        self.assert_clean_temp()

    def test_staged_modes_and_internal_symlink_are_preserved(self):
        executable = self.repo / "executable.sh"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        (self.repo / "link").symlink_to("file with spaces.txt")
        self.checker("from pathlib import Path\nimport os\n"
                     "assert Path('link').is_symlink()\n"
                     "assert os.readlink('link') == 'file with spaces.txt'\n"
                     "assert Path('executable.sh').stat().st_mode & 0o111\n")
        self.git("add", ".")
        self.command([sys.executable, str(self.repo / "scripts/check-index.py")])
        self.assert_clean_temp()

    def test_external_symlink_refuses_without_touching_target(self):
        outside = self.root / "outside"
        outside.write_text("unchanged")
        (self.repo / "link").symlink_to(outside)
        self.git("add", "link")
        before = self.state()
        result = self.command([sys.executable, str(self.repo / "scripts/check-index.py")], check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("staged symlink escapes", result.stderr)
        self.assertEqual(outside.read_text(), "unchanged")
        self.assertEqual(before, self.state())
        self.assert_clean_temp()

    def test_respects_alternate_index_and_split_index(self):
        self.git("update-index", "--split-index")
        alternate = self.root / "alternate index"
        alternate.write_bytes((self.repo / ".git/index").read_bytes())
        self.write("file with spaces.txt", "bad\n")
        self.git("add", "file with spaces.txt")
        before = self.state()
        alternate_bytes = alternate.read_bytes()
        self.command([sys.executable, str(self.repo / "scripts/check-index.py")],
                     env=self.env | {"GIT_INDEX_FILE": str(alternate)})
        self.assertEqual(before, self.state())
        self.assertEqual(alternate_bytes, alternate.read_bytes())
        self.assert_clean_temp()

    def test_interrupt_stops_child_that_ignores_term(self):
        cue = self.root / "child-pid"
        child = ("import os,signal,time; from pathlib import Path; "
                 "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                 f"Path({str(cue)!r}).write_text(str(os.getpid())); time.sleep(60)")
        self.checker(f"import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',{child!r}])\ntime.sleep(60)\n")
        self.git("add", "scripts/check-all.py")
        process = subprocess.Popen([sys.executable, str(self.repo / "scripts/check-index.py")],
                                   cwd=self.repo, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child_pid = None
        try:
            deadline = time.monotonic() + 20
            while not cue.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.03)
            self.assertTrue(cue.exists())
            child_pid = int(cue.read_text())
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=10)
            self.assertEqual(process.returncode, 143)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    child_pid = None
                    break
                time.sleep(0.03)
            self.assertIsNone(child_pid, "checker left a running child")
            self.assert_clean_temp()
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            if child_pid is not None:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_intent_to_add_is_not_in_snapshot(self):
        self.write("intent.txt", "not staged\n")
        self.git("add", "-N", "intent.txt")
        self.checker("from pathlib import Path\nassert not Path('intent.txt').exists()\n")
        self.git("add", "scripts/check-all.py")
        self.command([sys.executable, str(self.repo / "scripts/check-index.py")])

    def test_missing_staged_checker_and_recursion_refuse(self):
        self.install()
        self.git("rm", "scripts/check-all.py")
        missing = self.command([str(self.repo / ".git/hooks/pre-commit")], check=False)
        self.assertIn("staged scripts/check-all.py is missing", missing.stderr)
        recursion = self.command([str(self.repo / ".git/hooks/pre-commit")],
                                 env=self.env | {"AGENT_FLOW_PRE_COMMIT_RUNNING": "1"}, check=False)
        self.assertIn("recursive", recursion.stderr)
        self.assertNotEqual(recursion.returncode, 0)
        self.assert_clean_temp()

    def test_installed_runner_ignores_unstaged_runner_change(self):
        self.install()
        self.write("scripts/check-index.py", "raise RuntimeError('unstaged runner')\n")
        self.command([str(self.repo / ".git/hooks/pre-commit")])

    def test_install_is_idempotent_and_preserves_other_hooks(self):
        hooks = self.repo / ".git/hooks"
        hooks.mkdir()
        other = hooks / "commit-msg"
        other.write_text("#!/bin/sh\nexit 0\n")
        other.chmod(0o755)
        self.install()
        before = {p.name: p.read_bytes() for p in hooks.iterdir()}
        config = (self.repo / ".git/config").read_bytes()
        self.install()
        self.assertEqual(before, {p.name: p.read_bytes() for p in hooks.iterdir()})
        self.assertEqual(config, (self.repo / ".git/config").read_bytes())

    def test_existing_hook_and_hooks_path_conflicts_preserve_everything(self):
        hooks = self.repo / ".git/hooks"
        hooks.mkdir()
        existing = hooks / "pre-commit"
        existing.write_text("user hook\n")
        before = (self.repo / ".git/config").read_bytes()
        self.assertNotEqual(self.install(check=False).returncode, 0)
        self.assertEqual(existing.read_text(), "user hook\n")
        self.assertEqual(list(hooks.iterdir()), [existing])
        self.assertEqual(before, (self.repo / ".git/config").read_bytes())
        self.git("config", "core.hooksPath", "custom hooks")
        before = (self.repo / ".git/config").read_bytes()
        result = self.install(check=False)
        self.assertIn("core.hooksPath already configured", result.stderr)
        self.assertEqual(before, (self.repo / ".git/config").read_bytes())
        self.assertEqual(existing.read_text(), "user hook\n")


if __name__ == "__main__":
    unittest.main()
