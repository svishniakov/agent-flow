#!/usr/bin/env python3
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO, StringIO, TextIOWrapper
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import journal_structure as writer
from journal_io import JournalError, digest
import verification_evidence as evidence


def load(name, filename):
    spec = spec_from_file_location(name, Path(__file__).with_name(filename))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load('journal_test_evidence', 'test-verification-evidence.py')
journal = load('journal_test_cli', 'journal.py')


class JournalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='af-writer-')
        self.addCleanup(temporary.cleanup)
        self.f = base.Fixture(temporary.name)

    def record(self, kind, request, identifier):
        return writer.write_entry(self.f.run, kind, request, identifier=identifier, source=self.f.source)

    def report_request(self, key, summary='Observed current result'):
        assignment = self.f.known[key]
        thread = assignment['agent_id']
        report = {'status': 'completed', 'summary': summary, 'evidence': [self.f.ref(thread)]}
        if key == 'author':
            report['authored_paths'] = [str(self.f.product)]
            return {'assignment_id': key, 'turn_id': thread + '-turn', 'report': report}
        report['fingerprint'] = evidence.result_fingerprint(self.f.snapshot())
        if key == 'qa':
            report['criteria'] = {'ac-1': {'status': 'passed', 'evidence': [self.f.ref(thread)]}}
        else:
            report['qa'] = {'assignment_id': 'qa', 'sha256': digest(self.f.snapshot().read_bytes('reports/qa.json'))}
        self.f.source.sessions[thread] = base.session(thread, assignment['role'], report, cwd=self.f.root)
        return {'assignment_id': key, 'completion_turn_id': thread + '-turn'}

    def assert_unchanged(self, before):
        after = self.f.snapshot()
        self.assertEqual((before.revision, before.documents, before.operations),
                         (after.revision, after.documents, after.operations))

    def test_assignment_uses_native_identity_and_refuses_conflicts(self):
        request = dict(self.f.known['author'])
        receipt = self.record('assignment', request, 'assign-author')
        self.assertEqual(receipt['result']['path'], 'assignments/author.json')
        self.assertEqual(self.f.snapshot().json('assignments/author.json'), request)
        before = self.f.snapshot()
        with self.assertRaises(JournalError):
            self.record('assignment', {**request, 'agent_id': base.QA_ID}, 'different-author')
        self.assert_unchanged(before)
        with self.assertRaisesRegex(JournalError, 'independent child'):
            self.record('assignment', {**self.f.known['qa'], 'agent_id': base.ROOT_ID}, 'root-qa')
        self.assert_unchanged(before)

    def test_root_report_binds_native_edits_and_can_be_replaced(self):
        request = self.report_request('author')
        receipt = self.record('report', request, 'author-report')
        snapshot = self.f.snapshot()
        self.assertEqual(receipt['result']['sha256'], digest(snapshot.read_bytes('reports/author.json')))
        report = snapshot.json('reports/author.json')
        self.assertTrue(report['source']['root_author'])
        self.assertEqual(report['evidence'], request['report']['evidence'])
        self.assertNotIn('task_complete', json.dumps(self.f.source.sessions[base.ROOT_ID]))
        request['report']['summary'] = 'Clarified native implementation'
        self.record('report', request, 'author-correction')
        self.assertEqual(self.f.snapshot().json('reports/author.json')['summary'], 'Clarified native implementation')
        self.assertEqual(self.f.snapshot().operation_receipt('author-report'), receipt)

    def test_child_report_uses_completed_native_answer(self):
        child = '00000000-0000-4000-8000-000000000004'
        assignment = {**self.f.known['author'], 'id': 'child', 'agent_id': child, 'depends_on': ['author']}
        self.record('report', self.report_request('author'), 'author-before-child')
        native = {'status': 'completed', 'summary': 'Native child result', 'authored_paths': [str(self.f.product)],
                  'evidence': [{'thread_id': child, 'turn_id': child + '-turn', 'call_id': 'tool'}]}
        self.f.source.sessions[child] = base.session(child, 'python-worker', native, cwd=self.f.root,
                                                   patch='*** Update File: result.py\n')
        self.record('assignment', assignment, 'assign-child')
        self.record('report', {'assignment_id': 'child', 'completion_turn_id': child + '-turn',
                              'report': {'summary': 'Forged caller report'}}, 'child-report')
        report = self.f.snapshot().json('reports/child.json')
        self.assertEqual(report['summary'], native['summary'])
        self.assertFalse(report['source']['root_author'])

    def test_incomplete_child_refuses_before_publication(self):
        self.record('report', self.report_request('author'), 'author-report')
        request = self.report_request('qa')
        self.f.source.sessions[base.QA_ID] = self.f.source.sessions[base.QA_ID][:-2]
        before = self.f.snapshot()
        with self.assertRaisesRegex(JournalError, 'completed turn'):
            self.record('report', request, 'incomplete-qa')
        self.assert_unchanged(before)
        self.assertFalse(self.f.snapshot().exists('reports/qa.json'))

    def test_qa_and_reviewer_publish_actual_native_reports(self):
        for key in ('author', 'qa', 'review'):
            receipt = self.record('report', self.report_request(key), key + '-report')
            snapshot = self.f.snapshot()
            self.assertEqual(receipt['result']['path'], 'reports/' + key + '.json')
            self.assertEqual(receipt['result']['sha256'], digest(snapshot.read_bytes(receipt['result']['path'])))
        self.assertEqual(evidence.validate_acceptance(self.f.snapshot(), self.f.source), [])

    def test_unverified_qa_publishes_only_with_a_reason(self):
        self.record('report', self.report_request('author'), 'author-report')
        report = {'status': 'failed', 'summary': 'Inspected available sources', 'reason': 'Runtime check is forbidden',
                  'fingerprint': evidence.result_fingerprint(self.f.snapshot()), 'evidence': [self.f.ref(base.QA_ID)],
                  'criteria': {'ac-1': {'status': 'unverified'}}}
        request = {'assignment_id': 'qa', 'completion_turn_id': base.QA_ID + '-turn'}
        self.f.source.sessions[base.QA_ID] = base.session(base.QA_ID, 'qa-verifier', report, cwd=self.f.root)
        before = self.f.snapshot()
        with self.assertRaisesRegex(JournalError, 'criterion ac-1: .*reason required'):
            self.record('report', request, 'qa-missing-reason')
        self.assert_unchanged(before)
        report['criteria']['ac-1']['reason'] = 'Runtime check is forbidden'
        self.f.source.sessions[base.QA_ID] = base.session(base.QA_ID, 'qa-verifier', report, cwd=self.f.root)
        receipt = self.record('report', request, 'qa-unverified')
        accepted = self.f.snapshot().json('reports/qa.json')
        self.assertEqual(accepted['criteria'], report['criteria'])
        self.assertEqual(accepted['status'], 'failed')
        self.assertEqual(receipt['result']['path'], 'reports/qa.json')
        self.assertEqual(receipt['result']['sha256'], digest(self.f.snapshot().read_bytes('reports/qa.json')))

    def test_locked_reviewer_refuses_native_qa_tamper(self):
        self.record('report', self.report_request('author'), 'author-report')
        self.record('report', self.report_request('qa'), 'qa-report')
        request = self.report_request('review')
        before = self.f.snapshot()
        real_transact = writer.transact
        def racing(*args, **kwargs):
            events = self.f.source.sessions[base.QA_ID]
            report = json.loads(events[-1]['payload']['last_agent_message'])
            report['summary'] = 'Changed native QA'
            text = json.dumps(report)
            events[-1]['payload']['last_agent_message'] = text
            events[-2]['payload']['content'][0]['text'] = text
            return real_transact(*args, **kwargs)
        with patch.object(writer, 'transact', new=racing), self.assertRaises(JournalError):
            self.record('report', request, 'stale-review')
        self.assert_unchanged(before)
        self.assertFalse(self.f.snapshot().exists('reports/review.json'))

    def test_locked_report_refuses_product_change(self):
        self.record('report', self.report_request('author'), 'author-report')
        request = self.report_request('qa')
        before = self.f.snapshot()
        real_transact = writer.transact
        def racing(*args, **kwargs):
            self.f.product.write_text('answer = 3\n')
            return real_transact(*args, **kwargs)
        with patch.object(writer, 'transact', new=racing), self.assertRaisesRegex(JournalError, 'stale'):
            self.record('report', request, 'stale-qa')
        self.assert_unchanged(before)

    def check_request(self, name='unit', exit_code=0):
        return {'id': name, 'assignment_id': 'author', 'cwd': str(self.f.root),
                'command': [sys.executable, '-B', '-c', 'import sys; print("actual check output"); sys.exit(' + str(exit_code) + ')']}

    def test_check_replay_does_not_launch_second_subprocess(self):
        request = self.check_request()
        first = self.record('check', request, 'run-unit')
        before = self.f.snapshot()
        with patch.object(writer, 'execute_check', side_effect=AssertionError('replay executed command')):
            self.assertEqual(self.record('check', request, 'run-unit'), first)
            with self.assertRaisesRegex(JournalError, 'payload conflict'):
                self.record('check', {**request, 'timeout': 10}, 'run-unit')
        self.assert_unchanged(before)
        check = before.json('checks/unit.json')
        self.assertEqual(check['exit_code'], 0)
        self.assertEqual(before.read_bytes(check['output']['path']), b'actual check output\n')
        self.assertEqual(check['output']['sha256'], digest(b'actual check output\n'))
        self.assertEqual(check['operation_id'], 'run-unit')

    def test_failed_check_has_receipt_missing_executable_has_none(self):
        result = self.record('check', self.check_request('failing', 7), 'run-failing')
        self.assertEqual(result['result']['exit_code'], 7)
        self.assertEqual(self.f.snapshot().json('checks/failing.json')['exit_code'], 7)
        before = self.f.snapshot()
        request = {**self.check_request('missing'), 'command': [str(self.f.root / 'no-such-program')]}
        with self.assertRaises(OSError):
            self.record('check', request, 'run-missing')
        self.assert_unchanged(before)
        self.assertFalse(self.f.snapshot().exists('checks/missing.json'))

    def test_check_that_changes_product_has_no_success_receipt(self):
        request = self.check_request('mutating')
        request['command'] = [sys.executable, '-B', '-c', 'from pathlib import Path; Path("result.py").write_text("changed")']
        before = self.f.snapshot()
        with self.assertRaisesRegex(JournalError, 'changed product'):
            self.record('check', request, 'run-mutating')
        self.assert_unchanged(before)

    def test_check_cwd_symlink_cannot_escape_product_scope(self):
        outside = tempfile.TemporaryDirectory(prefix='af-check-outside-')
        self.addCleanup(outside.cleanup)
        link = self.f.root / 'linked-cwd'
        link.symlink_to(Path(outside.name).resolve(), target_is_directory=True)
        before = self.f.snapshot()
        request = {**self.check_request(), 'cwd': str(link)}
        with patch.object(writer.subprocess, 'run', side_effect=AssertionError('outside command executed')):
            with self.assertRaisesRegex(JournalError, 'outside product scope'):
                self.record('check', request, 'escaped-cwd')
        self.assert_unchanged(before)

    def test_artifact_immutable_bytes_and_evidence_reference(self):
        source = self.f.root.parent / (self.f.root.name + '-output')
        self.addCleanup(source.unlink, missing_ok=True)
        source.write_bytes(b'raw\x00evidence\n')
        receipt = writer.publish_artifact(self.f.run, 'artifacts/output.bin', source, identifier='publish-output')
        ref = {'path': receipt['result']['path'], 'sha256': receipt['result']['sha256']}
        self.assertEqual(self.f.snapshot().read_bytes(ref['path']), source.read_bytes())
        evidence.verify_evidence(self.f.snapshot(), [ref], self.f.source, root_id=base.ROOT_ID)
        before = self.f.snapshot()
        source.write_bytes(b'different')
        with self.assertRaisesRegex(JournalError, 'different bytes'):
            writer.publish_artifact(self.f.run, ref['path'], source, identifier='overwrite-output')
        for forged in ({**ref, 'sha256': '0' * 64}, {'path': '../outside', 'sha256': ref['sha256']}):
            with self.assertRaises(JournalError):
                evidence.verify_evidence(self.f.snapshot(), [forged], self.f.source, root_id=base.ROOT_ID)
        self.assert_unchanged(before)

    def test_cli_stdin_record_contract_and_read(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(journal.main(['contract']), 0)
        self.assertIn('report', json.loads(output.getvalue())['records'])
        stdin = TextIOWrapper(BytesIO(json.dumps(self.report_request('author')).encode()))
        output = StringIO()
        with patch.object(sys, 'stdin', stdin), patch.object(evidence, 'CodexSessionSource', return_value=self.f.source), redirect_stdout(output):
            self.assertEqual(journal.main(['--run-dir', str(self.f.run), 'record', '--kind', 'report',
                '--request-file', '-', '--operation-id', 'stdin-author']), 0)
        self.assertEqual(json.loads(output.getvalue())['result']['path'], 'reports/author.json')
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(journal.main(['--run-dir', str(self.f.run), 'read', 'summary']), 0)
        self.assertEqual(json.loads(output.getvalue())['reports'][0]['path'], 'reports/author.json')


if __name__ == '__main__':
    unittest.main()
