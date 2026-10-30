import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from journal_io import JournalError
from verification_evidence import (CodexSessionSource, author_turn_ids, completed_turn,
                                   native_item, native_references, native_tool_record)
from agent_config import default_agents_dir, read_frontmatter, resolve_role_path, role_config


ROOT = str(UUID(int=1, version=4))


class LocalSource(CodexSessionSource):
    def __init__(self, directory):
        super().__init__()
        self.directory = Path(directory)

    def session_dir(self):
        return self.directory

    def _index_rows(self, query, parameters):
        return [(str(self.directory / 'session.jsonl'),)]


def events(count):
    yield {'type': 'session_meta', 'payload': {'id': ROOT}}
    yield {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'turn'}}
    for index in range(count):
        yield {'type': 'response_item', 'payload': {
            'type': 'function_call', 'call_id': str(index), 'name': 'exec_command',
            'arguments': '{}', 'encrypted_content': 'private'}}
        yield {'type': 'response_item', 'payload': {
            'type': 'function_call_output', 'call_id': str(index), 'output': 'ok'}}


class NativePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='af-native-performance-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'session.jsonl'

    def write(self, count, extra=()):
        values = [*events(count), *extra]
        self.path.write_text(''.join(json.dumps(value) + '\n' for value in values))
        return len(values)

    def test_long_sessions_parse_once_and_lookup_without_rescans(self):
        timings = []
        for count in (1000, 5000):
            length = self.write(count)
            source = LocalSource(self.temporary.name)
            original = json.loads
            started = time.perf_counter()
            with patch('verification_evidence.json.loads', wraps=original) as loads:
                for index in range(count):
                    call, output = native_tool_record(source, {
                        'thread_id': ROOT, 'turn_id': 'turn', 'call_id': str(index)}, root_id=ROOT)
                    self.assertEqual(call['call_id'], str(index))
                    self.assertEqual(output['output'], 'ok')
                self.assertEqual(loads.call_count, length)
                self.assertEqual(len(source._native), 1)
                self.assertEqual(len(source._own), 1)
            timings.append((count, time.perf_counter() - started))
        print('native lookup benchmark:', timings)

    def test_filtered_views_do_not_expose_unselected_or_encrypted_content(self):
        self.write(2)
        source = LocalSource(self.temporary.name)
        self.assertEqual(source.read(ROOT)[2]['payload'], {})
        selected = source.read(ROOT, event_indices={3})
        self.assertEqual(selected[2]['payload']['call_id'], '0')
        self.assertNotIn('encrypted_content', selected[2]['payload'])
        self.assertEqual(selected[3]['payload'], {})
        self.assertNotIn('private', json.dumps(selected))

    def test_native_listing_scales_with_history(self):
        timings = []
        for count in (1000, 5000):
            length = self.write(count)
            source = LocalSource(self.temporary.name)
            assignment = {'agent_id': ROOT, 'kind': 'author', 'role': 'python-worker', 'start_turn_id': 'turn'}
            snapshot = SimpleNamespace(identities={'root_agent_id': ROOT}, source_root=self.path.parent,
                                       json=lambda path: assignment)
            started = time.perf_counter()
            with patch('verification_evidence.json.loads', wraps=json.loads) as loads:
                result = native_references(snapshot, 'author', source)
                self.assertEqual(len(result['observations']), count)
                self.assertEqual(loads.call_count, length)
            timings.append((count, time.perf_counter() - started))
        print('native listing benchmark:', timings)

    def test_many_author_reports_use_turn_index(self):
        child = str(UUID(int=2, version=4))
        role = 'python-worker'
        model = role_config(read_frontmatter(resolve_role_path(default_agents_dir(), role)), role)['model']
        timings = []
        for count in (1000, 5000):
            values = [{'type': 'session_meta', 'payload': {
                'id': child, 'parent_thread_id': ROOT, 'agent_role': role,
                'source': {'subagent': {'thread_spawn': {'parent_thread_id': ROOT, 'agent_role': role}}}}}]
            for index in range(count):
                turn = str(index)
                values.extend([
                    {'type': 'event_msg', 'timestamp': '2026-10-19T10:00:00+00:00', 'payload': {'type': 'task_started', 'turn_id': turn}},
                    {'type': 'turn_context', 'payload': {'turn_id': turn, 'model': model}},
                    {'type': 'event_msg', 'timestamp': '2026-10-19T10:00:01+00:00', 'payload': {'type': 'task_complete', 'turn_id': turn, 'last_agent_message': '{}'}},
                ])
            self.path.write_text(''.join(json.dumps(value) + '\n' for value in values))
            source = LocalSource(self.temporary.name)
            started = time.perf_counter()
            with patch('verification_evidence.json.loads', wraps=json.loads) as loads:
                for index in range(count):
                    turn = str(index)
                    self.assertEqual(completed_turn(source, child, turn, ROOT, role, current=False)['answer'], '{}')
                    self.assertEqual(author_turn_ids(source, {'agent_id': child, 'start_turn_id': turn}, turn=turn), [turn])
                self.assertEqual(loads.call_count, len(values))
            timings.append((count, time.perf_counter() - started))
        print('author turn benchmark:', timings)
        values.insert(3, {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'foreign'}})
        self.path.write_text(''.join(json.dumps(value) + '\n' for value in values))
        with self.assertRaisesRegex(JournalError, 'selected turn restarted before completion'):
            completed_turn(LocalSource(self.temporary.name), child, '0', ROOT, role, current=False)

    def test_duplicate_calls_and_wrong_turn_are_rejected(self):
        self.write(1, [list(events(1))[2]])
        source = LocalSource(self.temporary.name)
        for turn in ('turn', 'foreign'):
            with self.assertRaisesRegex(JournalError, 'missing or ambiguous'):
                native_tool_record(source, {'thread_id': ROOT, 'turn_id': turn, 'call_id': '0'}, root_id=ROOT)

    def test_new_operation_reads_new_events_and_checks_item_identity(self):
        self.write(1)
        first = LocalSource(self.temporary.name)
        self.assertEqual(len(first.read(ROOT)), 4)
        extra = {'type': 'event_msg', 'payload': {
            'type': 'item_completed', 'thread_id': ROOT, 'turn_id': 'turn',
            'item': {'type': 'CommandExecution', 'id': 'item', 'status': 'completed'}}}
        self.write(1, [extra])
        second = LocalSource(self.temporary.name)
        ref = {'thread_id': ROOT, 'turn_id': 'turn', 'item_id': 'item'}
        self.assertEqual(native_item(second, ref, root_id=ROOT)['id'], 'item')
        with self.assertRaisesRegex(JournalError, 'thread or turn mismatch'):
            native_item(second, {**ref, 'turn_id': 'other'}, root_id=ROOT)
        self.assertEqual(len(first.read(ROOT)), 4)


if __name__ == '__main__':
    unittest.main()
