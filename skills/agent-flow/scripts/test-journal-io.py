#!/usr/bin/env python3
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO, StringIO, TextIOWrapper
import json
import sqlite3
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import journal_structure as writer
from journal_io import JournalError
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
        if kind == 'assignment':
            request = {key: value for key, value in request.items() if key != 'start_turn_id'}
        return writer.write_entry(self.f.run, kind, request, identifier=identifier, source=self.f.source)

    def report_request(self, key, summary='Observed current result'):
        assignment = self.f.known[key]
        thread = assignment['agent_id']
        report = {'status': 'completed', 'outcome': 'passed', 'summary': summary, 'evidence': [self.f.ref(thread)]}
        if key == 'author':
            report['authored_paths'] = [str(self.f.product)]
            return {'assignment_id': key, 'turn_id': thread + '-turn', 'report': report}
        if key == 'qa':
            report['criteria'] = {'ac-1': {'status': 'passed', 'evidence': [self.f.ref(thread)]}}
            report['reviewer'] = {'assignment_id': 'review'}
        self.f.source.sessions[thread] = base.session(thread, assignment['role'], report, cwd=self.f.root)
        return {'assignment_id': key, 'completion_turn_id': thread + '-turn'}

    def assert_unchanged(self, before):
        after = self.f.snapshot()
        self.assertEqual((before.revision, before.documents, before.operations),
                         (after.revision, after.documents, after.operations))

    def test_assignment_uses_native_identity_and_refuses_conflicts(self):
        request = dict(self.f.known['author'])
        with self.assertRaisesRegex(JournalError, 'start_turn_id'):
            writer.write_entry(self.f.run, 'assignment', request, identifier='forged-start', source=self.f.source)
        with self.assertRaisesRegex(JournalError, 'already exists'):
            self.record('assignment', request, 'assign-author')
        self.assertEqual(self.f.snapshot().json('assignments/author.json'), request)
        before = self.f.snapshot()
        with self.assertRaises(JournalError):
            self.record('assignment', {**request, 'agent_id': base.QA_ID}, 'different-author')
        self.assert_unchanged(before)
        with self.assertRaisesRegex(JournalError, 'independent child'):
            self.record('assignment', {**self.f.known['qa'], 'agent_id': base.ROOT_ID}, 'root-qa')
        self.assert_unchanged(before)

    def test_author_assignment_retains_initial_turn_after_continuation(self):
        original = self.f.snapshot().json('assignments/author.json')
        self.f.source.sessions[base.ROOT_ID].extend([
            {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': base.ROOT_ID + '-turn', 'last_agent_message': '{}'}},
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'author-next'}},
        ])
        with self.assertRaisesRegex(JournalError, 'already exists'):
            self.record('assignment', self.f.known['author'], 'reassign-author')
        self.assertEqual(self.f.snapshot().json('assignments/author.json'), original)

    def test_disjoint_successor_cannot_hide_predecessor_coverage(self):
        self.record('report', self.report_request('author'), 'author-report')
        before = self.f.snapshot()
        assignment = {**self.f.known['author'], 'id': 'unrelated', 'depends_on': ['author'],
                      'previous_assignment_id': 'author', 'allowed_paths': [str(self.f.root / 'unrelated.py')]}
        with self.assertRaisesRegex(JournalError, 'cover predecessor allowed paths'):
            self.record('assignment', assignment, 'disjoint-successor')
        self.assert_unchanged(before)
        self.assertFalse(self.f.snapshot().exists('assignments/unrelated.json'))

    def test_three_author_iterations_retain_history_and_accept_latest_coverage(self):
        import journal_lifecycle
        self.record('report', self.report_request('author'), 'author-report')
        original = dict(self.f.snapshot().documents)
        predecessor = 'author'
        previous_turn = base.ROOT_ID + '-turn'
        for index in (2, 3):
            name = 'author-' + str(index)
            turn = 'correction-' + str(index)
            self.f.source.sessions[base.ROOT_ID].append({'type': 'event_msg', 'payload': {
                'type': 'task_complete', 'turn_id': previous_turn, 'last_agent_message': '{}'}})
            self.f.source.sessions[base.ROOT_ID].extend(base.session(base.ROOT_ID, 'python-worker', cwd=self.f.root,
                turn=turn, patch='*** Begin Patch\n*** Update File: result.py\n@@\n-answer = 2\n+answer = 3\n*** End Patch')[1:])
            assignment = {**self.f.known['author'], 'id': name, 'previous_assignment_id': predecessor,
                          'depends_on': [predecessor]}
            self.record('assignment', assignment, 'assign-' + name)
            self.f.known[name] = self.f.snapshot().json('assignments/' + name + '.json')
            self.f.save(name, turn=turn)
            predecessor, previous_turn = name, turn
        for name, kind, role, agent, dependencies in (
            ('review-third', 'reviewer', 'reviewer', base.REVIEWER_ID, ['author-3']),
            ('qa-third', 'qa', 'qa-verifier', base.QA_ID, ['review-third']),
        ):
            self.f.source.sessions[agent] = base.session(agent, role, cwd=self.f.root)
            assignment = {'id': name, 'kind': kind, 'role': role, 'agent_id': agent,
                          'allowed_paths': [], 'depends_on': dependencies}
            self.record('assignment', assignment, 'assign-' + name)
            self.f.known[name] = assignment
            self.f.save(name)
        journal_lifecycle.finalize(self.f.run, identifier='third-final', report_id='qa-third', source=self.f.source)
        self.assertEqual(self.f.snapshot().json('final.json')['outcome'], 'passed')
        for path, content in original.items():
            self.assertEqual(self.f.snapshot().read_bytes(path), content)

    def test_root_report_binds_native_edits_and_is_immutable(self):
        request = self.report_request('author')
        receipt = self.record('report', request, 'author-report')
        snapshot = self.f.snapshot()
        self.assertEqual(receipt['result'], {'path': 'reports/author.json'})
        report = snapshot.json('reports/author.json')
        self.assertTrue(report['source']['root_author'])
        self.assertEqual(report['evidence'], request['report']['evidence'])
        self.assertNotIn('task_complete', json.dumps(self.f.source.sessions[base.ROOT_ID]))
        request['report']['summary'] = 'Clarified native implementation'
        with self.assertRaisesRegex(JournalError, 'immutable|already exists'):
            self.record('report', request, 'author-correction')
        self.assertEqual(self.f.snapshot().json('reports/author.json'), report)
        self.assertEqual(self.f.snapshot().operation_receipt('author-report'), receipt)

    def test_child_report_uses_completed_native_answer(self):
        child = '00000000-0000-4000-8000-000000000004'
        assignment = {**self.f.known['author'], 'id': 'child', 'agent_id': child, 'depends_on': ['author']}
        self.record('report', self.report_request('author'), 'author-before-child')
        native = {'status': 'completed', 'outcome': 'passed', 'summary': 'Native child result', 'authored_paths': [str(self.f.product)],
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
        self.record('report', self.report_request('review'), 'review-report')
        request = self.report_request('qa')
        self.f.source.sessions[base.QA_ID] = self.f.source.sessions[base.QA_ID][:-2]
        before = self.f.snapshot()
        with self.assertRaisesRegex(JournalError, 'completed turn'):
            self.record('report', request, 'incomplete-qa')
        self.assertFalse(self.f.snapshot().exists('reports/qa.json'))

    def test_qa_and_reviewer_publish_actual_native_reports(self):
        for key in ('author', 'review', 'qa'):
            receipt = self.record('report', self.report_request(key), key + '-report')
            snapshot = self.f.snapshot()
            self.assertEqual(receipt['result']['path'], 'reports/' + key + '.json')
            self.assertTrue(snapshot.exists(receipt['result']['path']))
        self.assertEqual(evidence.validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa'), [])

    def test_qa_assignment_and_report_require_current_completed_review(self):
        self.record('report', self.report_request('author'), 'author-report')
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Inspected result', 'evidence': [self.f.ref(base.QA_ID)],
                  'reviewer': {'assignment_id': 'review'},
                  'criteria': {'ac-1': {'status': 'passed', 'evidence': [self.f.ref(base.QA_ID)]}}}
        request = {'assignment_id': 'qa', 'completion_turn_id': base.QA_ID + '-turn'}
        for state in ('missing', 'failed'):
            with self.subTest(review=state):
                if state == 'failed':
                    review = {'status': 'completed', 'outcome': 'failed', 'summary': 'Review found a defect', 'reason': 'Result is incorrect',
                              'evidence': [self.f.ref(base.REVIEWER_ID)]}
                    self.f.save('review', review)
                self.f.source.sessions[base.QA_ID] = base.session(base.QA_ID, 'qa-verifier', report, cwd=self.f.root)
                before = self.f.snapshot()
                with self.assertRaises(JournalError):
                    self.record('assignment', self.f.known['qa'], 'assign-qa-' + state)
                with self.assertRaises(JournalError):
                    self.record('report', request, 'qa-' + state)
                self.assert_unchanged(before)

    def test_unverified_qa_publishes_only_with_a_reason(self):
        self.record('report', self.report_request('author'), 'author-report')
        self.record('report', self.report_request('review'), 'review-report')
        report = {'status': 'completed', 'outcome': 'failed', 'summary': 'Inspected available sources', 'reason': 'Runtime check is forbidden',
                  'evidence': [self.f.ref(base.QA_ID)],
                  'reviewer': {'assignment_id': 'review'},
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
        self.assertEqual(accepted['outcome'], 'failed')
        self.assertEqual(receipt['result']['path'], 'reports/qa.json')
        self.assertEqual(receipt['result'], {'path': 'reports/qa.json'})

    def test_journal_revision_race_refuses_qa_publication(self):
        self.record('report', self.report_request('author'), 'author-report')
        self.record('report', self.report_request('review'), 'review-report')
        request = self.report_request('qa')
        before = self.f.snapshot()
        real_transact = writer.transact
        def racing(*args, **kwargs):
            real_transact(self.f.run, 'competing-write', {}, lambda current: ({'artifacts/competing': b'other'}, {}))
            return real_transact(*args, **kwargs)
        with patch.object(writer, 'transact', new=racing), self.assertRaises(JournalError):
            self.record('report', request, 'stale-qa')
        self.assertFalse(self.f.snapshot().exists('reports/qa.json'))

    def test_locked_report_accepts_other_chat_changes(self):
        self.record('report', self.report_request('author'), 'author-report')
        self.record('report', self.report_request('review'), 'review-report')
        request = self.report_request('qa')
        real_transact = writer.transact
        def racing(*args, **kwargs):
            (self.f.root / 'other-chat.md').write_text('Another task changed\n')
            return real_transact(*args, **kwargs)
        with patch.object(writer, 'transact', new=racing):
            self.record('report', request, 'qa-with-other-chat-change')
        self.assertEqual(evidence.validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa'), [])

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
        self.assertEqual(check['output'], {'path': 'artifacts/checks/unit.log'})
        self.assertEqual(check['operation_id'], 'run-unit')
        evidence.verify_evidence(self.f.snapshot(), [{'path': first['result']['path'], 'sha256': 'obsolete'}],
                                 self.f.source, root_id=base.ROOT_ID, success_required=True)

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

    def test_check_accepts_other_chat_changes_during_execution(self):
        request = self.check_request('mutating')
        actual_run = writer.subprocess.run
        def execute(*args, **kwargs):
            result = actual_run(*args, **kwargs)
            if args[0] == request['command']:
                (self.f.root / 'other-chat.md').write_text('Another task changed\n')
            return result
        with patch.object(writer.subprocess, 'run', side_effect=execute):
            receipt = self.record('check', request, 'run-mutating')
        self.assertEqual(receipt['result']['exit_code'], 0)
        evidence.verify_evidence(self.f.snapshot(), [{'path': receipt['result']['path']}], self.f.source,
                                 root_id=base.ROOT_ID, success_required=True)

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

    def generation_request(self, text='generated = 3\n'):
        return {'assignment_id': 'author', 'cwd': str(self.f.root), 'outputs': [str(self.f.product)],
                'command': [sys.executable, '-B', '-c',
                            'from pathlib import Path; Path("result.py").write_text(' + repr(text) + '); print("generated")']}

    def test_generation_stores_request_and_output_and_replay_never_executes(self):
        request = self.generation_request()
        first = self.record('generation', request, 'generate-result')
        self.assertTrue(first['executed'])
        receipt = first['receipt']
        body = self.f.snapshot().json(receipt['result']['path'])
        self.assertEqual(body['exit_code'], 0)
        self.assertNotIn('changes', body)
        self.assertEqual(self.f.snapshot().read_bytes(body['output']['path']), b'generated\n')
        self.assertEqual(body['request'], request)
        self.assertNotIn('executed', json.dumps(receipt))
        before = self.f.snapshot()
        with patch.object(writer, 'execute_generation', side_effect=AssertionError('replay launched generation')):
            self.assertEqual(self.record('generation', request, 'generate-result'), {'executed': False, 'receipt': receipt})
            with self.assertRaisesRegex(JournalError, 'payload conflict'):
                self.record('generation', {**request, 'timeout': 10}, 'generate-result')
        self.assert_unchanged(before)

    def test_generation_busy_precedes_command_and_locked_replay_is_not_execution(self):
        request = self.generation_request()
        before = self.f.snapshot()
        real_transact = writer.transact
        def quick_transact(*args, **kwargs):
            return real_transact(*args, **kwargs, timeout=0.01)
        connection = sqlite3.connect(self.f.run / '.journal/state.sqlite3')
        try:
            connection.execute('BEGIN IMMEDIATE')
            with patch.object(writer, 'transact', new=quick_transact), patch.object(
                    writer, 'execute_generation', side_effect=AssertionError('busy launched generation')):
                with self.assertRaisesRegex(JournalError, 'transaction failed'):
                    self.record('generation', request, 'busy-generation')
        finally:
            connection.rollback()
            connection.close()
        self.assert_unchanged(before)
        def competing(*args, **kwargs):
            with patch.object(writer, 'transact', new=real_transact):
                self.first_generation = self.record('generation', request, 'racing-generation')
            return real_transact(*args, **kwargs)
        with patch.object(writer, 'transact', new=competing):
            replay = self.record('generation', request, 'racing-generation')
        self.assertTrue(self.first_generation['executed'])
        self.assertFalse(replay['executed'])
        self.assertEqual(replay['receipt'], self.first_generation['receipt'])

    def test_generation_refuses_outside_assignment_before_execution(self):
        request = {**self.generation_request(), 'outputs': [str(self.f.plan)]}
        before = self.f.snapshot()
        with patch.object(writer.subprocess, 'run', side_effect=AssertionError('unowned generation executed')):
            with self.assertRaisesRegex(JournalError, 'outside assignment'):
                self.record('generation', request, 'unowned-generation')
        self.assert_unchanged(before)

    def test_generation_accepts_other_chat_changes_during_execution(self):
        request = self.generation_request()
        actual_run = writer.subprocess.run
        def execute(*args, **kwargs):
            result = actual_run(*args, **kwargs)
            if args[0] == request['command']:
                (self.f.root / 'other-chat.md').write_text('Another task changed\n')
            return result
        with patch.object(writer.subprocess, 'run', side_effect=execute):
            result = self.record('generation', request, 'generate-with-neighbor')
        body = self.f.snapshot().json(result['receipt']['result']['path'])
        self.assertTrue(result['executed'])
        self.assertEqual(body['exit_code'], 0)
        self.assertEqual(body['request']['outputs'], [str(self.f.product)])

    def test_generation_records_noop_and_failed_execution(self):
        request = self.generation_request('answer = 2\n')
        result = self.record('generation', request, 'noop-generation')
        self.assertEqual(result['receipt']['result']['exit_code'], 0)
        request['command'] = [sys.executable, '-B', '-c', 'print("failed generator"); raise SystemExit(7)']
        result = self.record('generation', request, 'failed-generation')
        self.assertEqual(result['receipt']['result']['exit_code'], 7)
        body = self.f.snapshot().json(result['receipt']['result']['path'])
        self.assertEqual(self.f.snapshot().read_bytes(body['output']['path']), b'failed generator\n')

    def test_generation_logs_cannot_be_published_as_user_artifacts(self):
        with self.assertRaisesRegex(JournalError, 'writer-owned'):
            writer.publish_artifact(self.f.run, 'artifacts/generations/forged.log', self.f.product, identifier='forge-log')

    def native_generation(self, request, identifier, *, thread=base.ROOT_ID):
        script = writer.generation_invocation(self.f.run, identifier, request)
        process = subprocess.run(['/bin/zsh', '-lc', script], cwd=self.f.root, capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        envelope = json.loads(process.stdout)
        item = {'type': 'CommandExecution', 'id': 'exec-' + identifier, 'status': 'completed', 'exit_code': 0,
                'command': ['/bin/zsh', '-lc', script], 'cwd': self.f.root.as_uri(), 'aggregated_output': process.stdout}
        event = {'type': 'event_msg', 'payload': {'type': 'item_completed', 'thread_id': thread,
                                                'turn_id': thread + '-turn', 'item': item}}
        events = self.f.source.sessions[thread]
        index = -2 if events[-1]['payload'].get('type') == 'task_complete' else len(events)
        events.insert(index, event)
        result = envelope['receipt']['result']
        ref = {'path': result['path'],
               'native': {'thread_id': thread, 'turn_id': thread + '-turn', 'item_id': item['id']}}
        return ref, event

    def test_generated_root_author_to_independent_acceptance_and_final(self):
        import journal_lifecycle
        ref, event = self.native_generation(self.generation_request(), 'root-generation')
        observed = evidence.native_references(self.f.snapshot(), 'author', self.f.source)
        self.assertEqual(observed['observations'][-1]['ref'], ref)
        self.assertEqual(observed['observations'][-1]['authored_paths'], [str(self.f.product)])
        request = self.report_request('author')
        request['report']['evidence'] = [ref]
        self.record('report', request, 'generated-author')
        for key in ('review', 'qa'):
            self.record('report', self.report_request(key), 'generated-' + key)
        self.assertEqual(evidence.validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa'), [])
        native_home = tempfile.TemporaryDirectory(prefix='af-generation-native-')
        self.addCleanup(native_home.cleanup)
        home = Path(native_home.name)
        (home / 'sessions').mkdir()
        connection = sqlite3.connect(home / 'state_5.sqlite')
        try:
            connection.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, agent_path TEXT, source TEXT)')
            for thread, events in self.f.source.sessions.items():
                path = home / 'sessions' / (thread + '.jsonl')
                path.write_text(''.join(json.dumps(event) + '\n' for event in events))
                connection.execute('INSERT INTO threads VALUES (?, ?, NULL, ?)', (thread, str(path), '{}'))
            connection.commit()
        finally:
            connection.close()
        environment = patch.dict(base.os.environ, {'CODEX_HOME': str(home)})
        environment.start()
        self.addCleanup(environment.stop)
        native_source = evidence.CodexSessionSource()
        self.assertEqual(evidence.native_references(self.f.snapshot(), 'author', native_source)['observations'][-1]['ref'], ref)
        receipt = journal_lifecycle.finalize(self.f.run, identifier='generated-final', report_id='qa', source=native_source)
        self.assertEqual(receipt['result']['outcome'], 'passed')
        with self.assertRaisesRegex(JournalError, 'successful check or native inspection'):
            evidence.verify_evidence(self.f.snapshot(), [ref], self.f.source, root_id=base.ROOT_ID, success_required=True)

    def test_child_corrects_report_next_turn_without_repeating_edits_or_generation(self):
        import journal_lifecycle
        child = '00000000-0000-4000-8000-000000000004'
        generated = self.f.root / 'generated.py'
        counter = self.f.run / 'generation-invocations'
        self.record('report', self.report_request('author'), 'initial-author')
        self.f.source.sessions[child] = base.session(child, 'python-worker', cwd=self.f.root)
        assignment = {**self.f.known['author'], 'id': 'child', 'agent_id': child, 'depends_on': ['author'],
                      'allowed_paths': [str(self.f.product), str(generated)]}
        self.record('assignment', assignment, 'assign-child')
        self.assertEqual(self.f.snapshot().json('assignments/child.json')['start_turn_id'], child + '-turn')
        self.f.product.write_text('answer = 3\n')
        self.f.source.sessions[child].append(base.file_change(child, self.f.product))
        edit_ref = {'thread_id': child, 'turn_id': child + '-turn', 'item_id': 'exec-native-patch'}
        generation = {'assignment_id': 'child', 'cwd': str(self.f.root), 'outputs': [str(generated)],
                      'command': [sys.executable, '-B', '-c',
                                  'from pathlib import Path; counter = Path(' + repr(str(counter)) + '); '
                                  'counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else "1"); '
                                  'Path("generated.py").write_text("generated = 4\\n"); print("generated once")']}
        generation_ref, _ = self.native_generation(generation, 'child-once', thread=child)
        generation_receipt = self.f.snapshot().operation_receipt('child-once')
        report = {'status': 'completed', 'outcome': 'passed', 'summary': {'result': 'Implemented both files'},
                  'authored_paths': [str(self.f.product), str(generated)], 'evidence': [edit_ref, generation_ref]}
        self.f.source.sessions[child].extend(base.session(child, 'python-worker', report, cwd=self.f.root)[-2:])
        before = self.f.snapshot()
        with self.assertRaisesRegex(JournalError, 'report summary required'):
            self.record('report', {'assignment_id': 'child', 'completion_turn_id': child + '-turn'}, 'malformed-child')
        self.assert_unchanged(before)
        self.assertFalse(self.f.snapshot().exists('reports/child.json'))
        self.assertIsNone(self.f.snapshot().operation_receipt('malformed-child'))
        self.assertEqual(self.f.snapshot().operation_receipt('child-once'), generation_receipt)
        report['summary'] = 'Implemented both files'
        turn_b = 'child-report-correction'
        continuation = base.session(child, 'python-worker', report, cwd=self.f.root, turn=turn_b)
        self.f.source.sessions[child].extend(continuation[1:3] + continuation[-2:])
        observed = evidence.native_references(self.f.snapshot(), 'child', self.f.source)
        self.assertEqual(observed['turn_id'], turn_b)
        self.assertTrue(observed['completed'])
        self.assertIn(edit_ref, [item['ref'] for item in observed['observations']])
        self.assertIn(generation_ref, [item['ref'] for item in observed['observations']])
        with patch.object(writer, 'execute_generation', side_effect=AssertionError('report correction reran generator')):
            self.record('report', {'assignment_id': 'child', 'completion_turn_id': turn_b}, 'corrected-child')
        self.assertEqual(self.f.snapshot().json('reports/child.json')['evidence'], report['evidence'])
        self.assertEqual(counter.read_text(), '1')
        self.assertEqual(generated.read_text(), 'generated = 4\n')
        for key in ('review', 'qa'):
            self.record('report', self.report_request(key), 'corrected-child-' + key)

    def test_generation_requires_real_own_invocation_without_tracking_output_changes(self):
        ref, event = self.native_generation(self.generation_request(), 'bound-generation')
        item = event['payload']['item']
        original_output, original_command = item['aggregated_output'], item['command']
        request = self.report_request('author')
        request['report']['evidence'] = [ref]
        envelope = json.loads(original_output)
        envelope['executed'] = False
        item['aggregated_output'] = json.dumps(envelope)
        with self.assertRaisesRegex(JournalError, 'not a replay'):
            self.record('report', request, 'replayed-author')
        item['aggregated_output'] = original_output
        item['command'] = ['/bin/zsh', '-lc', 'printf ' + repr(original_output)]
        with self.assertRaisesRegex(JournalError, 'canonical standalone'):
            self.record('report', request, 'echoed-author')
        item['command'] = original_command
        self.f.product.write_text('different = 4\n')
        self.record('report', request, 'generated-author')
        self.assertEqual(self.f.snapshot().json('reports/author.json')['authored_paths'], [str(self.f.product)])

    def test_sequential_generations_preserve_declared_output_evidence(self):
        import journal_lifecycle
        self.record('report', self.report_request('author'), 'initial-author')
        second = self.f.root / 'second.py'
        assignment = {**self.f.known['author'], 'id': 'batch', 'depends_on': ['author'],
                      'allowed_paths': [str(self.f.product), str(second)]}
        self.record('assignment', assignment, 'assign-batch')
        request = {**self.generation_request(), 'assignment_id': 'batch',
                   'outputs': [str(self.f.product), str(second)]}
        request['command'][-1] += '; Path("second.py").write_text("value = 1\\n")'
        first, _ = self.native_generation(request, 'generate-batch')
        request = {**request, 'command': [sys.executable, '-c', 'from pathlib import Path; Path("second.py").write_text("value = 2\\n")'],
                   'outputs': [str(second)]}
        latest, _ = self.native_generation(request, 'correct-second')
        self.assertEqual(evidence.generation_evidence(self.f.snapshot(), first, self.f.source), [str(self.f.product), str(second)])
        self.assertEqual(evidence.generation_evidence(self.f.snapshot(), latest, self.f.source), [str(second)])
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Generated and corrected files',
                  'authored_paths': [str(self.f.product), str(second)], 'evidence': [first]}
        publication = {'assignment_id': 'batch', 'turn_id': base.ROOT_ID + '-turn', 'report': report}
        self.record('report', publication, 'batch-proof')
        for key in ('review', 'qa'):
            self.record('report', self.report_request(key), 'batch-' + key)

    def test_dependent_author_generation_remains_valid_after_file_changes(self):
        import journal_lifecycle
        original, _ = self.native_generation(self.generation_request(), 'predecessor-generation')
        request = self.report_request('author')
        request['report']['evidence'] = [original]
        self.record('report', request, 'predecessor-report')
        child = '00000000-0000-4000-8000-000000000004'
        assignment = {**self.f.known['author'], 'id': 'child', 'agent_id': child, 'depends_on': ['author']}
        self.f.source.sessions[child] = base.session(child, 'python-worker', cwd=self.f.root)
        self.record('assignment', assignment, 'assign-correction')
        request = {**self.generation_request('generated = 4\n'), 'assignment_id': 'child'}
        latest, event = self.native_generation(request, 'child-correction', thread=child)
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Corrected generated result',
                  'authored_paths': [str(self.f.product)], 'evidence': [latest]}
        self.f.source.sessions[child] = base.session(child, 'python-worker', report, cwd=self.f.root)
        self.f.source.sessions[child].insert(-2, event)
        self.record('report', {'assignment_id': 'child', 'completion_turn_id': child + '-turn'}, 'correction-report')
        self.f.product.write_text('unobserved = 5\n')
        for key in ('review', 'qa'):
            self.record('report', self.report_request(key), 'unobserved-' + key)

    def test_successful_noop_generation_is_evidence_and_failed_generation_is_not(self):
        ref, event = self.native_generation(self.generation_request('answer = 2\n'), 'no-write')
        evidence.verify_evidence(self.f.snapshot(), [ref], self.f.source, root_id=base.ROOT_ID)
        self.assertEqual(evidence.generation_evidence(self.f.snapshot(), ref, self.f.source), [str(self.f.product)])
        evidence.verify_evidence(self.f.snapshot(), [{**ref, 'sha256': 'obsolete'}], self.f.source, root_id=base.ROOT_ID)
        request = self.generation_request()
        request['command'][-1] += '; raise SystemExit(7)'
        result = self.record('generation', request, 'failed-write')
        receipt = result['receipt']
        failed_ref = {'path': receipt['result']['path'], 'native': ref['native']}
        with self.assertRaisesRegex(JournalError, 'command did not succeed'):
            evidence.verify_evidence(self.f.snapshot(), [failed_ref], self.f.source, root_id=base.ROOT_ID)

    def test_generated_child_cannot_claim_root_execution_through_replay(self):
        child = '00000000-0000-4000-8000-000000000004'
        self.record('report', self.report_request('author'), 'initial-root-author')
        assignment = {**self.f.known['author'], 'id': 'child', 'agent_id': child, 'depends_on': ['author']}
        self.f.source.sessions[child] = base.session(child, 'python-worker', cwd=self.f.root)
        self.record('assignment', assignment, 'assign-generation-child')
        request = {**self.generation_request(), 'assignment_id': 'child'}
        root_ref, _ = self.native_generation(request, 'root-executed-for-child')
        with self.assertRaisesRegex(JournalError, 'own author native execution'):
            evidence.verify_evidence(self.f.snapshot(), [root_ref], self.f.source, root_id=base.ROOT_ID)
        replay_ref, _ = self.native_generation(request, 'root-executed-for-child', thread=child)
        with self.assertRaisesRegex(JournalError, 'not a replay'):
            evidence.verify_evidence(self.f.snapshot(), [replay_ref], self.f.source, root_id=base.ROOT_ID)
        request = {**self.generation_request('generated = 4\n'), 'assignment_id': 'child'}
        own_ref, own_event = self.native_generation(request, 'child-execution', thread=child)
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Generated current result', 'authored_paths': [str(self.f.product)], 'evidence': [own_ref]}
        self.f.source.sessions[child] = base.session(child, 'python-worker', report, cwd=self.f.root)
        self.f.source.sessions[child].insert(-2, own_event)
        accepted = self.record('report', {'assignment_id': 'child', 'completion_turn_id': child + '-turn'}, 'generated-child-report')
        self.assertEqual(accepted['result']['path'], 'reports/child.json')

    def test_artifact_immutable_bytes_and_evidence_reference(self):
        source = self.f.root.parent / (self.f.root.name + '-output')
        self.addCleanup(source.unlink, missing_ok=True)
        source.write_bytes(b'raw\x00evidence\n')
        receipt = writer.publish_artifact(self.f.run, 'artifacts/output.bin', source, identifier='publish-output')
        ref = {'path': receipt['result']['path']}
        self.assertEqual(self.f.snapshot().read_bytes(ref['path']), source.read_bytes())
        evidence.verify_evidence(self.f.snapshot(), [ref], self.f.source, root_id=base.ROOT_ID)
        before = self.f.snapshot()
        source.write_bytes(b'different')
        with self.assertRaisesRegex(JournalError, 'different bytes'):
            writer.publish_artifact(self.f.run, ref['path'], source, identifier='overwrite-output')
        evidence.verify_evidence(self.f.snapshot(), [{**ref, 'sha256': '0' * 64}], self.f.source, root_id=base.ROOT_ID)
        with self.assertRaises(JournalError):
            evidence.verify_evidence(self.f.snapshot(), [{'path': '../outside'}], self.f.source, root_id=base.ROOT_ID)
        self.assert_unchanged(before)

    def test_native_read_ignores_late_previous_turn_command(self):
        thread, earlier_turn, turn = base.ROOT_ID, base.ROOT_ID + '-turn', 'root-inspection-next'
        assignment = {**self.f.known['author'], 'id': 'resources', 'kind': 'analysis', 'allowed_paths': []}
        events = base.session(thread, assignment['role'], cwd=self.f.root)
        events[3]['payload']['input'] = '{"cmd":"start fixture command"}'
        events[4]['payload']['output'] = '{"session_id":17,"output":"Command still running"}'
        self.f.source.sessions[thread] = events
        self.record('assignment', assignment, 'assign-resources')
        events.append({'type': 'event_msg', 'payload': {
            'type': 'task_complete', 'turn_id': earlier_turn, 'last_agent_message': '{}'}})
        continuation = base.session(thread, assignment['role'], cwd=self.f.root, turn=turn)[1:]
        events.extend(continuation[:2])
        events.append({'type': 'event_msg', 'payload': {
            'type': 'item_completed', 'thread_id': thread, 'turn_id': earlier_turn,
            'item': {'id': 'exec-delayed-command', 'type': 'CommandExecution', 'status': 'completed',
                     'exit_code': 0, 'aggregated_output': 'Fixture command stopped'}}})
        events.extend(continuation[2:])
        before, output = self.f.snapshot(), StringIO()
        with patch.object(evidence, 'CodexSessionSource', return_value=self.f.source), redirect_stdout(output):
            self.assertEqual(journal.main(['--run-dir', str(self.f.run), 'read', 'native/resources']), 0)
        observed = json.loads(output.getvalue())
        self.assertEqual(observed['turn_id'], turn)
        self.assertFalse(observed['completed'])
        self.assertEqual([item['ref'] for item in observed['observations']], [self.f.ref(thread, turn)])
        self.assertTrue(observed['observations'][0]['success'])
        self.assert_unchanged(before)
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Inspected current resources',
                  'evidence': [observed['observations'][0]['ref']]}
        self.record('report', {'assignment_id': 'resources', 'turn_id': turn, 'report': report}, 'resources-report')
        accepted = self.f.snapshot().json('reports/resources.json')
        self.assertEqual(accepted['source']['turn_id'], turn)
        self.assertEqual(accepted['evidence'], report['evidence'])
        before = self.f.snapshot()
        forged = {**report, 'evidence': [{'thread_id': thread, 'turn_id': turn, 'item_id': 'exec-delayed-command'}]}
        with self.assertRaisesRegex(JournalError, 'native item thread or turn mismatch'):
            evidence.native_item(self.f.source, forged['evidence'][0], root_id=base.ROOT_ID)
        with self.assertRaisesRegex(JournalError, 'immutable'):
            self.record('report', {'assignment_id': 'resources', 'turn_id': turn, 'report': forged}, 'forged-resources-report')
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

    def test_read_result_returns_declared_author_paths_only(self):
        (self.f.root / 'other-chat.md').write_text('Another task changed\n')
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(journal.main(['--run-dir', str(self.f.run), 'read', 'result']), 0)
        self.assertEqual(json.loads(output.getvalue()), {'authored_paths': []})
        self.record('report', self.report_request('author'), 'author-report')
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(journal.main(['--run-dir', str(self.f.run), 'read', 'result']), 0)
        self.assertEqual(json.loads(output.getvalue()), {'authored_paths': [str(self.f.product)]})


if __name__ == '__main__':
    unittest.main()
