#!/usr/bin/env python3
"""Archive, relocation, and installed-source regression checks; no model calls."""

from __future__ import annotations

import importlib.util
import json
import os
import re
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
from package_distribution import (PackageError, check_package, digest, inventory, json_bytes,
                                  marketplace, validate_manifest, validate_marketplace)

spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "scripts/build-plugin.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PluginBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="agent-flow-distribution-tests-")
        cls.base = Path(cls.temp.name).resolve()
        cls.output = cls.base / "archives with spaces"
        cls.result = builder.build(ROOT, cls.output)
        cls.install = cls.base / "relocated packages"
        with zipfile.ZipFile(cls.result["archive"]) as z:
            z.extractall(cls.install / "plugin")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def copy_source(self, name):
        source = self.base / name
        shutil.copytree(ROOT / "skills/agent-flow", source / "skills/agent-flow", ignore=shutil.ignore_patterns("__pycache__", "*.bak"))
        shutil.copytree(ROOT / ".codex-plugin", source / ".codex-plugin")
        for name in ("README.md", "README.ru.md", "LICENSE"):
            shutil.copyfile(ROOT / name, source / name)
        return source

    def test_reproducible_build(self):
        second = builder.build(ROOT, self.base / "second build")
        self.assertEqual(self.result["sha256"], second["sha256"])
        self.assertEqual(self.result["version"], second["version"])

    def test_installed_check_runs_without_source_checkout(self):
        root = self.install / "plugin/agent-flow/skills/agent-flow"
        result = subprocess.run([sys.executable, str(root / "scripts/check-installed-package.py"), "--package-only"], cwd=root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["package_root"], str(root))
        self.assertEqual(data["runtime_smoke"], "passed")

    def test_incomplete_source_fails_build_verification(self):
        for missing in ("journal_structure.py", "workspace_scope.py"):
            with self.subTest(missing=missing):
                source = self.copy_source("missing-" + missing)
                (source / "skills/agent-flow/scripts" / missing).unlink()
                with self.assertRaisesRegex(PackageError, "runtime smoke failed"):
                    builder.build(source, self.base / ("archive-" + missing))
                self.assertEqual(list((self.base / ("archive-" + missing)).iterdir()), [])

    def test_installed_plugin_creates_v3_journal_and_rejects_manual_final(self):
        package = self.install / "plugin/agent-flow/skills/agent-flow"
        project = self.base / "plugin-lifecycle"
        project.mkdir()
        command = [sys.executable, "-B", str(package / "scripts/init-run.py"),
                   "--repo", str(project), "--slug", "archive-check", "--mode", "compact"]
        initialized = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
        run = Path(initialized.stdout.strip())
        request = project / "progress-request.json"
        request.write_text(json.dumps({"summary": "Installed lifecycle record", "status": "active"}))
        recorded = subprocess.run([sys.executable, "-B", str(package / "scripts/journal.py"),
                                   "--run-dir", str(run), "record", "--kind", "progress", "--request-file", str(request)],
                                  capture_output=True, text=True)
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        read = subprocess.run([sys.executable, "-B", str(package / "scripts/journal.py"),
                               "--run-dir", str(run), "read", "progress.md"], capture_output=True, text=True)
        self.assertEqual(read.returncode, 0, read.stderr)
        self.assertIn("Installed lifecycle record", read.stdout)
        source, context = self.base / "plugin-source", self.base / "plugin-context"
        source.mkdir()
        context.mkdir()
        (source / "product.txt").write_text("product\n")
        (context / "context.txt").write_text("context\n")
        environment = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        for arguments in (("init", "-q"), ("add", "product.txt"),
                          ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                           "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")):
            git = subprocess.run(["git", "-C", str(source), *arguments], env=environment, capture_output=True, text=True)
            self.assertEqual(git.returncode, 0, git.stderr)
        prepared = subprocess.run([sys.executable, "-B", str(package / "scripts/task-workspace.py"),
                                   "--run-dir", str(run), "prepare", "--source", str(source),
                                   "--context-root", str(context), "--destination", str(self.base / "plugin-workspace")],
                                  env=environment, capture_output=True, text=True)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        self.assertEqual(json.loads(prepared.stdout)["version"], 2)
        with sqlite3.connect(run / ".journal/state.sqlite3") as connection:
            before = connection.execute("SELECT version, revision FROM run_state").fetchone()
        self.assertEqual(before[0], 3)
        manual_request = project / "final-event-request.json"
        manual_request.write_text(json.dumps({"summary": "Manual final must fail", "status": "pass",
                                              "stage": "final", "role": "orchestrator"}))
        manual = subprocess.run([sys.executable, "-B", str(package / "scripts/journal.py"),
                                 "--run-dir", str(run), "record", "--kind", "event",
                                 "--request-file", str(manual_request)],
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

    def test_missing_required_entry_and_symlink_fail_before_output(self):
        source = self.copy_source("broken skill")
        (source / "skills/agent-flow/SKILL.md").unlink()
        output = self.base / "missing output"
        with self.assertRaisesRegex(PackageError, "missing package resource"):
            builder.build(source, output)
        self.assertFalse(output.exists())
        source = self.copy_source("broken directory")
        shutil.rmtree(source / "skills/agent-flow/references")
        with self.assertRaisesRegex(PackageError, "missing package directory"):
            builder.build(source, output)
        self.assertFalse(output.exists())
        source = self.copy_source("linked resource")
        path = source / "skills/agent-flow/references/delegation.md"
        path.unlink()
        path.symlink_to(ROOT / "skills/agent-flow/references/delegation.md")
        with self.assertRaisesRegex(PackageError, "symlink"):
            builder.build(source, output)
        self.assertFalse(output.exists())

    def test_development_files_are_not_packaged(self):
        source = self.copy_source("local additions")
        (source / "skills/agent-flow/.env").write_text("DO_NOT_SHIP")
        (source / "skills/agent-flow/scripts/.env").write_text("DO_NOT_SHIP")
        with self.assertRaisesRegex(PackageError, "unexpected package entry"):
            (source / "skills/agent-flow/local-notes.md").write_text("DO_NOT_SHIP")
            builder.build(source, self.base / "stray output")
        (source / "skills/agent-flow/local-notes.md").unlink()
        result = builder.build(source, self.base / "filtered output")
        with zipfile.ZipFile(result["archive"]) as z:
            names = z.namelist()
            self.assertFalse(any(p.endswith("/.env") for p in names))
            self.assertFalse(any("/testdata/" in p for p in names))
            self.assertFalse(any("/scripts/test-" in p for p in names))
            self.assertFalse(any((i.external_attr >> 16) & 0o170000 == 0o120000 for i in z.infolist()))

    def test_rejects_symlinked_walked_directory(self):
        source = self.copy_source("linked directory")
        target = source / "skills/agent-flow/references/linked"
        target.symlink_to(ROOT / "skills/agent-flow/references", target_is_directory=True)
        with self.assertRaisesRegex(PackageError, "symlink"):
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

    def test_missing_record_is_not_a_source_install(self):
        root = self.base / "missing record"
        shutil.copytree(self.install / "plugin/agent-flow/skills/agent-flow", root)
        (root / "agent-flow-package.json").unlink()
        with self.assertRaisesRegex(PackageError, "missing package record"):
            check_package(root)

    def test_missing_plugin_metadata_is_not_a_standalone_install(self):
        for index, name in enumerate((".codex-plugin/plugin.json", ".agents/plugins/marketplace.json")):
            with self.subTest(name=name):
                root = self.base / f"missing plugin metadata {index}"
                shutil.copytree(self.install / "plugin/agent-flow", root)
                (root / name).unlink()
                with self.assertRaises(PackageError):
                    check_package(root / "skills/agent-flow")

    def test_record_version_must_match_plugin_manifest(self):
        root = self.base / "mutated record"
        shutil.copytree(self.install / "plugin/agent-flow", root)
        path = root / "skills/agent-flow/agent-flow-package.json"
        path.write_bytes(json_bytes({"version": "0.0.0"}))
        with self.assertRaisesRegex(PackageError, "does not match"):
            check_package(root / "skills/agent-flow")
        path.write_bytes(json_bytes({"version": "0.0.0", "sha256": "0" * 64}))
        with self.assertRaisesRegex(PackageError, "invalid package record"):
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
        original = role.read_text()
        changed, replacements = re.subn(r'^model = "[^"]+"$', 'model = "different-model"', original, count=1, flags=re.MULTILINE)
        self.assertEqual(replacements, 1)
        self.assertNotEqual(original, changed)
        role.write_text(changed)
        result = subprocess.run(check, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(role), result.stderr)


class ReleaseBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="agent-flow-release-build-tests-")
        cls.base = Path(cls.temp.name).resolve()
        cls.source = cls.base / "source"
        shutil.copytree(ROOT / "skills/agent-flow", cls.source / "skills/agent-flow",
                        ignore=shutil.ignore_patterns("__pycache__", "*.bak"))
        shutil.copytree(ROOT / ".codex-plugin", cls.source / ".codex-plugin")
        for name in ("README.md", "README.ru.md", "LICENSE"):
            shutil.copyfile(ROOT / name, cls.source / name)
        for args in (["init", "-q"], ["add", "."],
                     ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture"]):
            subprocess.run(["git", "-C", str(cls.source), *args], check=True, capture_output=True)
        cls.sha = subprocess.check_output(["git", "-C", str(cls.source), "rev-parse", "HEAD"], text=True).strip()
        cls.tag = "v" + json.loads((cls.source / ".codex-plugin/plugin.json").read_bytes())["version"]
        cls.output = cls.base / "release"
        cls.result = builder.build(cls.source, cls.output, cls.sha, release_tag=cls.tag)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def verify(self, path):
        files, version = builder.release_files(self.source, self.sha, self.tag)
        return builder.verify_archive(path, files, version)

    def test_release_cli_builds_verified_single_archive(self):
        output = self.base / "release-cli"
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/build-plugin.py"),
                                 "--source", str(self.source), "--output", str(output),
                                 "--commit-sha", self.sha, "--release-tag", self.tag],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["version"], self.tag[1:])
        self.assertEqual(data["archive"], str(output / f"agent-flow-{self.tag[1:]}-codex-plugin.zip"))
        self.assertEqual([p.name for p in output.iterdir()],
                         [f"agent-flow-{self.tag[1:]}-codex-plugin.zip"])
        print("release-tag-match-ok release-single-plugin-archive release-unpacked-plugin-check-passed")

    def test_release_rebuild_has_identical_bytes(self):
        output = self.base / "release-rebuilt"
        builder.build(self.source, output, self.sha, release_tag=self.tag)
        self.assertEqual({p.name: p.read_bytes() for p in output.iterdir()},
                         {p.name: p.read_bytes() for p in self.output.iterdir()})
        print("release-rebuild-identical")

    def test_release_build_is_hermetic_for_untracked_and_ignored_files(self):
        ignored = self.source / "skills/agent-flow/scripts/.env.production"
        ignored.write_text("SECRET=1\n")
        untracked = self.source / "skills/agent-flow/scripts/untracked-notes.py"
        untracked.write_text("print('local only')\n")
        result = builder.build(self.source, self.base / "hermetic", self.sha, release_tag=self.tag)
        with zipfile.ZipFile(result["archive"]) as archive:
            names = archive.namelist()
        self.assertFalse(any(".env.production" in name for name in names))
        self.assertFalse(any("untracked-notes" in name for name in names))
        ignored.unlink()
        untracked.unlink()
        print("release-build-hermetic-untracked-ignored")

    def test_release_invalid_cli_identity_fails_before_output(self):
        cases = [(self.tag, None, "require --commit-sha"),
                 ("v01.0.0", self.sha, "release-tag"),
                 ("v1.0.0-rc.1", self.sha, "release-tag"),
                 ("v1.0.0+build", self.sha, "release-tag"),
                 ("vbad", self.sha, "release-tag"),
                 ("v999.0.0", self.sha, "manifest version"),
                 (self.tag, "0" * 40, "Git HEAD")]
        for index, (tag, sha, error) in enumerate(cases):
            output = self.base / f"invalid-release-{index}"
            args = ["--commit-sha", sha] if sha is not None else []
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/build-plugin.py"),
                                     "--source", str(self.source), "--output", str(output),
                                     "--release-tag", tag, *args], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(error, result.stderr)
            self.assertFalse(output.exists())
        print("release-tag-invalid-rejected release-tag-mismatch-rejected release-source-sha-rejected")

    def test_release_dirty_source_and_overwrite_rejected(self):
        path = self.source / "README.md"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"changed")
            with self.assertRaisesRegex(PackageError, "tracked source"):
                builder.build(self.source, self.base / "release-dirty", self.sha, release_tag=self.tag)
        finally:
            path.write_bytes(original)
        archive = Path(self.result["archive"])
        archive.write_bytes(b"keep existing")
        try:
            with self.assertRaisesRegex(PackageError, "different bytes"):
                builder.build(self.source, self.output, self.sha, release_tag=self.tag)
        finally:
            builder.build(self.source, self.base / "release-restored", self.sha, release_tag=self.tag)
            archive.write_bytes((self.base / "release-restored" / archive.name).read_bytes())

    def test_written_archive_mutation_is_rejected(self):
        archive = Path(self.result["archive"])
        for index, mutation in enumerate(("bytes", "extra-member", "unsafe-member", "wrong-name")):
            with self.subTest(mutation=mutation):
                directory = self.base / f"mutated-{index}"
                directory.mkdir()
                path = directory / archive.name
                if mutation == "wrong-name":
                    path = directory / "agent-flow-0.0.0-codex-plugin.zip"
                    path.write_bytes(archive.read_bytes())
                    with self.assertRaisesRegex(PackageError, "unexpected archive name"):
                        self.verify(path)
                    continue
                with zipfile.ZipFile(archive) as zipped:
                    files = {name: zipped.read(name) for name in zipped.namelist()}
                if mutation == "bytes":
                    files["agent-flow/skills/agent-flow/SKILL.md"] += b"changed"
                elif mutation == "extra-member":
                    files["agent-flow/extra"] = b"extra"
                else:
                    files["../escape"] = b"escape"
                path.write_bytes(builder.archive_bytes(files))
                with self.assertRaises(PackageError):
                    self.verify(path)
        self.assertFalse((self.base / "escape").exists())
        print("release-archive-mutation-rejected")


if __name__ == "__main__":
    unittest.main()
