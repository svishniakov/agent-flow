#!/usr/bin/env python3
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from journal_io import JournalError, JournalSnapshot


def load(name, filename):
    spec = spec_from_file_location(name, Path(__file__).with_name(filename))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


init = load('init_test_command', 'init-run.py')
base = load('init_test_evidence', 'test-verification-evidence.py')


class InitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='af-init-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.plan = self.root / 'todo.md'
        self.plan.write_text('## Sample\n\n### Критерии приёмки\n\n- Result is correct\n')
        self.product = self.root / 'result.py'
        self.product.write_text('answer = 1\n')
        self.run = self.root / '.agent-work/runs/sample'
        self.source = base.SyntheticSource()
        self.source.sessions[base.ROOT_ID] = base.session(base.ROOT_ID, 'python-worker', cwd=self.root)
        self.inputs = {'repo': self.root, 'summary': 'Sample implementation', 'root_agent_id': base.ROOT_ID,
                       'requirement_source': {'path': 'todo.md', 'task_heading': 'Sample', 'criteria_heading': 'Критерии приёмки'},
                       'identifier': 'initialize-sample', 'source': self.source}

    def test_init_creates_only_database_preserving_checkout(self):
        before = {path.name: path.read_bytes() for path in (self.plan, self.product)}
        index = subprocess.run(['git', '-C', str(self.root), 'ls-files', '--stage'], capture_output=True, check=True).stdout
        snapshot = init.initialize(self.run, **self.inputs)
        self.assertEqual(set(snapshot.documents), {'task.json', 'scope.json'})
        self.assertEqual([path.relative_to(self.run).as_posix() for path in self.run.rglob('*') if path.is_file()],
                         ['.journal/state.sqlite3'])
        self.assertEqual({path.name: path.read_bytes() for path in (self.plan, self.product)}, before)
        self.assertEqual(subprocess.run(['git', '-C', str(self.root), 'ls-files', '--stage'], capture_output=True, check=True).stdout, index)
        self.assertIsNotNone(snapshot.operation_receipt(self.inputs['identifier']))

    def test_exact_repeat_preserves_identity_revision_and_receipts(self):
        first = init.initialize(self.run, **self.inputs)
        again = init.initialize(self.run, **self.inputs)
        self.assertEqual((first.run_uuid, first.revision, first.documents, first.operations),
                         (again.run_uuid, again.revision, again.documents, again.operations))

    def test_changed_init_inputs_refuse_without_mutation(self):
        first = init.initialize(self.run, **self.inputs)
        context = self.root / 'context.txt'
        context.write_text('Declared context\n')
        cases = ({'summary': 'Different task'}, {'identifier': 'different-init'},
                 {'roots': [{'path': str(self.product)}]}, {'context': [str(context)]}, {'memory': [str(context)]})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(JournalError):
                init.initialize(self.run, **{**self.inputs, **changes})
            current = JournalSnapshot.open(self.run)
            self.assertEqual((first.revision, first.documents, first.operations),
                             (current.revision, current.documents, current.operations))

    def test_invalid_requirements_or_root_identity_create_no_run(self):
        for changes in ({'requirement_source': {**self.inputs['requirement_source'], 'task_heading': 'Absent'}},
                        {'root_agent_id': base.QA_ID}):
            with self.subTest(changes=changes), self.assertRaises(JournalError):
                init.initialize(self.run, **{**self.inputs, **changes})
            self.assertFalse(self.run.exists())

    def test_cli_outputs_existing_run_metadata(self):
        args = ['--repo', str(self.root), '--slug', 'cli', '--summary', 'Sample implementation',
                '--root-agent-id', base.ROOT_ID, '--requirements', 'todo.md', '--task-heading', 'Sample',
                '--operation-id', 'cli-init']
        outputs = []
        for _ in range(2):
            output = StringIO()
            with patch.object(init, 'CodexSessionSource', return_value=self.source), redirect_stdout(output):
                self.assertEqual(init.main(args), 0)
            outputs.append(json.loads(output.getvalue()))
        self.assertEqual(outputs[0], outputs[1])
        snapshot = JournalSnapshot.open(Path(outputs[0]['run_dir']))
        self.assertEqual(outputs[0]['run_uuid'], snapshot.run_uuid)
        self.assertEqual(outputs[0]['revision'], snapshot.revision)


if __name__ == '__main__':
    unittest.main()
