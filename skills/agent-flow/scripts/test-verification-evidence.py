#!/usr/bin/env python3
import copy
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agent_config import default_agents_dir, read_frontmatter, resolve_role_path, role_config
from journal_io import JournalSnapshot, encode_json, initialize_journal, transact
from task_workspace import bind_workspace
from verification_evidence import (CodexSessionSource, JournalError, accept_report, native_item, native_references,
                                   native_tool, requirements, validate_acceptance, verify_evidence)

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


def file_change(thread_id, path, *, status='completed'):
    return {'type': 'event_msg', 'payload': {
        'type': 'item_completed', 'thread_id': thread_id, 'turn_id': thread_id + '-turn',
        'item': {'id': 'exec-native-patch', 'type': 'FileChange', 'status': status,
                 'changes': {str(path): {'type': 'update', 'move_path': None, 'unified_diff': 'actual native patch'}},
                 'stdout': '', 'stderr': ''},
    }}


class Fixture:
    def __init__(self, directory, *, name='sample'):
        self.root = Path(directory).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.plan = self.root / ('todo.md' if name == 'sample' else name + '-todo.md')
        self.plan.write_text('# Tasks\n\n## Sample\n\n### Критерии приёмки\n\n- [ ] Result is correct\n\n## Other\n\nUnrelated task\n')
        self.product = self.root / ('result.py' if name == 'sample' else name + '-result.py')
        self.product.write_text('answer = 1\n')
        self.run = self.root / '.agent-work' / 'runs' / name
        self.saved = 0
        self.scope = bind_workspace(self.root, run_dir=self.run, memory=[str(self.plan)])
        self.source = SyntheticSource()
        self.source.sessions[ROOT_ID] = session(ROOT_ID, 'python-worker', cwd=self.root,
            patch='*** Begin Patch\n*** Update File: ' + self.product.name + '\n@@\n-answer = 1\n+answer = 2\n*** End Patch')
        self.known = {
            'author': {'id': 'author', 'kind': 'author', 'role': 'python-worker', 'agent_id': ROOT_ID, 'allowed_paths': [str(self.product)], 'depends_on': [], 'start_turn_id': ROOT_ID + '-turn'},
            'qa': {'id': 'qa', 'kind': 'qa', 'role': 'qa-verifier', 'agent_id': QA_ID, 'allowed_paths': [], 'depends_on': ['review']},
            'review': {'id': 'review', 'kind': 'reviewer', 'role': 'reviewer', 'agent_id': REVIEWER_ID, 'allowed_paths': [], 'depends_on': ['author']},
        }
        docs = {'task.json': encode_json({'contract_version': 2, 'summary': 'Sample', 'requirements': {'path': str(self.plan), 'task_heading': 'Sample', 'criteria_heading': 'Критерии приёмки'}}).encode(),
                'scope.json': encode_json(self.scope).encode()}
        docs.update({'assignments/' + key + '.json': encode_json(value).encode() for key, value in self.known.items()})
        initialize_journal(self.run, docs, source_root=self.root, identities={'root_agent_id': ROOT_ID})
        self.product.write_text('answer = 2\n')

    def snapshot(self):
        return JournalSnapshot.open(self.run)

    def ref(self, thread, turn=None):
        return {'thread_id': thread, 'turn_id': turn or thread + '-turn', 'call_id': 'tool'}

    def save(self, key, report=None, *, turn=None):
        kind = self.known[key]['kind']
        if kind in {'author', 'analysis'}:
            report = report or {'status': 'completed', 'outcome': 'passed', 'summary': 'Implemented result', 'authored_paths': [str(self.product)], 'evidence': [self.ref(ROOT_ID, turn)]}
            request = {'turn_id': turn or ROOT_ID + '-turn', 'report': report}
        else:
            if report is None:
                report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Checked result', 'evidence': [self.ref(self.known[key]['agent_id'], turn)]}
                if kind == 'qa':
                    report['criteria'] = {'ac-1': {'status': 'passed', 'evidence': [self.ref(QA_ID, turn)]}}
            if kind == 'qa':
                review = next(value for value in self.known[key]['depends_on'] if self.known[value]['kind'] == 'reviewer')
                report = {**report, 'reviewer': {'assignment_id': review}}
            thread = self.known[key]['agent_id']
            events = session(thread, self.known[key]['role'], report, cwd=self.root, turn=turn)
            if thread in self.source.sessions and turn:
                self.source.sessions[thread].extend(events[1:])
            else:
                self.source.sessions[thread] = events
            request = {'completion_turn_id': turn or thread + '-turn'}
        canonical = accept_report(self.snapshot(), self.known[key], request, self.source)
        self.saved += 1
        transact(self.run, 'save-' + key + '-' + str(self.saved), {'report': canonical},
                 lambda current: ({'reports/' + key + '.json': encode_json(canonical).encode()}, {'path': 'reports/' + key + '.json'}))
        return canonical

    def completed(self):
        for key in ('author', 'review', 'qa'):
            self.save(key)

    def reviewed(self):
        self.save('author')
        self.save('review')


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='af-evidence-')
        self.addCleanup(self.temporary.cleanup)
        self.f = Fixture(self.temporary.name)

    def native_source(self):
        temporary = tempfile.TemporaryDirectory(prefix='af-native-evidence-')
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name).resolve()
        (home / 'sessions').mkdir()
        with closing(sqlite3.connect(home / 'state_5.sqlite')) as connection:
            connection.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, agent_path TEXT, source TEXT)')
        environment = patch.dict(os.environ, {'CODEX_HOME': str(home)})
        environment.start()
        self.addCleanup(environment.stop)
        return CodexSessionSource(), home

    def indexed_session(self, home, thread_id, events, *, suffix='', agent_path=None):
        path = home / 'sessions' / ('rollout-' + thread_id + suffix + '.jsonl')
        path.write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
        source = CodexSessionSource()
        metadata = next(event['payload'] for event in events if event['type'] == 'session_meta')
        with closing(sqlite3.connect(home / 'state_5.sqlite')) as connection:
            connection.execute('INSERT OR REPLACE INTO threads VALUES (?, ?, ?, ?)',
                               (thread_id, str(path), agent_path, json.dumps(metadata.get('source', {}))))
            connection.commit()
        return path

    def test_real_root_author_and_independent_native_reports(self):
        self.f.completed()
        self.assertEqual(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa'), [])
        review = self.f.snapshot().json('reports/review.json')
        qa = self.f.snapshot().json('reports/qa.json')
        self.assertNotIn('authors', review['source'])
        self.assertEqual(qa['reviewer'], {'assignment_id': 'review'})
        self.assertNotIn('qa', review)
        self.assertNotIn('authors', qa['source'])

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
        self.f.reviewed()
        self.f.save('qa')
        observed = native_references(self.f.snapshot(), 'qa', self.f.source)
        self.assertTrue(observed['completed'])
        self.assertEqual(observed['observations'][0]['ref'], self.f.ref(QA_ID))
        self.assertTrue(observed['observations'][0]['success'])
        self.f.source.sessions[QA_ID][0]['payload']['parent_thread_id'] = REVIEWER_ID
        with self.assertRaisesRegex(JournalError, 'parent_thread_id mismatch'):
            native_references(self.f.snapshot(), 'qa', self.f.source)

    def test_native_file_change_confirms_dynamic_patch_without_interpreting_javascript(self):
        source, home = self.native_source()
        events = copy.deepcopy(self.f.source.sessions[ROOT_ID])
        events[3]['payload'].update(name='exec', input='const before = load("result.py"); text(await tools.apply_patch(before));')
        events[4]['payload']['output'] = 'Script completed\nOutput:\n{}'
        events.append(file_change(ROOT_ID, self.f.product))
        path = self.indexed_session(home, ROOT_ID, events)
        source = CodexSessionSource()
        observed = native_references(self.f.snapshot(), 'author', source)
        ref = {'thread_id': ROOT_ID, 'turn_id': ROOT_ID + '-turn', 'item_id': 'exec-native-patch'}
        self.assertEqual(observed['observations'][-1], {
            'ref': ref, 'name': 'FileChange', 'success': True, 'authored_paths': [str(self.f.product)]})
        self.assertFalse(observed['observations'][0]['success'])
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Implemented result',
                  'authored_paths': [str(self.f.product)], 'evidence': [ref]}
        accepted = accept_report(self.f.snapshot(), self.f.known['author'],
                                 {'turn_id': ref['turn_id'], 'report': report}, source)
        self.assertEqual(accepted['authored_paths'], [str(self.f.product)])
        self.assertNotIn('unified_diff', json.dumps(observed))
        with self.assertRaisesRegex(JournalError, 'successful check or native inspection'):
            verify_evidence(self.f.snapshot(), [ref], source, root_id=ROOT_ID, success_required=True)
        events.pop()
        events[4]['payload']['output'] = json.dumps(file_change(ROOT_ID, self.f.product))
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        source = CodexSessionSource()
        with self.assertRaisesRegex(JournalError, 'native item missing'):
            accept_report(self.f.snapshot(), self.f.known['author'],
                          {'turn_id': ref['turn_id'], 'report': report}, source)

    def test_native_file_change_rejects_failed_foreign_and_duplicate_items(self):
        source, home = self.native_source()
        ref = {'thread_id': ROOT_ID, 'turn_id': ROOT_ID + '-turn', 'item_id': 'exec-native-patch'}
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Implemented result',
                  'authored_paths': [str(self.f.product)], 'evidence': [ref]}
        original = self.f.source.sessions[ROOT_ID] + [file_change(ROOT_ID, self.f.product)]
        for alteration, message in (('failed', 'did not succeed'), ('foreign-thread', 'thread or turn mismatch'),
                                    ('foreign-turn', 'thread or turn mismatch'), ('duplicate', 'ambiguous')):
            with self.subTest(alteration=alteration):
                events = copy.deepcopy(original)
                item = events[-1]['payload']
                if alteration == 'failed':
                    item['item']['status'] = 'failed'
                elif alteration == 'foreign-thread':
                    item['thread_id'] = QA_ID
                elif alteration == 'foreign-turn':
                    item['turn_id'] = 'another-turn'
                else:
                    events.append(copy.deepcopy(events[-1]))
                self.indexed_session(home, ROOT_ID, events)
                source = CodexSessionSource()
                with self.assertRaisesRegex(JournalError, message):
                    accept_report(self.f.snapshot(), self.f.known['author'],
                                  {'turn_id': ref['turn_id'], 'report': report}, source)
                if alteration == 'failed':
                    observed = native_references(self.f.snapshot(), 'author', source)
                    self.assertFalse(observed['observations'][-1]['success'])
                else:
                    with self.assertRaisesRegex(JournalError, message):
                        native_references(self.f.snapshot(), 'author', source)

    def test_native_file_change_child_retains_role_parent_and_author_checks(self):
        source, home = self.native_source()
        self.indexed_session(home, ROOT_ID, self.f.source.sessions[ROOT_ID])
        source = CodexSessionSource()
        ref = {'thread_id': QA_ID, 'turn_id': QA_ID + '-turn', 'item_id': 'exec-native-patch'}
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Implemented result',
                  'authored_paths': [str(self.f.product)], 'evidence': [ref]}
        events = session(QA_ID, 'python-worker', report, cwd=self.f.root)
        events.insert(-2, file_change(QA_ID, self.f.product))
        self.indexed_session(home, QA_ID, events)
        source = CodexSessionSource()
        assignment = dict(self.f.known['author'], agent_id=QA_ID, start_turn_id=QA_ID + '-turn')
        accepted = accept_report(self.f.snapshot(), assignment, {'completion_turn_id': ref['turn_id']}, source)
        self.assertEqual(accepted['authored_paths'], [str(self.f.product)])
        with self.assertRaisesRegex(JournalError, 'agent_role'):
            native_item(source, ref, root_id=ROOT_ID, role='reviewer')
        with self.assertRaisesRegex(JournalError, 'parent_thread_id'):
            native_item(source, ref, root_id=REVIEWER_ID, role='python-worker')
        with self.assertRaisesRegex(JournalError, 'no native edit evidence'):
            accept_report(self.f.snapshot(), self.f.known['author'],
                          {'turn_id': ROOT_ID + '-turn', 'report': report}, source)

    def test_native_image_outputs_remain_unavailable_without_breaking_listing(self):
        source, home = self.native_source()
        events = copy.deepcopy(self.f.source.sessions[ROOT_ID])
        for kind in ('function', 'custom_tool'):
            for content in ('image', 'mixed', 'flagged-text', 'flagged-array'):
                call_id = kind + '-' + content
                output = [{'type': 'input_image', 'image_url': 'data:image/png;base64,secret-image-bytes'}]
                if content == 'mixed':
                    output.insert(0, {'type': 'input_text', 'text': '{"exit_code":0}'})
                if content == 'flagged-text':
                    output = '{"exit_code":0}'
                if content == 'flagged-array':
                    output = [{'type': 'input_text', 'text': '{"exit_code":0}'}]
                events.extend([
                    {'type': 'response_item', 'payload': {'type': kind + '_call', 'call_id': call_id, 'name': 'exec_command', 'arguments': '{}'}},
                    {'type': 'response_item', 'payload': {'type': kind + '_call_output', 'call_id': call_id, 'output': output,
                                                        'output_unavailable': content.startswith('flagged-')}},
                ])
        self.indexed_session(home, ROOT_ID, events)
        source = CodexSessionSource()
        observed = native_references(self.f.snapshot(), 'author', source)
        self.assertEqual(len(observed['observations']), 9)
        self.assertTrue(observed['observations'][0]['success'])
        self.assertNotIn('secret-image-bytes', json.dumps(observed))
        self.assertNotIn('secret-image-bytes', json.dumps(source.read(ROOT_ID, event_indices=range(1, len(events) + 1))))
        for item in observed['observations'][1:]:
            self.assertFalse(item['success'])
            self.assertEqual(item['authored_paths'], [])
            self.assertIn('unavailable', item['reason'])
            with self.assertRaisesRegex(JournalError, 'output unavailable'):
                native_tool(source, item['ref'], root_id=ROOT_ID)
        events.append(copy.deepcopy(events[-1]))
        self.indexed_session(home, ROOT_ID, events)
        source = CodexSessionSource()
        with self.assertRaisesRegex(JournalError, 'ambiguous'):
            native_references(self.f.snapshot(), 'author', source)

    def test_canonical_task_path_resolves_actual_uuid_and_role(self):
        source, home = self.native_source()
        events = session(QA_ID, 'qa-verifier', {'status': 'failed'}, cwd=self.f.root)
        meta = events[0]['payload']
        meta['agent_path'] = '/root/qa'
        meta['source']['subagent']['thread_spawn']['agent_path'] = '/root/qa'
        path = self.indexed_session(home, QA_ID, events, suffix='_' + REVIEWER_ID, agent_path='/root/qa')
        source = CodexSessionSource()
        self.assertEqual(source.resolve_session('/root/qa', ROOT_ID, 'qa-verifier')['codex_thread_id'], QA_ID)
        observed = native_references(self.f.snapshot(), 'qa', source)
        self.assertTrue(observed['completed'])
        self.assertEqual(observed['observations'][0]['ref'], self.f.ref(QA_ID))
        with self.assertRaisesRegex(JournalError, 'agent_role mismatch'):
            source.resolve_session('/root/qa', ROOT_ID, 'reviewer')
        with self.assertRaisesRegex(JournalError, 'source task path missing or ambiguous'):
            source.resolve_session('/root/qa', REVIEWER_ID, 'qa-verifier')
        meta['source']['subagent']['thread_spawn']['parent_thread_id'] = REVIEWER_ID
        path.write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
        source = CodexSessionSource()
        with self.assertRaisesRegex(JournalError, 'conflicting source parent_thread_id'):
            source.resolve_session('/root/qa', ROOT_ID, 'qa-verifier')

    def test_canonical_task_path_ignores_missing_rollout_of_another_root(self):
        source, home = self.native_source()
        for thread, parent in ((REVIEWER_ID, QA_ID), (QA_ID, ROOT_ID)):
            events = session(thread, 'qa-verifier', root=parent, cwd=self.f.root)
            meta = events[0]['payload']
            meta['agent_path'] = '/root/qa'
            meta['source']['subagent']['thread_spawn']['agent_path'] = '/root/qa'
            path = self.indexed_session(home, thread, events, agent_path='/root/qa')
            source = CodexSessionSource()
            if parent != ROOT_ID:
                path.unlink()
        self.assertEqual(source.resolve_session('/root/qa', ROOT_ID, 'qa-verifier')['codex_thread_id'], QA_ID)

    def test_index_selects_current_root_edits_over_old_filename(self):
        source, home = self.native_source()
        self.indexed_session(home, ROOT_ID, session(ROOT_ID, 'python-worker', cwd=self.f.root, turn='old'))
        source = CodexSessionSource()
        self.indexed_session(home, ROOT_ID, self.f.source.sessions[ROOT_ID], suffix='_' + QA_ID)
        source = CodexSessionSource()
        index_before = (home / 'state_5.sqlite').read_bytes()
        observed = native_references(self.f.snapshot(), 'author', source)
        self.assertEqual(observed['turn_id'], ROOT_ID + '-turn')
        self.assertEqual(observed['observations'], [{
            'ref': self.f.ref(ROOT_ID), 'name': 'apply_patch', 'success': True,
            'authored_paths': [str(self.f.product)],
        }])
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Implemented result',
                  'authored_paths': [str(self.f.product)], 'evidence': [self.f.ref(ROOT_ID)]}
        accepted = accept_report(self.f.snapshot(), self.f.known['author'],
                                 {'turn_id': observed['turn_id'], 'report': report}, source)
        self.assertEqual(accepted['authored_paths'], [str(self.f.product)])
        self.assertEqual((home / 'state_5.sqlite').read_bytes(), index_before)

    def test_index_reads_plain_filename_and_rejects_foreign_actions(self):
        source, home = self.native_source()
        self.indexed_session(home, ROOT_ID, self.f.source.sessions[ROOT_ID])
        source = CodexSessionSource()
        observed = native_references(self.f.snapshot(), 'author', source)
        self.assertEqual(observed['observations'][0]['ref'], self.f.ref(ROOT_ID))
        self.assertTrue(observed['observations'][0]['success'])
        child = session(QA_ID, 'python-worker', cwd=self.f.root, patch='*** Update File: result.py\n')
        self.indexed_session(home, QA_ID, child, suffix='_' + REVIEWER_ID)
        source = CodexSessionSource()
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Not my edit',
                  'authored_paths': [str(self.f.product)], 'evidence': [self.f.ref(QA_ID)]}
        with self.assertRaisesRegex(JournalError, 'authored path has no native edit evidence'):
            accept_report(self.f.snapshot(), self.f.known['author'],
                          {'turn_id': observed['turn_id'], 'report': report}, source)

    def test_index_failure_never_falls_back_to_old_file(self):
        source, home = self.native_source()
        old = self.indexed_session(home, ROOT_ID, self.f.source.sessions[ROOT_ID])
        source = CodexSessionSource()
        index = home / 'state_5.sqlite'
        with closing(sqlite3.connect(index)) as connection:
            connection.execute('DELETE FROM threads')
            connection.commit()
        with self.assertRaisesRegex(JournalError, 'execution unconfirmed:.*missing or ambiguous in Codex index'):
            source.read(ROOT_ID)
        self.indexed_session(home, ROOT_ID, self.f.source.sessions[ROOT_ID], suffix='_' + QA_ID).unlink()
        source = CodexSessionSource()
        with self.assertRaisesRegex(JournalError, 'execution unconfirmed: unreadable source session'):
            source.read(ROOT_ID)
        self.indexed_session(home, ROOT_ID, session(QA_ID, 'qa-verifier'), suffix='_' + QA_ID)
        source = CodexSessionSource()
        with self.assertRaisesRegex(JournalError, 'execution unconfirmed: missing or conflicting session metadata'):
            source.read(ROOT_ID)
        index.unlink()
        with self.assertRaisesRegex(JournalError, 'execution unconfirmed: cannot read Codex source index'):
            source.read(ROOT_ID)
        self.assertFalse(index.exists())
        self.assertTrue(old.is_file())

    def test_index_rejects_paths_outside_native_sessions(self):
        source, home = self.native_source()
        path = self.indexed_session(home, ROOT_ID, self.f.source.sessions[ROOT_ID])
        outside = home / 'export.jsonl'
        path.rename(outside)
        with closing(sqlite3.connect(home / 'state_5.sqlite')) as connection:
            connection.execute('UPDATE threads SET rollout_path = ?', (str(outside),))
            connection.commit()
        with self.assertRaisesRegex(JournalError, 'execution unconfirmed:.*outside native sessions'):
            source.read(ROOT_ID)
        path.symlink_to(outside)
        with closing(sqlite3.connect(home / 'state_5.sqlite')) as connection:
            connection.execute('UPDATE threads SET rollout_path = ?', (str(path),))
            connection.commit()
        with self.assertRaisesRegex(JournalError, 'execution unconfirmed:.*outside native sessions'):
            source.read(ROOT_ID)

    def test_index_rejects_ambiguous_canonical_task_path(self):
        source, home = self.native_source()
        for thread in (QA_ID, REVIEWER_ID):
            events = session(thread, 'qa-verifier', cwd=self.f.root)
            meta = events[0]['payload']
            meta['agent_path'] = '/root/qa'
            meta['source']['subagent']['thread_spawn']['agent_path'] = '/root/qa'
            self.indexed_session(home, thread, events, agent_path='/root/qa')
        with self.assertRaisesRegex(JournalError, 'execution unconfirmed: source task path missing or ambiguous'):
            source.resolve_session('/root/qa', ROOT_ID, 'qa-verifier')

    def test_child_author_reads_native_answer_not_request_body(self):
        assignment = {**self.f.known['author'], 'agent_id': QA_ID, 'start_turn_id': QA_ID + '-turn'}
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Native author', 'authored_paths': [str(self.f.product)], 'evidence': [self.f.ref(QA_ID)]}
        self.f.source.sessions[QA_ID] = session(QA_ID, 'python-worker', report, cwd=self.f.root, patch='*** Update File: result.py\n')
        result = accept_report(self.f.snapshot(), assignment, {'completion_turn_id': QA_ID + '-turn', 'report': {'summary': 'Forged'}}, self.f.source)
        self.assertEqual(result['summary'], 'Native author')

    def test_root_analysis_uses_current_native_inspection_without_author_paths(self):
        assignment = {**self.f.known['author'], 'kind': 'analysis', 'allowed_paths': []}
        self.f.source.sessions[ROOT_ID] = session(ROOT_ID, 'python-worker', cwd=self.f.root)
        body = {'status': 'completed', 'outcome': 'passed', 'summary': 'Analyzed current sources', 'evidence': [self.f.ref(ROOT_ID)]}
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
        self.f.reviewed()
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Incomplete', 'evidence': [self.f.ref(QA_ID)]}
        with self.assertRaisesRegex(JournalError, 'every acceptance criterion'):
            self.f.save('qa', report)
        self.f.source.sessions[QA_ID].append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'new'}})
        with self.assertRaisesRegex(JournalError, 'stale'):
            accept_report(self.f.snapshot(), self.f.known['qa'], {'completion_turn_id': QA_ID + '-turn'}, self.f.source)

    def test_new_qa_or_review_cannot_reuse_inspection_from_earlier_turn(self):
        self.f.completed()
        for key in ('review', 'qa'):
            with self.subTest(assignment=key):
                events = self.f.source.sessions[self.f.known[key]['agent_id']]
                body = json.loads(events[-1]['payload']['last_agent_message'])
                new_turn = key + '-new-turn'
                events.extend([
                    {'type': 'event_msg', 'timestamp': '2026-10-19T10:01:00+00:00', 'payload': {'type': 'task_started', 'turn_id': new_turn}},
                    {'type': 'turn_context', 'payload': {'turn_id': new_turn, 'model': events[2]['payload']['model']}},
                    {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': json.dumps(body)}]}},
                    {'type': 'event_msg', 'timestamp': '2026-10-19T10:01:01+00:00', 'payload': {'type': 'task_complete', 'turn_id': new_turn, 'last_agent_message': json.dumps(body)}},
                ])
                with self.assertRaisesRegex(JournalError, 'own successful native inspection'):
                    accept_report(self.f.snapshot(), self.f.known[key], {'completion_turn_id': new_turn}, self.f.source)

    def test_file_change_and_author_report_replacement_preserve_qa(self):
        self.f.completed()
        self.f.product.write_text('answer = 3\n')
        self.assertEqual(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa'), [])
        self.f.product.write_text('answer = 2\n')
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Clarified implementation', 'authored_paths': [str(self.f.product)], 'evidence': [self.f.ref(ROOT_ID)]}
        with self.assertRaisesRegex(JournalError, 'immutable'):
            self.f.save('author', report)
        self.assertEqual(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa'), [])

    def test_reviewer_report_replacement_preserves_qa_assignment_reference(self):
        self.f.completed()
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Clarified independent review',
                  'evidence': [self.f.ref(REVIEWER_ID)]}
        with self.assertRaisesRegex(JournalError, 'immutable'):
            self.f.save('review', report)
        self.assertIn('accepted report does not match its native response',
                      ';'.join(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa')))

    def test_legacy_journal_remains_readable_but_mutations_are_refused(self):
        from types import MappingProxyType
        from dataclasses import replace
        from journal_io import require_writable
        snapshot = self.f.snapshot()
        documents = dict(snapshot.documents)
        task = snapshot.json('task.json')
        task.pop('contract_version')
        documents['task.json'] = encode_json(task).encode()
        legacy = replace(snapshot, documents=MappingProxyType(documents))
        self.assertEqual(legacy.json('task.json')['summary'], 'Sample')
        with self.assertRaisesRegex(JournalError, 'read-only'):
            require_writable(legacy)

    def test_root_corrects_report_next_turn_using_completed_own_edits(self):
        self.f.source.sessions[ROOT_ID].append(file_change(ROOT_ID, self.f.product))
        ref = {'thread_id': ROOT_ID, 'turn_id': ROOT_ID + '-turn', 'item_id': 'exec-native-patch'}
        report = {'status': 'completed', 'outcome': 'passed', 'summary': {'result': 'Implemented result'},
                  'authored_paths': [str(self.f.product)], 'evidence': [ref]}
        before = self.f.snapshot()
        with self.assertRaisesRegex(JournalError, 'report summary required'):
            self.f.save('author', report)
        self.assertEqual(self.f.snapshot().revision, before.revision)
        self.f.source.sessions[ROOT_ID].append({'type': 'event_msg', 'payload': {
            'type': 'task_complete', 'turn_id': ROOT_ID + '-turn', 'last_agent_message': '{}'}})
        turn = 'root-report-correction'
        self.f.source.sessions[ROOT_ID].extend(session(ROOT_ID, 'python-worker', cwd=self.f.root, turn=turn)[1:3])
        report['summary'] = 'Implemented result'
        accepted = self.f.save('author', report, turn=turn)
        self.assertEqual(accepted['source']['turn_id'], turn)
        self.assertEqual(accepted['evidence'], [ref])
        observed = native_references(self.f.snapshot(), 'author', self.f.source)
        self.assertEqual(observed['turn_id'], turn)
        self.assertFalse(observed['completed'])
        self.assertIn(ref, [item['ref'] for item in observed['observations']])

    def test_author_rejects_unfinished_or_before_assignment_edit_turns(self):
        events = self.f.source.sessions[ROOT_ID]
        events.append(file_change(ROOT_ID, self.f.product))
        turn = 'new-author-turn'
        events.extend(session(ROOT_ID, 'python-worker', cwd=self.f.root, turn=turn)[1:3])
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Claimed old edits', 'authored_paths': [str(self.f.product)],
                  'evidence': [self.f.ref(ROOT_ID)]}
        with self.assertRaisesRegex(JournalError, 'previous author turn is unfinished'):
            self.f.save('author', report, turn=turn)
        events.insert(-2, {'type': 'event_msg', 'payload': {
            'type': 'task_complete', 'turn_id': ROOT_ID + '-turn', 'last_agent_message': '{}'}})
        assignment = {**self.f.known['author'], 'start_turn_id': turn}
        for ref in (self.f.ref(ROOT_ID), {'thread_id': ROOT_ID, 'turn_id': ROOT_ID + '-turn', 'item_id': 'exec-native-patch'}):
            with self.subTest(ref=ref):
                report['evidence'] = [ref]
                with self.assertRaisesRegex(JournalError, 'native edit author turn outside assignment'):
                    accept_report(self.f.snapshot(), assignment, {'turn_id': turn, 'report': report}, self.f.source)

    def test_requirements_are_read_from_document_without_content_tracking(self):
        initial = requirements(self.f.snapshot())
        self.f.plan.write_text(self.f.plan.read_text().replace('[ ]', '[x]').replace('Unrelated task', 'Changed unrelated task'))
        self.assertEqual(requirements(self.f.snapshot()), initial)
        self.f.plan.write_text(self.f.plan.read_text().replace('Result is correct', 'Result has tests'))
        self.assertEqual(requirements(self.f.snapshot())['criteria'], [{'id': 'ac-1', 'text': 'Result has tests'}])

    def test_native_qa_tamper_is_rejected(self):
        self.f.completed()
        events = self.f.source.sessions[QA_ID]
        answer = json.loads(events[-1]['payload']['last_agent_message'])
        answer['summary'] = 'Tampered native final'
        events[-1]['payload']['last_agent_message'] = json.dumps(answer)
        self.assertIn('disagree', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa')))

    def test_successful_native_output_and_root_continuation_need_no_checksum(self):
        self.f.completed()
        self.f.source.sessions[ROOT_ID].append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'root-next-turn'}})
        self.assertEqual(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa'), [])
        self.f.source.sessions[QA_ID][4]['payload']['output'] = '{"exit_code":0,"output":"Different inspection"}'
        self.assertEqual(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa'), [])

    def test_failed_native_check_cannot_prove_passed_criterion(self):
        self.f.completed()
        self.f.source.sessions[QA_ID][4]['payload']['output'] = '{"exit_code":1,"output":"PASS"}'
        self.assertIn('successful native inspection', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa')))

    def test_unverified_criterion_accepts_missing_empty_or_valid_evidence(self):
        self.f.reviewed()
        for evidence in ({}, {'evidence': []}, {'evidence': [self.f.ref(QA_ID)]}):
            with self.subTest(evidence=evidence):
                self.f = Fixture(self.f.root, name='variant-' + str(len(str(evidence))))
                self.f.reviewed()
                outcome = {'status': 'unverified', 'reason': 'Required runtime check is forbidden', **evidence}
                report = {'status': 'completed', 'outcome': 'failed', 'summary': 'Inspected available sources',
                          'reason': 'Required runtime check is forbidden', 'evidence': [self.f.ref(QA_ID)],
                          'criteria': {'ac-1': outcome}}
                accepted = self.f.save('qa', report)
                self.assertEqual(accepted['criteria']['ac-1'], outcome)
                self.assertEqual(accepted['outcome'], 'failed')
                self.assertIn('final QA did not pass', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa')))

    def test_criterion_errors_identify_the_criterion(self):
        self.f.reviewed()
        cases = [
            ({'status': 'unverified'}, 'reason required'),
            ({'status': 'unverified', 'reason': ' ', 'evidence': [self.f.ref(QA_ID)]}, 'reason required'),
            ({'status': 'unverified', 'reason': 'Forbidden', 'evidence': None}, 'must be an array'),
            ({'status': 'unverified', 'reason': 'Forbidden', 'evidence': {}}, 'must be an array'),
            ({'status': 'unverified', 'reason': 'Forbidden', 'evidence': 'none'}, 'must be an array'),
            ({'status': 'unverified', 'reason': 'Forbidden',
              'evidence': [{'path': 'artifacts/missing.txt'}]}, 'artifact evidence missing'),
            ({'status': 'passed'}, 'nonempty array'),
            ({'status': 'failed', 'evidence': []}, 'nonempty array'),
        ]
        for outcome, message in cases:
            with self.subTest(outcome=outcome):
                report = {'status': 'completed', 'outcome': 'failed', 'summary': 'Inspected sources', 'reason': 'Result not accepted',
                          'evidence': [self.f.ref(QA_ID)],
                          'criteria': {'ac-1': outcome}}
                with self.assertRaisesRegex(JournalError, 'criterion ac-1: .*' + message):
                    self.f.save('qa', report)
                self.assertFalse(self.f.snapshot().exists('reports/qa.json'))

    def test_narrative_attachment_alone_cannot_prove_criterion(self):
        self.f.reviewed()
        transact(self.f.run, 'attachment', {'artifact': 'narrative'},
                 lambda current: ({'artifacts/pass.txt': b'PASS'}, {}))
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Checked',
                  'evidence': [self.f.ref(QA_ID)],
                  'criteria': {'ac-1': {'status': 'passed', 'evidence': [{'path': 'artifacts/pass.txt'}]}}}
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
        self.assertIn('parent_thread_id mismatch', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa')))
        self.f.source.sessions[QA_ID] = copy.deepcopy(original)
        self.f.source.sessions[QA_ID][2]['payload']['model'] = 'unregistered-model'
        self.assertIn('source model', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa')))
        self.f.source.sessions[QA_ID] = original[:-1]
        self.assertIn('completed turn missing', ';'.join(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa')))

    def test_completion_before_start_is_rejected(self):
        self.f.completed()
        self.assertEqual(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa'), [])
        events = self.f.source.sessions[QA_ID]
        for started_at, completed_at in (
            ('2026-10-19T10:00:00+00:00', '2026-10-19T09:59:59+00:00'),
            (100, 99),
        ):
            with self.subTest(started_at=started_at, completed_at=completed_at):
                events[1]['payload']['started_at'] = started_at
                events[-1]['payload']['completed_at'] = completed_at
                self.assertIn('source completion is before its own task_started',
                              ';'.join(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa')))

    def test_author_path_permission(self):
        self.f.known['author']['allowed_paths'] = [str(self.f.root / 'other.py')]
        with self.assertRaisesRegex(JournalError, 'outside assignment'):
            self.f.save('author')

    def test_actual_check_reference_before_author_report(self):
        from journal_io import write_entry
        receipt = write_entry(self.f.run, 'check', {'id': 'actual', 'assignment_id': 'author',
                              'command': [sys.executable, '-B', '-c', 'print("checked")'], 'cwd': str(self.f.root)}, identifier='check')
        receipt = receipt['result']
        report = {'status': 'completed', 'outcome': 'passed', 'summary': 'Implemented result', 'authored_paths': [str(self.f.product)],
                  'evidence': [self.f.ref(ROOT_ID), {'path': receipt['path']}]}
        self.f.save('author', report)
        self.f.save('review')
        self.f.save('qa')
        self.assertEqual(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa'), [])

    def test_old_uncited_failed_check_does_not_block_fresh_result(self):
        from journal_io import write_entry
        write_entry(self.f.run, 'check', {'id': 'old', 'assignment_id': 'author',
                    'command': [sys.executable, '-B', '-c', 'raise SystemExit(1)'], 'cwd': str(self.f.root)}, identifier='old-check')
        self.f.product.write_text('answer = 3\n')
        self.f.completed()
        self.assertEqual(validate_acceptance(self.f.snapshot(), self.f.source, report_id='qa'), [])


if __name__ == '__main__':
    unittest.main()
