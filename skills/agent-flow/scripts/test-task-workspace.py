#!/usr/bin/env python3
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import task_workspace as workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-flow-workspace-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "source"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def test_binding_records_paths_without_reading_checkout_contents(self):
        file = self.root / "dirty.txt"
        file.write_text("staged")
        subprocess.run(["git", "-C", str(self.root), "add", "dirty.txt"], check=True)
        file.write_text("user edits")
        index = (self.root / ".git/index").read_bytes()
        with patch.object(subprocess, "run", side_effect=AssertionError("binding must not invoke git")), \
             patch.object(Path, "read_bytes", side_effect=AssertionError("binding must not read files")):
            scope = workspace.bind_workspace(self.root)
        self.assertEqual(scope, {"source_root": str(self.root), "roots": [{"path": str(self.root)}],
                                 "context": [], "memory": []})
        self.assertEqual(file.read_text(), "user edits")
        self.assertEqual((self.root / ".git/index").read_bytes(), index)
        self.assertEqual(set(self.base.iterdir()), {self.root})

    def test_file_operations_do_not_change_bound_scope(self):
        file = self.root / "file"
        file.write_text("before")
        scope = workspace.bind_workspace(self.root)
        file.write_text("after")
        file.chmod(0o755)
        file.unlink()
        (self.root / "another-chat.txt").write_text("another task")
        self.assertEqual(workspace.bind_workspace(self.root), scope)

    def test_expansion_retains_existing_paths_and_adds_roots(self):
        extra = self.base / "other"
        extra.mkdir()
        context = self.base / "requirements.md"
        context.write_text("Requirements")
        scope = workspace.bind_workspace(self.root, memory=[str(context)])
        expanded = workspace.expand_workspace(scope, [{"path": str(extra)}], context=[str(context)])
        self.assertEqual(expanded["roots"], sorted([{"path": str(self.root)}, {"path": str(extra)}],
                                                  key=lambda item: item["path"]))
        self.assertEqual(expanded["context"], [str(context)])
        self.assertEqual(expanded["memory"], [str(context)])
        self.assertEqual(scope["roots"], [{"path": str(self.root)}])

    def test_explicit_selections_merge_without_reading_files(self):
        scope = workspace.bind_workspace(self.root, roots=[{"path": str(self.root), "include": ["first"]}])
        with patch.object(subprocess, "run", side_effect=AssertionError("expansion must not invoke git")):
            expanded = workspace.expand_workspace(scope, [{"path": str(self.root), "include": ["second"]}])
        self.assertEqual(expanded["roots"], [{"path": str(self.root), "include": ["first", "second"]}])

    def test_nested_repositories_need_no_inventory(self):
        nested = self.root / "nested"
        nested.mkdir()
        subprocess.run(["git", "init", "-q", str(nested)], check=True)
        scope = workspace.bind_workspace(self.root)
        self.assertEqual(scope["roots"], [{"path": str(self.root)}])
        expanded = workspace.expand_workspace(scope, [{"path": str(nested)}])
        self.assertIn({"path": str(nested)}, expanded["roots"])

    def test_context_and_memory_are_paths_independent_of_contents(self):
        document = self.base / "plan.md"
        document.write_text("Original requirements")
        scope = workspace.bind_workspace(self.root, context=[str(document)], memory=[str(document)])
        document.write_text("Updated requirements")
        self.assertEqual(workspace.bind_workspace(self.root, context=[str(document)], memory=[str(document)]), scope)
        self.assertEqual(scope["context"], [str(document)])
        self.assertEqual(scope["memory"], [str(document)])

    def test_outside_symbolic_parent_is_not_an_allowed_output(self):
        outside = self.base / "outside"
        outside.mkdir()
        secret = outside / "secret"
        secret.write_text("outside")
        link = self.root / "link"
        link.symlink_to(outside, target_is_directory=True)
        scope = workspace.bind_workspace(self.root)
        self.assertFalse(workspace.path_allowed(scope, link / "secret"))
        self.assertEqual(secret.read_text(), "outside")

    def test_missing_context_is_rejected(self):
        missing = self.base / "missing"
        with self.assertRaises(workspace.WorkspaceError):
            workspace.bind_workspace(self.root, context=[str(missing)])
        scope = workspace.bind_workspace(self.root)
        with self.assertRaises(workspace.WorkspaceError):
            workspace.expand_workspace(scope, context=[str(missing)])


if __name__ == "__main__":
    unittest.main()
