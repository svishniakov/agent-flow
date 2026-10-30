#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile
import unittest

from journal_io import JournalError
from journal_lifecycle import finalize

spec = importlib.util.spec_from_file_location('review_outcome_fixture', Path(__file__).with_name('test-verification-evidence.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class ReviewOutcomeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='af-review-outcomes-')
        self.addCleanup(temporary.cleanup)
        self.f = fixture.Fixture(temporary.name)

    def author_report(self, outcome):
        return {'status': 'completed', 'outcome': outcome, 'summary': 'Implementation inspected; runtime checks unavailable',
                'reason': 'User has not authorized runtime checks', 'authored_paths': [str(self.f.product)],
                'evidence': [self.f.ref(fixture.ROOT_ID)]}

    def test_author_unverified_does_not_override_review_or_qa(self):
        self.f.save('author', self.author_report('unverified'))
        self.f.save('review')
        self.assertEqual(self.f.snapshot().json('reports/review.json')['outcome'], 'passed')
        self.f.save('qa', {'status': 'completed', 'outcome': 'unverified', 'summary': 'Required runtime verification unavailable',
            'reason': 'No runtime authorization', 'evidence': [self.f.ref(fixture.QA_ID)],
            'criteria': {'ac-1': {'status': 'unverified', 'reason': 'Runtime check not executed'}}})
        before = self.f.snapshot()
        with self.assertRaisesRegex(JournalError, 'terminal nonpass report must belong to root'):
            finalize(self.f.run, identifier='incomplete', report_id='qa', source=self.f.source)
        after = self.f.snapshot()
        self.assertEqual((before.documents, before.operations, before.revision),
                         (after.documents, after.operations, after.revision))

    def test_review_makes_its_own_decision_after_author_failure(self):
        self.f.save('author', self.author_report('failed'))
        self.f.save('review')
        self.f.save('qa')
        finalize(self.f.run, identifier='verified', report_id='qa', source=self.f.source)
        self.assertEqual(self.f.snapshot().json('reports/author.json')['outcome'], 'failed')
        self.assertEqual(self.f.snapshot().json('final.json')['outcome'], 'passed')

    def test_passed_qa_still_requires_passed_criteria(self):
        self.f.save('author', self.author_report('unverified'))
        self.f.save('review')
        with self.assertRaisesRegex(JournalError, 'passed QA requires passed criteria'):
            self.f.save('qa', {'status': 'completed', 'outcome': 'passed', 'summary': 'Cannot substitute code review for tests',
                'evidence': [self.f.ref(fixture.QA_ID)],
                'criteria': {'ac-1': {'status': 'unverified', 'reason': 'Not run'}}})
        self.assertFalse(self.f.snapshot().exists('reports/qa.json'))

    def test_verification_only_attestation_requires_known_author(self):
        self.f.save('author')
        with self.assertRaisesRegex(JournalError, 'existing author assignments'):
            self.f.save('review', {'status': 'completed', 'outcome': 'passed', 'summary': 'Invalid exclusion',
                'verification_only': ['missing'], 'evidence': [self.f.ref(fixture.REVIEWER_ID)]})


if __name__ == '__main__':
    unittest.main()
