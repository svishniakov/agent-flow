import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from journal_io import JournalError
from verification_evidence import native_assignment_stopped, native_command_paths, native_file_change_paths


THREAD = str(UUID(int=1, version=4))


def command(script, output, code=0):
    return {'type': 'CommandExecution', 'status': 'completed', 'exit_code': code,
            'command': ['/bin/zsh', '-lc', script], 'cwd': '/repo', 'aggregated_output': output}


class EditFormats(unittest.TestCase):
    def setUp(self):
        installed = patch('verification_evidence.shutil.which', return_value='/usr/local/bin/apply_patch')
        installed.start()
        self.addCleanup(installed.stop)

    def test_escalated_patch_add_update_delete_and_move(self):
        patch = '*** Begin Patch\n*** Add File: a.txt\n+x\n*** Update File: b.txt\n@@\n-x\n+y\n*** Delete File: archive.tgz\n*** Update File: old.txt\n*** Move to: new.txt\n@@\n-x\n+y\n*** End Patch'
        item = command("/usr/local/bin/apply_patch <<'PATCH'\n" + patch + '\nPATCH',
                       'Success. Updated the following files:\nA a.txt\nM b.txt\nD archive.tgz\nM new.txt\n')
        self.assertEqual(native_command_paths(item), ['/repo/a.txt', '/repo/archive.tgz', '/repo/b.txt', '/repo/new.txt', '/repo/old.txt'])

    def test_failed_forged_or_nonstandalone_command_is_rejected(self):
        script = "apply_patch <<'PATCH'\n*** Begin Patch\n*** Delete File: archive.tgz\n*** End Patch\nPATCH"
        output = 'Success. Updated the following files:\nD archive.tgz\n'
        with self.assertRaises(JournalError):
            native_command_paths(command(script, output, 1))
        with self.assertRaises(JournalError):
            native_command_paths(command(script, 'Done!\nD unrelated.tgz\n'))
        with self.assertRaises(JournalError):
            native_command_paths(command(script, output + 'Error: failed to delete\n'))
        self.assertEqual(native_command_paths(command("echo 'Success. Updated'; exit 0", output)), [])
        with self.assertRaises(JournalError):
            native_command_paths(command(script + '\necho success', output))

    def test_binary_removal_and_native_file_change_need_no_text_content(self):
        item = command('', '/repo/archive.tgz\n')
        item['command'] = ['/bin/rm', '-v', '--', '/repo/archive.tgz']
        self.assertEqual(native_command_paths(item), ['/repo/archive.tgz'])
        item['aggregated_output'] = ''
        with self.assertRaises(JournalError):
            native_command_paths(item)
        self.assertEqual(native_file_change_paths({'type': 'FileChange', 'status': 'completed',
            'changes': {'/repo/archive.tgz': {'type': 'delete'}}}), ['/repo/archive.tgz'])

    def test_standalone_shell_binary_removal_rejects_extra_commands(self):
        item = command('/bin/rm -v -- /repo/archive.tgz', '/repo/archive.tgz\n')
        self.assertEqual(native_command_paths(item), ['/repo/archive.tgz'])
        self.assertEqual(native_command_paths(command("/bin/rm -v -- '/repo/archive name.tgz'", '/repo/archive name.tgz\n')), ['/repo/archive name.tgz'])
        self.assertEqual(native_command_paths(command('/bin/rm -v -- /repo/archive.tgz; echo success', '/repo/archive.tgz\n')), [])
        self.assertEqual(native_command_paths(command('/bin/rm -v -- /repo/$(echo archive).tgz', '/repo/archive.tgz\n')), [])

    def test_custom_patch_executable_cannot_forge_native_edit(self):
        payload = '*** Begin Patch\n*** Delete File: archive.tgz\n*** End Patch'
        output = 'Success. Updated the following files:\nD archive.tgz\n'
        item = command('', output)
        item['command'] = ['/private/tmp/apply_patch', payload]
        self.assertEqual(native_command_paths(item), [])
        item = command("/private/tmp/fake/apply_patch <<'PATCH'\n" + payload + '\nPATCH', output)
        self.assertEqual(native_command_paths(item), [])
        with patch('verification_evidence.shutil.which', return_value=None):
            self.assertEqual(native_command_paths(command("apply_patch <<'PATCH'\n" + payload + '\nPATCH', output)), [])

    def test_first_heredoc_delimiter_cannot_be_backtracked(self):
        script = "apply_patch <<'PATCH'\n*** Begin Patch\n*** End Patch\nPATCH\nprintf 'Success. Updated the following files:\\nD archive.tgz\\n'\ncat <<'PATCH' >/dev/null\n*** Delete File: archive.tgz\n*** End Patch\nPATCH"
        with self.assertRaisesRegex(JournalError, 'first heredoc delimiter'):
            native_command_paths(command(script, 'Success. Updated the following files:\nD archive.tgz\n'))
        payload = '*** Begin Patch\n*** Delete File: archive.tgz\n*** Delete File: archive.tgz\n*** End Patch'
        with self.assertRaisesRegex(JournalError, 'duplicate'):
            native_command_paths(command("apply_patch <<'PATCH'\n" + payload + '\nPATCH', 'Done!\nD archive.tgz\n'))

    def test_command_execution_registers_author_report_without_reexecution(self):
        spec = importlib.util.spec_from_file_location('native_edit_fixture', Path(__file__).with_name('test-verification-evidence.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='af-native-edit-report-') as directory:
            fixture = module.Fixture(directory)
            path = str(fixture.product)
            payload = '*** Begin Patch\n*** Update File: ' + path + '\n@@\n-answer = 1\n+answer = 2\n*** End Patch'
            item = command("apply_patch <<'PATCH'\n" + payload + '\nPATCH', 'Success. Updated the following files:\nM ' + path + '\n')
            item['id'] = 'native-shell-patch'
            item['cwd'] = str(fixture.root)
            fixture.source.sessions[module.ROOT_ID].append({'type': 'event_msg', 'payload': {
                'type': 'item_completed', 'thread_id': module.ROOT_ID, 'turn_id': module.ROOT_ID + '-turn', 'item': item}})
            with patch('subprocess.run', side_effect=AssertionError('native validation must not execute commands')) as execution:
                report = fixture.save('author', {'status': 'completed', 'outcome': 'passed', 'summary': 'Observed actual patch',
                    'authored_paths': [path], 'evidence': [{'thread_id': module.ROOT_ID, 'turn_id': module.ROOT_ID + '-turn', 'item_id': item['id']}]})
                self.assertFalse(execution.mock_calls)
            self.assertEqual(report['authored_paths'], [path])
            self.assertEqual(fixture.snapshot().json('reports/author.json'), report)

    def test_stopped_executor_completion_abort_and_new_start(self):
        class Source:
            def read(self, thread):
                return copy.deepcopy(self.events)
        source = Source()
        source.events = [{'type': 'session_meta', 'payload': {'id': THREAD}},
                         {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'one'}}]
        assignment = {'agent_id': THREAD}
        with self.assertRaises(JournalError):
            native_assignment_stopped(source, assignment)
        for terminal in ('task_complete', 'turn_aborted'):
            source.events = source.events[:2] + [{'type': 'event_msg', 'payload': {'type': terminal, 'turn_id': 'one'}}]
            proof = native_assignment_stopped(source, assignment)
            self.assertEqual(proof['event_type'], terminal)
            self.assertEqual(proof['turn_id'], 'one')
        source.events.append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'two'}})
        self.assertEqual(native_assignment_stopped(source, assignment, before_turn_id='two')['turn_id'], 'one')
        with self.assertRaises(JournalError):
            native_assignment_stopped(source, assignment, before_turn_id='one')
        with self.assertRaises(JournalError):
            native_assignment_stopped(source, assignment)
        source.events.append({'type': 'event_msg', 'payload': {'type': 'turn_aborted', 'turn_id': 'wrong'}})
        with self.assertRaisesRegex(JournalError, 'mismatch'):
            native_assignment_stopped(source, assignment)


if __name__ == '__main__':
    unittest.main()
