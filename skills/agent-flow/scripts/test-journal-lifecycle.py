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
        receipt = finalize(self.f.run, identifier='final', status='completed', summary='Implemented and checked', source=self.f.source)
        final = self.f.snapshot().json('final.json')
        self.assertEqual(final['status'], 'completed')
        with patch.object(self.f.source, 'read', side_effect=AssertionError('replay must not reread sources')):
            self.assertEqual(finalize(self.f.run, identifier='final', status='completed', summary='Implemented and checked', source=self.f.source), receipt)
        with self.assertRaisesRegex(JournalError, 'payload conflict'):
            finalize(self.f.run, identifier='final', status='completed', summary='Different', source=self.f.source)
        with self.assertRaisesRegex(JournalError, 'closed'):
            finalize(self.f.run, identifier='new-final', status='completed', summary='Again', source=self.f.source)

    def test_failed_final_requires_reason_and_records_unverified(self):
        self.f.reviewed()
        report = {'status': 'failed', 'summary': 'Inspected available sources', 'reason': 'Tests forbidden by user',
                  'evidence': [self.f.ref(fixture.QA_ID)],
                  'criteria': {'ac-1': {'status': 'unverified', 'reason': 'Tests forbidden by user'}}}
        self.f.save('qa', report)
        self.assertFalse(self.f.snapshot().exists('final.json'))
        with self.assertRaisesRegex(JournalError, 'reason'):
            finalize(self.f.run, identifier='failed', status='failed', summary='Stopped', unverified=['tests'])
        finalize(self.f.run, identifier='failed', status='failed', summary='Stopped', reason='Tests forbidden by user', unverified=['tests'])
        self.assertEqual(self.f.snapshot().json('final.json')['unverified'], ['tests'])
        self.assertEqual(self.f.snapshot().json('reports/review.json')['status'], 'completed')

    def test_failed_qa_can_be_corrected_and_verified(self):
        self.f.reviewed()
        report = {'status': 'failed', 'summary': 'Incorrect result', 'reason': 'Result must be 3',
                  'evidence': [self.f.ref(fixture.QA_ID)],
                  'criteria': {'ac-1': {'status': 'failed', 'evidence': [self.f.ref(fixture.QA_ID)]}}}
        self.f.save('qa', report)
        before = self.f.snapshot()
        with self.assertRaisesRegex(JournalError, 'assignment failed: qa'):
            finalize(self.f.run, identifier='premature', status='completed', summary='Not ready', source=self.f.source)
        after = self.f.snapshot()
        self.assertEqual((after.revision, after.documents, after.operations),
                         (before.revision, before.documents, before.operations))
        self.f.product.write_text('answer = 3\n')
        self.f.source.sessions[fixture.ROOT_ID].append({'type': 'event_msg', 'payload': {
            'type': 'task_complete', 'turn_id': fixture.ROOT_ID + '-turn', 'last_agent_message': '{}'}})
        self.f.source.sessions[fixture.ROOT_ID].extend(fixture.session(
            fixture.ROOT_ID, 'python-worker', cwd=self.f.root, turn='author-correction',
            patch='*** Begin Patch\n*** Update File: result.py\n@@\n-answer = 2\n+answer = 3\n*** End Patch')[1:])
        self.f.save('author', turn='author-correction')
        self.f.save('review', turn='review-correction')
        qa = self.f.save('qa', turn='qa-correction')
        self.assertEqual(qa['source']['turn_id'], 'qa-correction')
        finalize(self.f.run, identifier='corrected', status='completed', summary='Corrected and verified', source=self.f.source)
        final = self.f.snapshot().json('final.json')
        self.assertEqual(final['status'], 'completed')
        self.assertNotIn('fingerprint', final)

    def test_missing_report_refuses_completed(self):
        with self.assertRaisesRegex(JournalError, 'report missing'):
            finalize(self.f.run, identifier='final', status='completed', summary='Incomplete', source=self.f.source)
        self.assertFalse(self.f.snapshot().exists('final.json'))

    def test_replaced_review_preserves_existing_qa(self):
        self.f.completed()
        previous_qa = self.f.snapshot().json('reports/qa.json')
        report = {'status': 'completed', 'summary': 'Clarified code review',
                  'evidence': [self.f.ref(fixture.REVIEWER_ID, 'review-correction')]}
        self.f.save('review', report, turn='review-correction')
        self.assertEqual(self.f.snapshot().json('reports/qa.json'), previous_qa)
        finalize(self.f.run, identifier='final', status='completed', summary='Accepted', source=self.f.source)
        self.assertEqual(self.f.snapshot().json('final.json')['status'], 'completed')

    def test_other_chat_change_during_final_publication_is_accepted(self):
        self.f.completed()
        from journal_io import transact as real_transact
        def race(run, identifier, payload, publisher):
            (self.f.root / 'other-chat.md').write_text('Another task completed\n')
            return real_transact(run, identifier, payload, publisher)
        with patch('journal_lifecycle.transact', side_effect=race):
            finalize(self.f.run, identifier='final', status='completed', summary='Result', source=self.f.source)
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
                finalize(current.run, identifier='final', status='completed', summary='Accepted', source=current.source)
                self.assertEqual(current.snapshot().json('final.json')['status'], 'completed')
                self.assertEqual(current.snapshot().json('reports/author.json')['authored_paths'], [str(current.product)])


if __name__ == '__main__':
    unittest.main()
