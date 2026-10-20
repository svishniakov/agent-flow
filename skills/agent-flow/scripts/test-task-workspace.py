#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import task_workspace as workspace


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-flow-inventory-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "source"
        self.repository(self.root)

    def repository(self, path):
        path.mkdir(parents=True)
        self.git(path, "init", "-q")

    def git(self, path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.STDOUT)

    def test_dirty_checkout_and_index_are_unchanged(self):
        file = self.root / "dirty.txt"
        file.write_text("staged")
        self.git(self.root, "add", "dirty.txt")
        file.write_text("user edits")
        index = (self.root / ".git/index").read_bytes()
        status = self.git(self.root, "status", "--porcelain=v1", "-z")
        scope = workspace.bind_workspace(self.root)
        self.assertIsNone(scope["git"][str(self.root)]["head"])
        self.assertEqual(workspace.changed_paths(scope), [])
        self.assertEqual(file.read_text(), "user edits")
        self.assertEqual((self.root / ".git/index").read_bytes(), index)
        self.assertEqual(self.git(self.root, "status", "--porcelain=v1", "-z"), status)
        self.assertNotIn("content", json.dumps(scope))
        self.assertEqual(set(self.base.iterdir()), {self.root})

    def test_edit_add_delete_and_normal_file_mode(self):
        old = self.root / "old"
        removed = self.root / "removed"
        mode = self.root / "mode"
        for path in (old, removed, mode):
            path.write_text("before")
        scope = workspace.bind_workspace(self.root)
        old.write_text("after")
        removed.unlink()
        mode.chmod(0o755 if mode.stat().st_mode & 0o777 != 0o755 else 0o644)
        added = self.root / "added"
        added.write_text("new")
        self.assertEqual(workspace.changed_paths(scope), sorted(map(str, (old, removed, mode, added))))

    def test_ignored_outputs_excluded_tracked_ignored_included(self):
        (self.root / ".gitignore").write_text("build/\n")
        output = self.root / "build"
        output.mkdir()
        tracked = output / "kept"
        tracked.write_text("tracked")
        self.git(self.root, "add", "-f", "build/kept")
        (output / "generated").write_text("ignored")
        scope = workspace.bind_workspace(self.root)
        names = {entry["path"] for entry in scope["initial"]}
        self.assertIn("build/kept", names)
        self.assertNotIn("build/generated", names)
        (output / "generated").write_text("other output")
        self.assertEqual(workspace.changed_paths(scope), [])

    def test_explicit_ignored_selection_and_task_storage_exclusion(self):
        (self.root / ".gitignore").write_text("input/\n")
        inputs = self.root / "input"
        inputs.mkdir()
        (inputs / "required").write_text("selected")
        run = self.root / "run"
        run.mkdir()
        (run / "journal.db").write_text("technical")
        scope = workspace.bind_workspace(self.root, roots=[{"path": str(inputs), "include": ["required"]}],
                                         run_dir=run)
        self.assertIn(str(inputs / "required"), [str(Path(e["root"]) / e["path"]) for e in scope["initial"]])
        (run / "journal.db").write_text("updated")
        self.assertEqual(workspace.changed_paths(scope, run), [])

    def test_expansion_preserves_prior_initial_and_changes(self):
        file = self.root / "file"
        file.write_text("before")
        scope = workspace.bind_workspace(self.root)
        file.write_text("after")
        extra = self.base / "other"
        extra.mkdir()
        (extra / "file").write_text("extra")
        expanded = workspace.expand_workspace(scope, [{"path": str(extra)}])
        self.assertEqual([e for e in expanded["initial"] if e["root"] == str(self.root)], scope["initial"])
        self.assertEqual(workspace.changed_paths(expanded), [str(file)])
        self.assertNotEqual(workspace.workspace_fingerprint(scope, "a"),
                            workspace.workspace_fingerprint(expanded, "a"))

    def test_nested_selection_preserves_initial_not_rebaseline(self):
        nested = self.root / "nested"
        nested.mkdir()
        file = nested / "file"
        file.write_text("before")
        scope = workspace.bind_workspace(self.root)
        file.write_text("after")
        (nested / "new").write_text("added after initial")
        expanded = workspace.expand_workspace(scope, [{"path": str(nested)}])
        self.assertEqual(workspace.changed_paths(expanded), [str(file), str(nested / "new")])
        self.assertEqual(sum(e["path"] == "file" for e in workspace.current_inventory(expanded)), 1)

    def test_explicit_selection_expands_without_resetting_old_paths(self):
        first, second = self.root / "first", self.root / "second"
        first.write_text("before")
        second.write_text("existing input")
        scope = workspace.bind_workspace(self.root, roots=[{"path": str(self.root), "include": ["first"]}])
        first.write_text("changed")
        expanded = workspace.expand_workspace(scope, [{"path": str(self.root), "include": ["second"]}])
        self.assertEqual(workspace.changed_paths(expanded), [str(first)])
        second.write_text("changed later")
        self.assertEqual(workspace.changed_paths(expanded), [str(first), str(second)])

    def test_git_includes_add_ignored_inputs_without_replacing_or_rebaselining(self):
        (self.root / ".gitignore").write_text("ignored/\n")
        original = self.root / "original"
        original.write_text("before")
        self.git(self.root, "add", "original")
        ignored = self.root / "ignored/input"
        ignored.parent.mkdir()
        ignored.write_text("existing ignored input")
        explicit = {"path": str(self.root), "include": ["ignored/input"]}
        initial_selected = workspace.bind_workspace(self.root, roots=[explicit])
        self.assertEqual({e["path"] for e in initial_selected["initial"]},
                         {".gitignore", "original", "ignored/input"})
        scope = workspace.bind_workspace(self.root)
        original_entry = next(e for e in scope["initial"] if e["path"] == "original")
        original.write_text("after")
        new_file = self.root / "new-file"
        new_file.write_text("added after initial")
        expanded = workspace.expand_workspace(scope, [explicit])
        self.assertEqual(expanded["roots"], [explicit])
        self.assertEqual(workspace.expand_workspace(expanded, [{"path": str(self.root)}])["roots"], [explicit])
        self.assertEqual(next(e for e in expanded["initial"] if e["path"] == "original"), original_entry)
        self.assertEqual(workspace.changed_paths(expanded), sorted([str(original), str(new_file)]))
        self.assertTrue(workspace.path_allowed(expanded, original))
        ignored.write_text("changed after expansion")
        self.assertEqual(workspace.changed_paths(expanded), sorted([str(original), str(new_file), str(ignored)]))

    def test_same_name_roots_and_nested_git_are_distinct(self):
        other = self.base / "other/source"
        self.repository(other)
        nested = self.root / "nested"
        self.repository(nested)
        for root in (other, nested, self.root):
            (root / "file").write_text(root.name)
        with self.assertRaisesRegex(workspace.WorkspaceError, "nested Git root"):
            workspace.bind_workspace(self.root)
        scope = workspace.bind_workspace(self.root, roots=[{"path": str(other)}, {"path": str(nested)}])
        self.assertEqual(sum(e["path"] == "file" for e in scope["initial"]), 3)

    def test_non_git_root_nested_file_delta_has_no_directory_entries(self):
        extra = self.base / "plain-input"
        extra.mkdir()
        scope = workspace.bind_workspace(self.root, roots=[{"path": str(extra)}])
        directory = extra / "newdir"
        directory.mkdir()
        file = directory / "file"
        file.write_text("created")
        self.assertEqual(workspace.changed_paths(scope), [str(file)])
        self.assertEqual([entry["type"] for entry in workspace.current_inventory(scope)], ["file"])
        after_creation = workspace.bind_workspace(self.root, roots=[{"path": str(extra)}])
        file.unlink()
        directory.rmdir()
        self.assertEqual(workspace.changed_paths(after_creation), [str(file)])
        directory.write_text("directory replaced by regular file")
        self.assertEqual(workspace.changed_paths(after_creation), sorted([str(file), str(directory)]))

    def test_context_bytes_requirements_and_link_targets_change_fingerprint(self):
        context = self.base / "context"
        context.write_text("before")
        link = self.root / "link"
        link.symlink_to(context)
        scope = workspace.bind_workspace(self.root, context=[str(context)])
        first = workspace.workspace_fingerprint(scope, "requirements")
        context.write_text("after")
        self.assertNotEqual(first, workspace.workspace_fingerprint(scope, "requirements"))
        self.assertNotEqual(first, workspace.workspace_fingerprint(scope, "other requirements"))
        link.unlink()
        link.symlink_to("missing")
        self.assertEqual(workspace.changed_paths(scope), [str(link)])

    def test_outside_link_is_observed_without_following_or_writing(self):
        outside = self.base / "outside"
        outside.mkdir()
        secret = outside / "secret"
        secret.write_text("outside")
        mode = secret.stat().st_mode
        link = self.root / "link"
        link.symlink_to(outside, target_is_directory=True)
        scope = workspace.bind_workspace(self.root)
        self.assertEqual(next(e for e in scope["initial"] if e["path"] == "link")["target"], str(outside))
        self.assertFalse(workspace.path_allowed(scope, link / "secret"))
        with self.assertRaises(workspace.WorkspaceError):
            workspace.bind_workspace(self.root, roots=[{"path": str(link / "secret")}],
                                     context=[str(self.base / "missing")])
        self.assertEqual(secret.read_text(), "outside")
        self.assertEqual(secret.stat().st_mode, mode)

    def test_read_detects_file_changed_during_hashing(self):
        file = self.root / "file"
        file.write_bytes(b"before")
        real_read = os.read
        changed = False

        def change_after_read(fd, length):
            nonlocal changed
            result = real_read(fd, length)
            if result and not changed:
                changed = True
                file.write_bytes(b"after")
            return result

        with patch.object(workspace.os, "read", change_after_read):
            with self.assertRaisesRegex(workspace.WorkspaceError, "changed while reading"):
                workspace.observation(self.root, "file")

    def test_memory_is_explicit_and_criteria_digest_controls_freshness(self):
        todo = self.root / "To Do.md"
        todo.write_text("# Task\n## Критерии приёмки\n- [ ] Correct result\n# Other task\nOld note\n")
        product = self.root / "main.py"
        product.write_text("print(1)\n")
        scope = workspace.bind_workspace(self.root, memory=[str(todo)], context=[str(todo)])
        original = workspace.workspace_fingerprint(scope, "selected-criterion")
        todo.write_text(todo.read_text().replace("[ ]", "[x]").replace("Old note", "New unrelated note"))
        self.assertEqual(workspace.workspace_fingerprint(scope, "selected-criterion"), original)
        self.assertNotEqual(workspace.workspace_fingerprint(scope, "changed-criterion"), original)
        self.assertEqual(workspace.changed_paths(scope), [])
        expanded = workspace.expand_workspace(scope)
        self.assertEqual(expanded["memory"], scope["memory"])
        product.write_text("print(2)\n")
        self.assertNotEqual(workspace.workspace_fingerprint(scope, "selected-criterion"), original)
        todo.unlink()
        todo.symlink_to(product)
        with self.assertRaisesRegex(workspace.WorkspaceError, "memory input"):
            workspace.current_inventory(scope)


if __name__ == "__main__":
    unittest.main()
