#!/usr/bin/env python3
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agent_config import default_agents_dir, read_frontmatter, resolve_role_path, role_config
from journal_io import JournalSnapshot, encode_json, initialize_journal, transact
from task_workspace import bind_workspace
from verification_evidence import (CodexSessionSource, JournalError, accept_report, native_references, native_tool, requirements, result_fingerprint,
                                   sha256, validate_acceptance)

ROOT_ID = '00000000-0000-4000-8000-000000000001'
QA_ID = '00000000-0000-4000-8000-000000000002'
REVIEWER_ID = '00000000-0000-4000-8000-000000000003'


class SyntheticSource:
    def __init__(self):
        self.sessions = {}

    def read(self, thread_id, *, event_indices=()):
        if thread_id not in self.sessions:
            raise JournalError('execution unconfirmed: synthetic session missing')
        return copy.deepcopy(self.sessions[thread_id])


def session(thread_id, role, answer=None, *, root=ROOT_ID, cwd=None, patch=None, turn=None):
    turn = turn or thread_id + '-turn'
    meta = {'id': thread_id, 'session_id': root, 'cwd': str(cwd or '/')}
    if thread_id != root:
        spawn = {'parent_thread_id': root, 'agent_role': role}
        meta.update(**spawn, source={'subagent': {'thread_spawn': spawn}})
    model = role_config(read_frontmatter(resolve_role_path(default_agents_dir(), role)), role)['model']
    events = [
        {'type': 'session_meta', 'payload': meta},
        {'type': 'event_msg', 'timestamp': '2026-10-19T10:00:00+00:00', 'payload': {'type': 'task_started', 'turn_id': turn}},
        {'type': 'turn_context', 'payload': {'turn_id': turn, 'model': model}},
        {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'call_id': 'tool', 'name': 'apply_patch' if patch else 'exec_command', 'input': patch or '{"cmd":"inspect result"}'}},
        {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'call_id': 'tool', 'output': 'Success. Updated the following files.' if patch else '{"exit_code":0,"output":"Inspection completed."}'}},
    ]
    if answer is not None:
        text = json.dumps(answer)
        events.extend([
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': text}]}},
            {'type': 'event_msg', 'timestamp': '2026-10-19T10:00:01+00:00', 'payload': {'type': 'task_complete', 'turn_id': turn, 'last_agent_message': text}},
        ])
    return events


