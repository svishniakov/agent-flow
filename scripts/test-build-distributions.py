#!/usr/bin/env python3
"""Archive, relocation, and installed-source regression checks; no model calls."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/agent-flow/scripts"))
from package_distribution import PackageError, check_package, inventory, marketplace, validate_manifest, validate_marketplace

spec = importlib.util.spec_from_file_location("build_distributions", ROOT / "scripts/build-distributions.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class Distributions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="agent-flow-distribution-tests-")
        cls.base = Path(cls.temp.name).resolve()
        cls.output = cls.base / "archives with spaces"
        cls.result = builder.build(ROOT, cls.output)
        cls.install = cls.base / "relocated packages"
        for archive in cls.result["archives"]:
            form = "plugin" if archive.endswith("-codex-plugin.zip") else "skill"
            with zipfile.ZipFile(archive) as z:
                z.extractall(cls.install / form)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_reproducible_and_identical_common_package(self):
        second = builder.build(ROOT, self.base / "second build")
        self.assertEqual(list(self.result["archives"].values()), list(second["archives"].values()))
        skill = self.install / "skill/agent-flow"
        plugin = self.install / "plugin/agent-flow/skills/agent-flow"
        self.assertEqual(inventory(skill), inventory(plugin))
        self.assertEqual((skill / "agent-flow-package.json").read_bytes(), (plugin / "agent-flow-package.json").read_bytes())

    def test_installed_check_runs_without_source_checkout(self):
        for root in (self.install / "skill/agent-flow", self.install / "plugin/agent-flow/skills/agent-flow"):
            result = subprocess.run([sys.executable, str(root / "scripts/check-installed-package.py"), "--package-only"], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["package_root"], str(root))

    def test_both_archives_create_v2_journals_and_reject_manual_final(self):
        for form, package in (("skill", self.install / "skill/agent-flow"),
                              ("plugin", self.install / "plugin/agent-flow/skills/agent-flow")):
            with self.subTest(form=form):
                project = self.base / f"{form}-lifecycle"
                project.mkdir()
                command = [sys.executable, "-B", str(package / "scripts/init-run.py"),
                           "--repo", str(project), "--slug", "archive-check", "--mode", "compact"]
                initialized = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
                run = Path(initialized.stdout.strip())
                with sqlite3.connect(run / ".journal/state.sqlite3") as connection:
                    before = connection.execute("SELECT version, revision FROM run_state").fetchone()
                self.assertEqual(before[0], 2)
                manual = subprocess.run([sys.executable, "-B", str(package / "scripts/append-timeline.py"),
                                         "--run-dir", str(run), "--role", "orchestrator", "--stage", "final",
                                         "--status", "pass", "--summary", "Manual final must fail"],
                                        capture_output=True, text=True)
                self.assertNotEqual(manual.returncode, 0, manual.stdout + manual.stderr)
                self.assertIn("finalize", manual.stdout + manual.stderr)
                with sqlite3.connect(run / ".journal/state.sqlite3") as connection:
                    self.assertEqual(before, connection.execute("SELECT version, revision FROM run_state").fetchone())
                help_result = subprocess.run([sys.executable, "-B", str(package / "scripts/journal.py"),
                                              "--run-dir", str(run), "finalize", "--help"],
                                             capture_output=True, text=True)
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                self.assertIn("--expected-generation", help_result.stdout)

    def copy_source(self, name):
        source = self.base / name
        shutil.copytree(ROOT / "skills/agent-flow", source / "skills/agent-flow", ignore=shutil.ignore_patterns("__pycache__", "*.bak"))
        shutil.copytree(ROOT / ".codex-plugin", source / ".codex-plugin")
        for name in ("README.md", "README.ru.md", "LICENSE"):
            shutil.copyfile(ROOT / name, source / name)
        return source

    def test_missing_resource_and_symlink_fail_before_output(self):
        source = self.copy_source("broken resource")
        path = source / "skills/agent-flow/references/delegation.md"
        path.unlink()
        output = self.base / "missing output"
        with self.assertRaisesRegex(PackageError, "missing"):
            builder.build(source, output)
        self.assertFalse(output.exists())
        path.symlink_to(ROOT / "skills/agent-flow/references/delegation.md")
        with self.assertRaisesRegex(PackageError, "symlink"):
            builder.build(source, output)
        self.assertFalse(output.exists())

    def test_local_files_are_not_packaged(self):
        source = self.copy_source("local additions")
        (source / "skills/agent-flow/scripts/private.txt").write_text("DO_NOT_SHIP")
        (source / "skills/agent-flow/.env").write_text("DO_NOT_SHIP")
        result = builder.build(source, self.base / "filtered output")
        for path in result["archives"]:
            with zipfile.ZipFile(path) as z:
                self.assertFalse(any("private.txt" in p or p.endswith("/.env") for p in z.namelist()))
                self.assertFalse(any((i.external_attr >> 16) & 0o170000 == 0o120000 for i in z.infolist()))

    def test_rejects_unsafe_file_list(self):
        source = self.copy_source("unsafe list")
        index = source / "skills/agent-flow/package-files.txt"
        names = index.read_text().splitlines()
        index.write_text("\n".join(sorted([*names, "../account.json"])) + "\n")
        with self.assertRaisesRegex(PackageError, "unsafe package path"):
            builder.build(source, self.base / "unsafe output")

    def test_schema_negative_cases(self):
        for field in ("policy", "category"):
            data = marketplace()
            del data["plugins"][0][field]
            with self.assertRaisesRegex(PackageError, "policy and category"):
                validate_marketplace(data)
        data = marketplace()
        data["plugins"][0]["source"]["path"] = "../outside"
        with self.assertRaises(PackageError):
            validate_marketplace(data)
        manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        for field in ("name", "version", "skills", "interface"):
            broken = dict(manifest)
            del broken[field]
            with self.assertRaises(PackageError):
                validate_manifest(broken)

    def test_no_build_tree_in_source(self):
        with self.assertRaisesRegex(PackageError, "outside source"):
            builder.build(ROOT, ROOT / "dist")

    def test_changed_outer_document_selects_a_new_plugin_cache_version(self):
        source = self.copy_source("changed readme")
        readme = source / "README.md"
        readme.write_bytes(readme.read_bytes() + b"\nRelease fixture.\n")
        result = builder.build(source, self.base / "new version")
        self.assertNotEqual(result["version"], self.result["version"])

    def test_missing_checksum_record_is_not_a_source_install(self):
        root = self.base / "missing record"
        shutil.copytree(self.install / "skill/agent-flow", root)
        (root / "agent-flow-package.json").unlink()
        with self.assertRaisesRegex(PackageError, "missing package checksum record"):
            check_package(root)

    def test_missing_plugin_metadata_is_not_a_standalone_install(self):
        for index, name in enumerate((".codex-plugin/plugin.json", "agent-flow-build.json", ".agents/plugins/marketplace.json")):
            with self.subTest(name=name):
                root = self.base / f"missing plugin metadata {index}"
                shutil.copytree(self.install / "plugin/agent-flow", root)
                (root / name).unlink()
                with self.assertRaises(PackageError):
                    check_package(root / "skills/agent-flow")

    def test_external_package_root_symlink_fails_before_output(self):
        source = self.copy_source("external package root")
        package = source / "skills/agent-flow"
        external = self.base / "external package"
        package.rename(external)
        package.symlink_to(external, target_is_directory=True)
        output = self.base / "external output"
        with self.assertRaisesRegex(PackageError, "symlink package root"):
            builder.build(source, output)
        self.assertFalse(output.exists())

    def test_project_role_directory_symlink_is_detected(self):
        package = self.install / "plugin/agent-flow/skills/agent-flow"
        project = self.base / "linked project roles"
        (project / ".codex").mkdir(parents=True)
        roles = self.base / "global roles"
        result = subprocess.run([sys.executable, str(package / "scripts/sync-codex-agent-config.py"), "--output-dir", str(roles)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        (project / ".codex/agents").symlink_to(roles, target_is_directory=True)
        result = subprocess.run([sys.executable, str(package / "scripts/check-installed-package.py"), "--roles-dir", str(roles), "--project", str(project)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("directory symlink", result.stderr)

    def test_explicit_codex_role_overrides_are_detected_without_writes(self):
        package = self.install / "plugin/agent-flow/skills/agent-flow"
        project = self.base / "explicit overrides"
        project.mkdir()
        home = project / "profile"
        roles = home / "agents"
        result = subprocess.run([sys.executable, str(package / "scripts/sync-codex-agent-config.py"), "--output-dir", str(roles)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = home / "config.toml"
        command = [sys.executable, str(package / "scripts/check-installed-package.py"), "--roles-dir", str(roles), "--project", str(project)]
        environment = dict(os.environ, CODEX_HOME=str(home))
        for filename, table in (("config.toml", "agents.qa-verifier"), ("config.toml", "profiles.legacy.agents.qa-verifier"), ("legacy.config.toml", "agents.qa-verifier")):
            config.unlink(missing_ok=True)
            config = home / filename
            config.write_text(f'[{table}]\nconfig_file = "custom.toml"\n')
            before = {p.name: p.read_bytes() for p in home.rglob("*") if p.is_file()}
            result = subprocess.run(command, env=environment, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("explicit role overrides", result.stderr)
            self.assertEqual(before, {p.name: p.read_bytes() for p in home.rglob("*") if p.is_file()})

    def test_role_drift_is_detected_in_installed_copy(self):
        package = self.install / "plugin/agent-flow/skills/agent-flow"
        project = self.base / "consumer project"
        project.mkdir()
        output = project / ".codex/agents"
        command = [sys.executable, str(package / "scripts/sync-codex-agent-config.py"), "--output-dir", str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        check = [sys.executable, str(package / "scripts/check-installed-package.py"), "--roles-dir", str(output), "--project", str(project)]
        result = subprocess.run(check, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        role = output / "qa-verifier.toml"
        role.write_text(role.read_text().replace('model = "gpt-6-astra"', 'model = "different-model"'))
        result = subprocess.run(check, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(role), result.stderr)


if __name__ == "__main__":
    unittest.main()
