#!/usr/bin/env python3
"""Focused checks for mandatory tools and explicit local dependency preparation."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_environment as environment


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EnvironmentChecks(unittest.TestCase):
    def test_missing_tools_fail_individually(self):
        for name in ("git", "node", "skills"):
            with self.subTest(name=name), patch.object(environment.shutil, "which", return_value=None):
                with self.assertRaisesRegex(environment.EnvironmentError, name):
                    environment.require_tool(name)

    def test_versions_are_checked_without_installers(self):
        with patch.object(environment, "require_tool", return_value="/tool"), \
             patch.object(environment.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "1.5.26\n", "")) as run:
            self.assertEqual(environment.require_version("skills", "1.5.26"), "1.5.26")
            self.assertEqual(run.call_args.args[0], ["/tool", "--version"])
        with patch.object(environment.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "v26.8.2\n", "")):
            self.assertEqual(environment.version_of(["node", "--version"]), "26.8.2")
        with patch.object(environment, "require_tool", return_value="/tool"), \
             patch.object(environment.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "1.5.26-alpha\n", "")):
            with self.assertRaisesRegex(environment.EnvironmentError, "1.5.26-alpha"):
                environment.require_version("skills", "1.5.26")
        with patch.object(environment, "require_tool", return_value="/tool"), \
             patch.object(environment, "version_of", return_value="22.19.0"):
            with self.assertRaisesRegex(environment.EnvironmentError, "22.20.0"):
                environment.require_version("node", "22.20.0", minimum=True)

    def test_missing_python_dependency_fails(self):
        with patch.object(environment, "require_tool", return_value="/tool"), \
             patch.object(environment, "require_version", return_value="1.2.3"), \
             patch.object(environment.importlib.metadata, "version", side_effect=environment.importlib.metadata.PackageNotFoundError):
            with self.assertRaisesRegex(environment.EnvironmentError, "Python dependency is missing"):
                environment.check_environment()

    def test_unsupported_platform_fails(self):
        with patch.object(environment.sys, "platform", "win32"):
            with self.assertRaisesRegex(environment.EnvironmentError, "cannot be skipped"):
                environment.check_environment()

    def test_foreign_prefix_is_not_modified(self):
        prepare = load_script("prepare-check-environment")
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            protected = prefix / "user-file"
            protected.write_text("keep")
            with patch.object(prepare, "require_version"), \
                 patch.object(prepare, "require_tool", return_value="/usr/bin/true"), \
                 patch.object(prepare.subprocess, "run") as run:
                with self.assertRaisesRegex(environment.EnvironmentError, "unowned"):
                    prepare.prepare(prefix)
            self.assertEqual(protected.read_text(), "keep")
            self.assertEqual(list(prefix.iterdir()), [protected])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