class Fixture:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.plan = self.root / 'todo.md'
        self.plan.write_text('# Tasks\n\n## Sample\n\n### Критерии приёмки\n\n- [ ] Result is correct\n\n## Other\n\nUnrelated task\n')
        self.product = self.root / 'result.py'
        self.product.write_text('answer = 1\n')
        self.run = self.root / '.agent-work' / 'runs' / 'sample'
        self.scope = bind_workspace(self.root, run_dir=self.run, memory=[str(self.plan)])
        self.source = SyntheticSource()
        self.source.sessions[ROOT_ID] = session(ROOT_ID, 'python-worker', cwd=self.root,
            patch='*** Begin Patch\n*** Update File: result.py\n@@\n-answer = 1\n+answer = 2\n*** End Patch')
        self.known = {
            'author': {'id': 'author', 'kind': 'author', 'role': 'python-worker', 'agent_id': ROOT_ID, 'allowed_paths': [str(self.product)], 'depends_on': []},
            'qa': {'id': 'qa', 'kind': 'qa', 'role': 'qa-verifier', 'agent_id': QA_ID, 'allowed_paths': [], 'depends_on': ['author']},
            'review': {'id': 'review', 'kind': 'reviewer', 'role': 'reviewer', 'agent_id': REVIEWER_ID, 'allowed_paths': [], 'depends_on': ['qa']},
        }
        docs = {'task.json': encode_json({'summary': 'Sample', 'requirements': {'path': str(self.plan), 'task_heading': 'Sample', 'criteria_heading': 'Критерии приёмки'}}).encode(),
                'scope.json': encode_json(self.scope).encode()}
        docs.update({'assignments/' + key + '.json': encode_json(value).encode() for key, value in self.known.items()})
        initialize_journal(self.run, docs, source_root=self.root, identities={'root_agent_id': ROOT_ID})
        self.product.write_text('answer = 2\n')

    def snapshot(self):
        return JournalSnapshot.open(self.run)

    def ref(self, thread, turn=None):
        return {'thread_id': thread, 'turn_id': turn or thread + '-turn', 'call_id': 'tool'}

    def save(self, key, report=None, *, turn=None):
        if key == 'author':
            report = report or {'status': 'completed', 'summary': 'Implemented result', 'authored_paths': [str(self.product)], 'evidence': [self.ref(ROOT_ID, turn)]}
            request = {'turn_id': turn or ROOT_ID + '-turn', 'report': report}
        else:
            if report is None:
                report = {'status': 'completed', 'summary': 'Checked result', 'fingerprint': result_fingerprint(self.snapshot()), 'evidence': [self.ref(self.known[key]['agent_id'], turn)]}
                if key == 'qa':
                    report['criteria'] = {'ac-1': {'status': 'passed', 'evidence': [self.ref(QA_ID, turn)]}}
                else:
                    report['qa'] = {'assignment_id': 'qa', 'sha256': sha256(self.snapshot().read_bytes('reports/qa.json'))}
            thread = self.known[key]['agent_id']
            self.source.sessions[thread] = session(thread, self.known[key]['role'], report, cwd=self.root, turn=turn)
            request = {'completion_turn_id': turn or thread + '-turn'}
        canonical = accept_report(self.snapshot(), self.known[key], request, self.source)
        transact(self.run, 'save-' + key + '-' + sha256(encode_json(canonical).encode()), {'report': canonical},
                 lambda current: ({'reports/' + key + '.json': encode_json(canonical).encode()}, {'path': 'reports/' + key + '.json'}))
        return canonical

    def completed(self):
        for key in ('author', 'qa', 'review'):
            self.save(key)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='af-evidence-')
        self.addCleanup(self.temporary.cleanup)
        self.f = Fixture(self.temporary.name)

    def test_real_root_author_and_independent_native_reports(self):
        self.f.completed()
        self.assertEqual(validate_acceptance(self.f.snapshot(), self.f.source), [])

    def test_native_reference_lookup_lists_completed_root_tools_only(self):
        events = self.f.source.sessions[ROOT_ID]
        events.extend([
            {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'collaboration.send_message', 'call_id': 'empty-message', 'arguments': '{}'}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'empty-message', 'output': ''}},
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'name': 'apply_patch', 'call_id': 'failed', 'input': '*** Update File: result.py\n'}},
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'call_id': 'failed', 'output': 'apply_patch verification failed'}},
            {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'current-lookup', 'input': 'lookup native references'}},
        ])
        observed = native_references(self.f.snapshot(), 'author', self.f.source)
        self.assertEqual(observed['turn_id'], ROOT_ID + '-turn')
        self.assertFalse(observed['completed'])
        self.assertEqual([item['ref']['call_id'] for item in observed['observations']], ['tool', 'failed'])
        self.assertEqual(observed['observations'][0]['authored_paths'], [str(self.f.product)])
        self.assertTrue(observed['observations'][0]['success'])
        self.assertFalse(observed['observations'][1]['success'])
        self.assertNotIn('input', json.dumps(observed))
        with self.assertRaisesRegex(JournalError, 'output unavailable'):
            native_tool(self.f.source, {'thread_id': ROOT_ID, 'turn_id': ROOT_ID + '-turn', 'call_id': 'empty-message'})

    def test_native_reference_lookup_uses_completed_child_and_checks_identity(self):
        self.f.save('author')
        self.f.save('qa')
        observed = native_references(self.f.snapshot(), 'qa', self.f.source)
        self.assertTrue(observed['completed'])
        self.assertEqual(observed['observations'][0]['ref'], self.f.ref(QA_ID))
        self.assertTrue(observed['observations'][0]['success'])
        self.f.source.sessions[QA_ID][0]['payload']['parent_thread_id'] = REVIEWER_ID
        with self.assertRaisesRegex(JournalError, 'parent_thread_id mismatch'):
            native_references(self.f.snapshot(), 'qa', self.f.source)

    def test_canonical_task_path_resolves_actual_uuid_and_role(self):
        directory = self.f.root / 'native'
        directory.mkdir()
        events = session(QA_ID, 'qa-verifier', cwd=self.f.root)
        meta = events[0]['payload']
        meta['agent_path'] = '/root/qa'
        meta['source']['subagent']['thread_spawn']['agent_path'] = '/root/qa'
        path = directory / ('rollout-' + QA_ID + '.jsonl')
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        with patch.object(CodexSessionSource, 'session_dir', return_value=directory):
            self.assertEqual(CodexSessionSource().resolve_session('/root/qa', ROOT_ID, 'qa-verifier')['codex_thread_id'], QA_ID)
            with self.assertRaisesRegex(JournalError, 'agent_role mismatch'):
                CodexSessionSource().resolve_session('/root/qa', ROOT_ID, 'reviewer')

    def test_child_author_reads_native_answer_not_request_body(self):
        assignment = {**self.f.known['author'], 'agent_id': QA_ID}
        report = {'status': 'completed', 'summary': 'Native author', 'authored_paths': [str(self.f.product)], 'evidence': [self.f.ref(QA_ID)]}
        self.f.source.sessions[QA_ID] = session(QA_ID, 'python-worker', report, cwd=self.f.root, patch='*** Update File: result.py\n')
        result = accept_report(self.f.snapshot(), assignment, {'completion_turn_id': QA_ID + '-turn', 'report': {'summary': 'Forged'}}, self.f.source)
        self.assertEqual(result['summary'], 'Native author')

    def test_root_analysis_uses_current_native_inspection_without_author_paths(self):
        assignment = {**self.f.known['author'], 'kind': 'analysis', 'allowed_paths': []}
        self.f.source.sessions[ROOT_ID] = session(ROOT_ID, 'python-worker', cwd=self.f.root)
        body = {'status': 'completed', 'summary': 'Analyzed current sources', 'evidence': [self.f.ref(ROOT_ID)]}
        accepted = accept_report(self.f.snapshot(), assignment, {'turn_id': ROOT_ID + '-turn', 'report': body}, self.f.source)
        self.assertEqual(accepted['summary'], body['summary'])
        self.assertNotIn('authored_paths', accepted)
        self.f.source.sessions[ROOT_ID][4]['payload']['output'] = 'Unconfirmed inspection'
        with self.assertRaisesRegex(JournalError, 'successful native inspection'):
            accept_report(self.f.snapshot(), assignment, {'turn_id': ROOT_ID + '-turn', 'report': body}, self.f.source)

    def test_missing_edit_evidence_refuses_author(self):
        self.f.source.sessions[ROOT_ID][3]['payload']['input'] = 'inspect result.py'
        with self.assertRaisesRegex(JournalError, 'native edit'):
            self.f.save('author')

    def test_incomplete_and_stale_native_turn_refuse(self):
        self.f.save('author')
        report = {'status': 'completed', 'summary': 'Incomplete', 'fingerprint': result_fingerprint(self.f.snapshot()), 'evidence': [self.f.ref(QA_ID)]}
        with self.assertRaisesRegex(JournalError, 'every acceptance criterion'):
            self.f.save('qa', report)
        self.f.source.sessions[QA_ID].append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'new'}})
        with self.assertRaisesRegex(JournalError, 'stale'):
            accept_report(self.f.snapshot(), self.f.known['qa'], {'completion_turn_id': QA_ID + '-turn'}, self.f.source)

    def test_new_qa_final_cannot_reuse_inspection_from_earlier_turn(self):
        self.f.save('author')
        self.f.save('qa')
        events = self.f.source.sessions[QA_ID]
        body = json.loads(events[-1]['payload']['last_agent_message'])
        new_turn = 'qa-new-turn'
        events.extend([
            {'type': 'event_msg', 'timestamp': '2026-10-19T10:01:00+00:00', 'payload': {'type': 'task_started', 'turn_id': new_turn}},
            {'type': 'turn_context', 'payload': {'turn_id': new_turn, 'model': events[2]['payload']['model']}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': json.dumps(body)}]}},
            {'type': 'event_msg', 'timestamp': '2026-10-19T10:01:01+00:00', 'payload': {'type': 'task_complete', 'turn_id': new_turn, 'last_agent_message': json.dumps(body)}},
        ])
        with self.assertRaisesRegex(JournalError, 'own successful native inspection'):
            accept_report(self.f.snapshot(), self.f.known['qa'], {'completion_turn_id': new_turn}, self.f.source)

    def test_source_change_and_author_report_replacement_invalidate_qa(self):
        self.f.completed()
        self.f.product.write_text('answer = 3\n')
        self.assertIn('stale', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source)))
        self.f.product.write_text('answer = 2\n')
        report = {'status': 'completed', 'summary': 'Clarified implementation', 'authored_paths': [str(self.f.product)], 'evidence': [self.f.ref(ROOT_ID)]}
        self.f.save('author', report)
        self.assertIn('stale', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source)))

    def test_criteria_checkbox_normalizes_but_text_change_invalidates(self):
        initial = requirements(self.f.snapshot())['digest']
        fingerprint = result_fingerprint(self.f.snapshot())
        self.f.plan.write_text(self.f.plan.read_text().replace('[ ]', '[x]').replace('Unrelated task', 'Changed unrelated task'))
        self.assertEqual(requirements(self.f.snapshot())['digest'], initial)
        self.assertEqual(result_fingerprint(self.f.snapshot()), fingerprint)
        self.f.plan.write_text(self.f.plan.read_text().replace('Result is correct', 'Result has tests'))
        self.assertNotEqual(requirements(self.f.snapshot())['digest'], initial)

    def test_native_qa_tamper_is_rejected(self):
        self.f.completed()
        events = self.f.source.sessions[QA_ID]
        answer = json.loads(events[-1]['payload']['last_agent_message'])
        answer['summary'] = 'Tampered native final'
        events[-1]['payload']['last_agent_message'] = json.dumps(answer)
        self.assertIn('disagree', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source)))

    def test_observed_tool_output_is_pinned_and_root_continuation_is_allowed(self):
        self.f.completed()
        self.f.source.sessions[ROOT_ID].append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'root-next-turn'}})
        self.assertEqual(validate_acceptance(self.f.snapshot(), self.f.source), [])
        self.f.source.sessions[QA_ID][4]['payload']['output'] = '{"exit_code":0,"output":"Different inspection"}'
        self.assertIn('stale', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source)))

    def test_failed_native_check_cannot_prove_passed_criterion(self):
        self.f.completed()
        self.f.source.sessions[QA_ID][4]['payload']['output'] = '{"exit_code":1,"output":"PASS"}'
        self.assertIn('successful native inspection', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source)))

    def test_unverified_criterion_accepts_missing_empty_or_valid_evidence(self):
        self.f.save('author')
        for evidence in ({}, {'evidence': []}, {'evidence': [self.f.ref(QA_ID)]}):
            with self.subTest(evidence=evidence):
                outcome = {'status': 'unverified', 'reason': 'Required runtime check is forbidden', **evidence}
                report = {'status': 'failed', 'summary': 'Inspected available sources',
                          'reason': 'Required runtime check is forbidden', 'evidence': [self.f.ref(QA_ID)],
                          'fingerprint': result_fingerprint(self.f.snapshot()), 'criteria': {'ac-1': outcome}}
                accepted = self.f.save('qa', report)
                self.assertEqual(accepted['criteria']['ac-1'], outcome)
                self.assertEqual(accepted['status'], 'failed')
                self.assertIn('assignment failed: qa', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source)))

    def test_criterion_errors_identify_the_criterion(self):
        self.f.save('author')
        cases = [
            ({'status': 'unverified'}, 'reason required'),
            ({'status': 'unverified', 'reason': ' ', 'evidence': [self.f.ref(QA_ID)]}, 'reason required'),
            ({'status': 'unverified', 'reason': 'Forbidden', 'evidence': None}, 'must be an array'),
            ({'status': 'unverified', 'reason': 'Forbidden', 'evidence': {}}, 'must be an array'),
            ({'status': 'unverified', 'reason': 'Forbidden', 'evidence': 'none'}, 'must be an array'),
            ({'status': 'unverified', 'reason': 'Forbidden',
              'evidence': [{'path': 'artifacts/missing.txt', 'sha256': '0' * 64}]}, 'artifact evidence missing'),
            ({'status': 'passed'}, 'nonempty array'),
            ({'status': 'failed', 'evidence': []}, 'nonempty array'),
        ]
        for outcome, message in cases:
            with self.subTest(outcome=outcome):
                report = {'status': 'failed', 'summary': 'Inspected sources', 'reason': 'Result not accepted',
                          'evidence': [self.f.ref(QA_ID)], 'fingerprint': result_fingerprint(self.f.snapshot()),
                          'criteria': {'ac-1': outcome}}
                with self.assertRaisesRegex(JournalError, 'criterion ac-1: .*' + message):
                    self.f.save('qa', report)
                self.assertFalse(self.f.snapshot().exists('reports/qa.json'))

    def test_narrative_attachment_alone_cannot_prove_criterion(self):
        self.f.save('author')
        transact(self.f.run, 'attachment', {'artifact': 'narrative'},
                 lambda current: ({'artifacts/pass.txt': b'PASS'}, {}))
        report = {'status': 'completed', 'summary': 'Checked', 'fingerprint': result_fingerprint(self.f.snapshot()),
                  'evidence': [self.f.ref(QA_ID)],
                  'criteria': {'ac-1': {'status': 'passed', 'evidence': [{'path': 'artifacts/pass.txt', 'sha256': sha256(b'PASS')}]}}}
        with self.assertRaisesRegex(JournalError, 'successful check or native inspection'):
            self.f.save('qa', report)

    def test_wrapped_apply_patch_uses_observed_successful_tool_output(self):
        events = self.f.source.sessions[ROOT_ID]
        patch_text = events[3]['payload']['input']
        events[3]['payload'].update(name='exec', input='text(await tools.apply_patch(' + json.dumps(patch_text) + '));')
        events[4]['payload']['output'] = [{'type': 'input_text', 'text': 'Script completed\nOutput:\n'}, {'type': 'input_text', 'text': '{}'}]
        self.f.save('author')
        events[4]['payload']['output'] = [{'type': 'input_text', 'text': 'Script error: apply_patch verification failed'}]
        with self.assertRaisesRegex(JournalError, 'did not succeed'):
            self.f.save('author')
        events[4]['payload']['output'] = [{'type': 'input_text', 'text': 'Script completed\nOutput:\n{"isError":true,"message":"apply_patch verification failed"}'}]
        with self.assertRaisesRegex(JournalError, 'did not succeed'):
            self.f.save('author')
        events[3]['payload']['input'] = 'text(' + json.dumps('await tools.apply_patch(' + json.dumps(patch_text) + ')') + ');'
        events[4]['payload']['output'] = [{'type': 'input_text', 'text': 'Script completed\nOutput:\nSuccess. Updated'}]
        with self.assertRaisesRegex(JournalError, 'no native edit evidence'):
            self.f.save('author')

    def test_native_identity_model_and_completion_are_required(self):
        self.f.completed()
        original = copy.deepcopy(self.f.source.sessions[QA_ID])
        self.f.source.sessions[QA_ID][0]['payload']['parent_thread_id'] = REVIEWER_ID
        self.assertIn('parent_thread_id mismatch', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source)))
        self.f.source.sessions[QA_ID] = copy.deepcopy(original)
        self.f.source.sessions[QA_ID][2]['payload']['model'] = 'unregistered-model'
        self.assertIn('source model', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source)))
        self.f.source.sessions[QA_ID] = original[:-1]
        self.assertIn('completed turn missing', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source)))

    def test_author_path_permission_and_changed_coverage(self):
        self.f.known['author']['allowed_paths'] = [str(self.f.root / 'other.py')]
        with self.assertRaisesRegex(JournalError, 'outside assignment'):
            self.f.save('author')

    def test_actual_check_reference_before_author_report_has_no_hash_cycle(self):
        from journal_io import write_entry
        receipt = write_entry(self.f.run, 'check', {'id': 'actual', 'assignment_id': 'author',
                              'command': [sys.executable, '-B', '-c', 'print("checked")'], 'cwd': str(self.f.root)}, identifier='check')
        receipt = receipt['result']
        report = {'status': 'completed', 'summary': 'Implemented result', 'authored_paths': [str(self.f.product)],
                  'evidence': [self.f.ref(ROOT_ID), {'path': receipt['path'], 'sha256': receipt['sha256']}]}
        self.f.save('author', report)
        self.f.save('qa')
        self.f.save('review')
        self.assertEqual(validate_acceptance(self.f.snapshot(), self.f.source), [])

    def test_old_uncited_failed_check_does_not_block_fresh_result(self):
        from journal_io import write_entry
        write_entry(self.f.run, 'check', {'id': 'old', 'assignment_id': 'author',
                    'command': [sys.executable, '-B', '-c', 'raise SystemExit(1)'], 'cwd': str(self.f.root)}, identifier='old-check')
        self.f.product.write_text('answer = 3\n')
        self.f.completed()
        self.assertEqual(validate_acceptance(self.f.snapshot(), self.f.source), [])


if __name__ == '__main__':
    unittest.main()
