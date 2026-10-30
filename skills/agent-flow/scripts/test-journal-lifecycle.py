#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from journal_io import JournalError
from journal_lifecycle import finalize

spec = importlib.util.spec_from_file_location('evidence_fixture', Path(__file__).with_name('test-verification-evidence.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='af-lifecycle-')
        self.addCleanup(self.temporary.cleanup)
        self.f = fixture.Fixture(self.temporary.name)

    def test_final_created_at_completion_and_exact_retry_after_close(self):
        self.assertFalse(self.f.snapshot().exists('final.json'))
        self.f.completed()
        receipt = finalize(self.f.run, identifier='final', report_id='qa', source=self.f.source)
        final = self.f.snapshot().json('final.json')
        self.assertEqual(final['status'], 'completed')
        with patch.object(self.f.source, 'read', side_effect=AssertionError('replay must not reread sources')):
            self.assertEqual(finalize(self.f.run, identifier='final', report_id='qa', source=self.f.source), receipt)
        with self.assertRaisesRegex(JournalError, 'payload conflict'):
            finalize(self.f.run, identifier='final', report_id='different', source=self.f.source)
        with self.assertRaisesRegex(JournalError, 'closed'):
            finalize(self.f.run, identifier='new-final', report_id='qa', source=self.f.source)

    def test_missing_report_refuses_completed(self):
        with self.assertRaisesRegex(JournalError, 'report missing'):
            finalize(self.f.run, identifier='final', report_id='qa', source=self.f.source)
        self.assertFalse(self.f.snapshot().exists('final.json'))

    def test_other_chat_change_during_final_publication_is_accepted(self):
        self.f.completed()
        from journal_io import transact as real_transact
        def race(run, identifier, payload, publisher, **kwargs):
            (self.f.root / 'other-chat.md').write_text('Another task completed\n')
            return real_transact(run, identifier, payload, publisher, **kwargs)
        with patch('journal_lifecycle.transact', side_effect=race):
            finalize(self.f.run, identifier='final', report_id='qa', source=self.f.source)
        self.assertEqual(self.f.snapshot().json('final.json')['status'], 'completed')

    def test_two_runs_in_one_repository_finish_with_separate_authored_paths(self):
        other = fixture.Fixture(self.f.root, name='other')
        self.f.save('author')
        other.save('author')
        self.f.save('review')
        other.product.write_text('answer = 3\n')
        other.save('review')
        self.f.save('qa')
        other.save('qa')
        (self.f.root / 'third-chat.md').write_text('Independent work\n')
        for current in (self.f, other):
            with self.subTest(run=current.run):
                finalize(current.run, identifier='final', report_id='qa', source=current.source)
                self.assertEqual(current.snapshot().json('final.json')['status'], 'completed')
                self.assertEqual(current.snapshot().json('reports/author.json')['authored_paths'], [str(current.product)])

    def test_failed_iteration_preserved_after_successful_correction(self):
        from journal_io import transact, encode_json
        self.f.reviewed()
        self.f.save('qa', {'status': 'completed', 'outcome': 'failed', 'summary': 'Wrong result',
            'reason': 'Must return 3', 'evidence': [self.f.ref(fixture.QA_ID)],
            'criteria': {'ac-1': {'status': 'failed', 'evidence': [self.f.ref(fixture.QA_ID)]}}})
        old = self.f.snapshot()
        updates = {}
        for key, dependencies in [('author', ['author']), ('review', ['author-next']), ('qa', ['review-next'])]:
            name = key + '-next'
            assignment = {**self.f.known[key], 'id': name, 'depends_on': dependencies, 'previous_assignment_id': key}
            if key == 'author':
                assignment['start_turn_id'] = 'author-correction'
            self.f.known[name] = assignment
            updates['assignments/' + name + '.json'] = encode_json(assignment).encode()
        transact(self.f.run, 'correction-assignments', {}, lambda current: (updates, {}))
        self.f.source.sessions[fixture.ROOT_ID].append({'type': 'event_msg', 'payload': {
            'type': 'task_complete', 'turn_id': fixture.ROOT_ID + '-turn', 'last_agent_message': '{}'}})
        self.f.source.sessions[fixture.ROOT_ID].extend(fixture.session(fixture.ROOT_ID, 'python-worker', cwd=self.f.root,
            turn='author-correction', patch='*** Begin Patch\n*** Update File: result.py\n@@\n-answer = 2\n+answer = 3\n*** End Patch')[1:])
        self.f.product.write_text('answer = 3\n')
        self.f.save('author-next', turn='author-correction')
        self.f.save('review-next', turn='review-correction')
        ref = self.f.ref(fixture.QA_ID, 'qa-correction')
        self.f.save('qa-next', {'status': 'completed', 'outcome': 'passed', 'summary': 'Correction verified',
            'evidence': [ref], 'resolves': {'qa': [ref]}, 'criteria': {'ac-1': {'status': 'passed', 'evidence': [ref]}}}, turn='qa-correction')
        finalize(self.f.run, identifier='corrected', report_id='qa-next', source=self.f.source)
        for path, content in old.documents.items():
            self.assertEqual(self.f.snapshot().read_bytes(path), content)
        self.assertEqual(self.f.snapshot().json('reports/qa.json')['outcome'], 'failed')

    def test_report_replacement_and_deletion_refused(self):
        from journal_io import transact
        self.f.completed()
        before = self.f.snapshot()
        for index, value in enumerate((b'{}', None)):
            with self.subTest(value=value), self.assertRaises(JournalError):
                transact(self.f.run, 'overwrite-' + str(index), {}, lambda current: (
                    {'reports/qa.json': value}, {}))
        self.assertEqual(self.f.snapshot().documents, before.documents)

    def test_new_author_assignment_requires_new_acceptance(self):
        from journal_io import transact, encode_json
        self.f.completed()
        assignment = {**self.f.known['author'], 'id': 'late-author', 'previous_assignment_id': 'author', 'depends_on': ['author']}
        transact(self.f.run, 'late-author', {}, lambda current: (
            {'assignments/late-author.json': encode_json(assignment).encode()}, {}))
        with self.assertRaisesRegex(JournalError, 'current author assignments'):
            finalize(self.f.run, identifier='stale', report_id='qa', source=self.f.source)
        self.assertFalse(self.f.snapshot().exists('final.json'))

    def test_terminal_stop_records_root_reason_without_rewriting_history(self):
        from journal_io import transact, encode_json
        self.f.reviewed()
        previous = dict(self.f.snapshot().documents)
        assignment = {**self.f.known['author'], 'id': 'stop', 'kind': 'analysis', 'allowed_paths': []}
        self.f.known['stop'] = assignment
        transact(self.f.run, 'stop-assignment', {}, lambda current: (
            {'assignments/stop.json': encode_json(assignment).encode()}, {}))
        self.f.source.sessions[fixture.ROOT_ID].extend([
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'call_id': 'inspection', 'name': 'exec_command', 'input': '{"cmd":"inspect current result"}'}},
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'call_id': 'inspection', 'output': '{"exit_code":0,"output":"Stopped state inspected"}'}},
        ])
        self.f.save('stop', {'status': 'interrupted', 'outcome': 'unverified', 'summary': 'Stopped',
            'reason': 'User stopped development', 'unverified': ['ac-1'], 'evidence': [{**self.f.ref(fixture.ROOT_ID), 'call_id': 'inspection'}]})
        finalize(self.f.run, identifier='stop', report_id='stop', source=self.f.source)
        final = self.f.snapshot().json('final.json')
        self.assertEqual((final['status'], final['outcome']), ('interrupted', 'unverified'))
        self.assertEqual(final['unverified'], ['ac-1'])
        for path, content in previous.items():
            self.assertEqual(self.f.snapshot().read_bytes(path), content)

    def test_revision_conflict_does_not_publish(self):
        from journal_io import transact
        before = self.f.snapshot()
        transact(self.f.run, 'other-write', {}, lambda current: ({'artifacts/other': b'other'}, {}))
        with self.assertRaisesRegex(JournalError, 'revision'):
            transact(self.f.run, 'stale-write', {}, lambda current: ({'artifacts/stale': b'stale'}, {}), expected_revision=before.revision)
        self.assertFalse(self.f.snapshot().exists('artifacts/stale'))


if __name__ == '__main__':
    unittest.main()
