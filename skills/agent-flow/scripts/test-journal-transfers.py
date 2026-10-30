import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from journal_io import JournalError, transact
from journal_lifecycle import finalize
from journal_structure import read_record, write_entry

spec = importlib.util.spec_from_file_location('transfer_fixture', Path(__file__).with_name('test-verification-evidence.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='af-transfer-')
        self.addCleanup(self.temporary.cleanup)
        self.f = fixture.Fixture(Path(self.temporary.name) / 'repo')
        self.git('add', 'result.py')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Initial')
        self.target = Path(self.temporary.name).resolve() / 'next'
        self.git('worktree', 'add', '-qb', 'next', str(self.target))
        self.record('scope', {'roots': [{'path': str(self.target)}]}, 'scope')
        self.child = str(UUID(int=4, version=4))
        self.f.source.sessions[self.child] = fixture.session(self.child, 'python-worker', cwd=self.target)
        self.request = {'from_assignment_id': 'author', 'root_map': [{'from': str(self.f.root), 'to': str(self.target)}],
                        'new_assignment': {'id': 'continued', 'kind': 'author', 'role': 'python-worker',
                            'agent_id': self.child, 'allowed_paths': [str(self.target / 'result.py')], 'depends_on': []}}

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.f.root), *args], capture_output=True, text=True, check=True)

    def record(self, kind, request, operation):
        return write_entry(self.f.run, kind, request, identifier=operation, source=self.f.source)

    def stop(self):
        self.f.source.sessions[fixture.ROOT_ID].append({'type': 'event_msg', 'payload': {
            'type': 'task_complete', 'turn_id': fixture.ROOT_ID + '-turn', 'last_agent_message': None}})

    def test_transfer_without_report_replay_and_old_writes(self):
        old = self.f.snapshot().read_bytes('assignments/author.json')
        self.stop()
        receipt = self.record('transfer', self.request, 'move')
        self.assertFalse(self.f.snapshot().exists('reports/author.json'))
        self.assertEqual(old, self.f.snapshot().read_bytes('assignments/author.json'))
        self.assertEqual(self.record('transfer', self.request, 'move'), receipt)
        for kind in ['report', 'check', 'generation']:
            with self.subTest(kind=kind), self.assertRaisesRegex(JournalError, 'transferred'):
                self.record(kind, {'assignment_id': 'author'}, 'late-' + kind)
        with self.assertRaisesRegex(JournalError, 'payload conflict'):
            self.record('transfer', {**self.request, 'reason': 'Changed'}, 'move')

    def test_active_author_rejected_without_mutation(self):
        before = self.f.snapshot()
        with self.assertRaises(JournalError):
            self.record('transfer', self.request, 'move')
        self.assertEqual(before.documents, self.f.snapshot().documents)
        self.assertEqual(before.revision, self.f.snapshot().revision)

    def test_unrelated_repository_and_lost_path_rejected(self):
        self.stop()
        unrelated = Path(self.temporary.name).resolve() / 'unrelated'
        subprocess.run(['git', 'init', '-q', str(unrelated)], check=True)
        self.record('scope', {'roots': [{'path': str(unrelated)}]}, 'unrelated-scope')
        for change in [
            {'root_map': [{'from': str(self.f.root), 'to': str(unrelated)}]},
            {'new_assignment': {**self.request['new_assignment'], 'allowed_paths': [str(self.target / 'other.py')]}},
        ]:
            before = self.f.snapshot()
            with self.assertRaises(JournalError):
                self.record('transfer', {**self.request, **change}, 'bad-transfer')
            self.assertEqual(before.documents, self.f.snapshot().documents)

    def test_same_agent_requires_new_turn_after_stop(self):
        request = {**self.request, 'new_assignment': {**self.request['new_assignment'], 'agent_id': '/root'}}
        with self.assertRaises(JournalError):
            self.record('transfer', request, 'same-active')
        self.stop()
        self.f.source.sessions[fixture.ROOT_ID].append({'type': 'event_msg', 'payload': {
            'type': 'task_started', 'turn_id': 'continued-turn'}})
        self.record('transfer', request, 'same-stopped')
        self.assertEqual(self.f.snapshot().json('assignments/continued.json')['start_turn_id'], 'continued-turn')

    def test_verification_only_requires_independent_attestation(self):
        self.f.save('author')
        self.stop()
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Only scratch verification occurred',
                  'verification_only': ['author'], 'evidence': [self.f.ref(fixture.REVIEWER_ID)]}
        self.f.source.sessions[fixture.REVIEWER_ID] = fixture.session(fixture.REVIEWER_ID, 'reviewer', report, cwd=self.f.root)
        request = {'from_assignment_id': 'author', 'purpose': 'verification_only', 'reason': 'Scratch checks only',
                   'evidence': [self.f.ref(fixture.REVIEWER_ID)], 'reviewer_assignment_id': 'retirement-review'}
        with self.assertRaises(JournalError):
            self.record('transfer', request, 'no-review')
        self.record('assignment', {'id': 'retirement-review', 'kind': 'reviewer', 'role': 'reviewer',
                    'agent_id': fixture.REVIEWER_ID, 'allowed_paths': [], 'depends_on': []}, 'retirement-review')
        self.record('report', {'assignment_id': 'retirement-review', 'completion_turn_id': fixture.REVIEWER_ID + '-turn'}, 'retirement-report')
        self.record('transfer', request, 'retired')
        self.assertTrue(self.f.snapshot().exists('reports/author.json'))
        self.assertEqual(read_record(self.f.snapshot(), 'result')['authored_paths'], [])

    def test_symlink_escape_and_revision_conflict(self):
        self.stop()
        (self.target / 'result.py').unlink()
        (self.target / 'result.py').symlink_to(self.f.product)
        with self.assertRaisesRegex(JournalError, 'escapes'):
            self.record('transfer', self.request, 'symlink')
        (self.target / 'result.py').unlink()
        (self.target / 'result.py').write_text('answer = 2\n')
        def concurrent(*args, **kwargs):
            transact(self.f.run, 'concurrent', {}, lambda current: ({'artifacts/concurrent': b'x'}, {}))
            return transact(*args, **kwargs)
        with patch('journal_structure.transact', side_effect=concurrent), self.assertRaisesRegex(JournalError, 'revision conflict'):
            self.record('transfer', self.request, 'conflicted')
        self.assertFalse(self.f.snapshot().exists('assignments/continued.json'))
        self.assertFalse(self.f.snapshot().exists('transfers/conflicted.json'))

    def test_result_maps_predecessor_outputs_without_rewriting_history(self):
        self.f.save('author')
        original = self.f.snapshot().read_bytes('reports/author.json')
        self.record('assignment', {'id': 'author-next', 'kind': 'author', 'role': 'python-worker', 'agent_id': '/root',
            'allowed_paths': [str(self.f.product)], 'depends_on': [], 'previous_assignment_id': 'author'}, 'author-next')
        self.stop()
        self.record('transfer', {**self.request, 'from_assignment_id': 'author-next'}, 'moved-result')
        self.assertEqual(read_record(self.f.snapshot(), 'result')['authored_paths'], [str(self.target / 'result.py')])
        self.assertEqual(self.f.snapshot().read_bytes('reports/author.json'), original)

    def test_incomplete_transfer_can_finish_with_new_author_review_and_qa(self):
        before = self.f.snapshot().read_bytes('assignments/author.json')
        self.stop()
        self.record('transfer', self.request, 'transfer-final')
        author_report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Inspected transferred product',
                         'authored_paths': [], 'evidence': [self.f.ref(self.child)]}
        self.f.source.sessions[self.child] = fixture.session(self.child, 'python-worker', author_report, cwd=self.target)
        self.record('report', {'assignment_id': 'continued', 'completion_turn_id': self.child + '-turn'}, 'continued-report')
        for key, kind, role, agent, dependencies in [
            ('new-review', 'reviewer', 'reviewer', fixture.REVIEWER_ID, ['continued']),
            ('new-qa', 'qa', 'qa-verifier', fixture.QA_ID, ['new-review']),
        ]:
            self.f.source.sessions[agent] = fixture.session(agent, role, cwd=self.target)
            assignment = {'id': key, 'kind': kind, 'role': role, 'agent_id': agent,
                          'allowed_paths': [], 'depends_on': dependencies}
            self.record('assignment', assignment, key + '-assignment')
            report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Verified transferred product',
                      'evidence': [self.f.ref(agent)]}
            if kind == 'qa':
                report.update(reviewer={'assignment_id': 'new-review'}, criteria={
                    'ac-1': {'status': 'passed', 'evidence': [self.f.ref(agent)]}})
            self.f.source.sessions[agent] = fixture.session(agent, role, report, cwd=self.target)
            self.record('report', {'assignment_id': key, 'completion_turn_id': agent + '-turn'}, key + '-report')
        finalize(self.f.run, identifier='transfer-finished', report_id='new-qa', source=self.f.source)
        self.assertEqual(self.f.snapshot().json('final.json')['outcome'], 'passed')
        self.assertEqual(self.f.snapshot().read_bytes('assignments/author.json'), before)
        self.assertFalse(self.f.snapshot().exists('reports/author.json'))


if __name__ == '__main__':
    unittest.main()
