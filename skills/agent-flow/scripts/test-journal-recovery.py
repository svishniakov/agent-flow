#!/usr/bin/env python3
"""Recovery controls use disposable SQLite journals, never retained candidates."""
import json
import importlib.util
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TEMP_ROOT = "/private/tmp" if sys.platform == "darwin" else "/tmp"

from journal_io import (JournalSnapshot, JournalError, initialize_journal, encode_json,
                        digest, transact, export_snapshot, append_event, now_iso,
                        UNSUPPORTED_STORAGE_VERSION)
from journal_recovery import diagnose_recovery, recover

SCRIPTS = Path(__file__).resolve().parent


# Reuse the existing synthetic source/workspace fixtures without new dependencies.
spec = importlib.util.spec_from_file_location("registrar_journal_cases", SCRIPTS / "test-journal-io.py")
journal_cases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(journal_cases)


class ProvenCompletionTests(unittest.TestCase):
    def setUp(self):
        journal_cases.JournalRegressions.setUp(self)
        journal_cases.base.workspace_pack(self)
        journal_cases.evidence.ensure_result_contract(self.run, self.source)
        self.original_records = json.loads(json.dumps(self.summary['subagents']))

    open_timeline = journal_cases.JournalRegressions.open_timeline
    record = journal_cases.JournalRegressions.record
    repeat_args = journal_cases.JournalRegressions.repeat_args

    def fixture(self, role='qa-verifier', defect=None):
        snapshot = JournalSnapshot.open(self.run)
        summary = json.loads(snapshot.read_text('delegation-summary.json'))
        record = next(r for r in summary['subagents'] if r['role'] == role)
        for key in ('handoff', 'completion_turn_id', 'handoff_sha256', 'evidence', 'reviewed_result_hash'):
            record.pop(key, None)
        record.update(status='pass', obligation={'id': 'acceptance', 'required': True, 'state': 'current'},
                      agent_path='/root/verifier', observed_spawn_at='2026-09-10T09:00:00Z',
                      session_meta_event=1, task_started_event=2)
        if role == 'qa-verifier':
            summary['verification']['qa'] = summary['verification']['reviewer'] = None
        else:
            summary['verification']['reviewer'] = None
        documents = {}
        for path in ('timeline.jsonl', record['trace']):
            events = [json.loads(line) for line in snapshot.read_text(path).splitlines() if line.strip()]
            events = [e for e in events if e.get('lane_id') != record['lane_id'] or e['stage'] == 'spawned']
            for event in events:
                if event.get('lane_id') == record['lane_id']:
                    event['status'] = 'pass'
            documents[path] = ''.join(json.dumps(e) + '\n' for e in events).encode()
        if defect == 'legacy':
            record['legacy'] = {'generation': 1}
        elif defect == 'resolved':
            record['obligation']['state'] = 'resolved'
        elif defect == 'finished':
            record['completion_turn_id'] = 'previous-turn'
        elif defect == 'history':
            event = {**journal_cases.base.trace_event(self.original_records[0 if role == 'qa-verifier' else 1], 'handoff'), 'status': 'done'}
            documents[record['trace']] += (json.dumps(event) + '\n').encode()
        elif defect == 'identity':
            events = [json.loads(line) for line in documents[record['trace']].splitlines()]
            events[0]['codex_thread_id'] = journal_cases.base.ROOT_ID
            documents[record['trace']] = ''.join(json.dumps(e) + '\n' for e in events).encode()
        elif defect == 'closed':
            documents['timeline.jsonl'] += (json.dumps({'stage': 'final'}) + '\n').encode()
        documents['delegation-summary.json'] = json.dumps(summary).encode()
        # This is construction of the pre-fix defect, not a production writer bypass.
        with sqlite3.connect(self.run / '.journal/state.sqlite3') as db:
            if defect == 'old-generation':
                archived = journal_cases.journal_io.replace(snapshot, documents={**snapshot.documents, **documents})
                archive_id, content = journal_cases.journal_io.capture_archive(archived)
                db.execute(journal_cases.journal_io.ARCHIVE_SCHEMA)
                db.execute('INSERT INTO archives VALUES (?, ?, ?)', (archive_id, content, digest(content)))
                db.execute('UPDATE run_state SET revision=revision+1')
                event = {'stage': 'reopen', 'archive_id': archive_id, 'archive_sha256': digest(content),
                         'generation': archived.generation + 1}
                documents['timeline.jsonl'] += (json.dumps(event) + '\n').encode()
            journal_cases.journal_io._put_documents(db, documents)
        return record

    def state(self):
        s = JournalSnapshot.open(self.run)
        return s.revision, dict(s.documents), dict(s.receipts)

    def complete(self, role='qa-verifier', *extra):
        return self.record(*self.repeat_args(role), '--operation-id', 'proven-completion', *extra)

    def test_proven_completion_preserves_history_source_and_replays(self):
        for role in ('qa-verifier', 'reviewer'):
            if role == 'reviewer':
                self.setUp()
            old = self.fixture(role)
            before = JournalSnapshot.open(self.run)
            sources = json.dumps(self.source.sessions)
            resolved = {k: old[k] for k in ('codex_thread_id', 'agent_path', 'observed_spawn_at', 'session_meta_event', 'task_started_event')}
            resolved['root_thread_id'] = journal_cases.base.ROOT_ID
            with patch.object(self.source, 'resolve_session', return_value=resolved, create=True):
                self.complete(role, '--resolve-session', '--agent-path', '/root/verifier')
                after = JournalSnapshot.open(self.run)
                self.assertIn('unchanged', self.complete(role, '--resolve-session', '--agent-path', '/root/verifier'))
            self.assertEqual(self.state(), (after.revision, dict(after.documents), dict(after.receipts)))
            self.assertEqual(json.dumps(self.source.sessions), sources)
            stored = json.loads(after.read_text('delegation-summary.json'))
            updated = next(r for r in stored['subagents'] if r['lane_id'] == old['lane_id'])
            for key, value in old.items():
                self.assertEqual(updated[key], value)
            for path in ('timeline.jsonl', old['trace']):
                self.assertTrue(after.read_bytes(path).startswith(before.read_bytes(path)))
                self.assertEqual(len(after.read_bytes(path).splitlines()), len(before.read_bytes(path).splitlines()) + 1)
            for path, value in before.documents.items():
                if path not in ('timeline.jsonl', old['trace'], 'delegation-summary.json', 'artifacts.json'):
                    self.assertEqual(after.documents[path], value)
            for operation, receipt in before.receipts.items():
                self.assertEqual(after.receipts[operation], receipt)
            if role == 'qa-verifier':
                before_conflict = self.state()
                with self.assertRaisesRegex((SystemExit, ValueError), 'payload conflict'):
                    self.complete('reviewer')
                self.assertEqual(before_conflict, self.state())

    def test_proven_completion_rejects_ineligible_assignments_atomically(self):
        for defect in ('legacy', 'resolved', 'finished', 'history', 'identity', 'closed', 'old-generation'):
            with self.subTest(defect=defect):
                if defect != 'legacy':
                    self.setUp()
                self.fixture(defect=defect)
                before = self.state()
                with self.assertRaises((SystemExit, ValueError)):
                    self.complete()
                self.assertEqual(self.state(), before)

    def test_proven_completion_rejects_wrong_source_hash_and_result(self):
        for defect in ('unfinished', 'foreign-parent', 'wrong-role', 'old-turn', 'result', 'handoff'):
            with self.subTest(defect=defect):
                if defect != 'unfinished':
                    self.setUp()
                self.fixture()
                events = self.source.sessions[journal_cases.base.QA_ID]
                if defect == 'unfinished':
                    events.pop()
                elif defect == 'foreign-parent':
                    events[0]['payload']['source']['subagent']['thread_spawn']['parent_thread_id'] = journal_cases.base.REVIEWER_ID
                elif defect == 'wrong-role':
                    events[0]['payload']['source']['subagent']['thread_spawn']['agent_role'] = 'reviewer'
                elif defect == 'old-turn':
                    events.append({'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'new'}})
                else:
                    for event in events:
                        payload = event['payload']
                        if payload.get('type') == 'task_complete':
                            answer = json.loads(payload['last_agent_message'])
                            answer['reviewed_result_hash' if defect == 'result' else 'handoff_sha256'] = '0' * 64
                            payload['last_agent_message'] = json.dumps(answer)
                            events[-2]['payload']['content'][0]['text'] = json.dumps(answer)
                before = self.state()
                with self.assertRaises((SystemExit, ValueError)):
                    self.complete()
                self.assertEqual(before, self.state())

    def test_publish_cannot_complete_or_edit_terminal_assignment(self):
        old = self.fixture()
        before = self.state()
        summary = json.loads(JournalSnapshot.open(self.run).read_text('delegation-summary.json'))
        summary['subagents'][0]['handoff'] = self.original_records[0]['handoff']
        with self.assertRaisesRegex(ValueError, 'terminal assignment'):
            transact(self.run, 'publish-bypass', {}, lambda current: ({'delegation-summary.json': json.dumps(summary)}, {}))
        self.assertEqual(before, self.state())
        capture = self.root / 'publish-capture.json'
        capture.write_text(json.dumps(summary))
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'journal.py'), '--run-dir', str(self.run),
                                 'publish', '--file', 'delegation-summary.json', str(capture),
                                 '--operation-id', 'public-publish-bypass'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('writer-owned', result.stderr)
        self.assertEqual(before, self.state())
        self.complete()
        before = self.state()
        with self.assertRaisesRegex(ValueError, 'terminal handoff'):
            transact(self.run, 'edit-handoff', {}, lambda current: ({self.original_records[0]['handoff']: b'replaced'}, {}))
        self.assertEqual(before, self.state())

    def test_proven_completion_rechecks_current_snapshot_and_rolls_back(self):
        self.fixture()
        import journal_io
        original = journal_io._transact_completion
        raced = []
        def race(*args, **kwargs):
            transact(self.run, 'concurrent-resolution', {}, lambda current: self.resolve_document(current))
            raced.append(self.state())
            return original(*args, **kwargs)
        with patch.object(journal_io, '_transact_completion', side_effect=race), self.assertRaisesRegex((SystemExit, ValueError), 'current successful'):
            self.complete()
        self.assertEqual(raced[0], self.state())
        self.setUp()
        self.fixture()
        before = self.state()
        with patch.object(journal_cases.journal_io, '_put_documents', side_effect=OSError('injected write failure')), self.assertRaisesRegex((SystemExit, OSError), 'injected write failure'):
            self.complete()
        self.assertEqual(before, self.state())

    def test_later_completion_turn_uses_captured_source_and_keeps_spawn(self):
        old = self.fixture()
        thread = old['codex_thread_id']
        events = self.source.sessions[thread]
        events[1:1] = [
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'original-spawn-turn'}},
            {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'original-spawn-turn', 'last_agent_message': 'preparation'}},
        ]
        import journal_io
        original = journal_io._transact_completion
        def captured_only(*args, **kwargs):
            with patch.object(self.source, 'read', side_effect=AssertionError('live source under SQL')):
                return original(*args, **kwargs)
        with patch.object(journal_io, '_transact_completion', side_effect=captured_only):
            self.complete()
        updated = json.loads(JournalSnapshot.open(self.run).read_text('delegation-summary.json'))['subagents'][0]
        self.assertEqual(updated['task_started_event'], 4)
        for key in ('agent_path', 'observed_spawn_at', 'status', 'obligation', 'codex_thread_id', 'role'):
            self.assertEqual(updated[key], old[key])

    def test_reviewer_repair_requires_accepted_qa_hash_and_order(self):
        for defect in ('qa-hash', 'order'):
            with self.subTest(defect=defect):
                if defect == 'order':
                    self.setUp()
                self.fixture('reviewer')
                events = self.source.sessions[journal_cases.base.REVIEWER_ID]
                if defect == 'qa-hash':
                    answer = json.loads(events[-1]['payload']['last_agent_message'])
                    answer['qa_handoff_sha256'] = '0' * 64
                    events[-1]['payload']['last_agent_message'] = json.dumps(answer)
                    events[-2]['payload']['content'][0]['text'] = json.dumps(answer)
                else:
                    events[-1]['timestamp'] = '2026-09-10T10:00:05+00:00'
                    events[1]['timestamp'] = '2026-09-10T10:00:01+00:00'
                before = self.state()
                with self.assertRaisesRegex((SystemExit, ValueError), 'qa_handoff_sha256|follow QA'):
                    self.complete('reviewer')
                self.assertEqual(before, self.state())

    def test_proven_completion_lost_response_reuses_receipt_and_proof(self):
        self.fixture()
        import journal_io
        original = journal_io._transact_completion
        def lost_response(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('lost completion response')
        with patch.object(journal_io, '_transact_completion', side_effect=lost_response), self.assertRaisesRegex(SystemExit, 'lost completion response'):
            self.complete()
        before = self.state()
        self.assertIn('unchanged', self.complete())
        self.assertEqual(before, self.state())
        self.source.sessions[journal_cases.base.QA_ID].append(
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'new-turn'}})
        with self.assertRaisesRegex(SystemExit, 'unfinished'):
            self.complete()
        self.assertEqual(before, self.state())

    def resolve_document(self, current):
        summary = json.loads(current.read_text('delegation-summary.json'))
        summary['subagents'][0]['obligation']['state'] = 'resolved'
        return {'delegation-summary.json': json.dumps(summary)}, {}


class RecoveryTests(unittest.TestCase):
    def append_assignment_event(self, event, *, identifier):
        def mutation(current):
            encoded = (encode_json(event) + "\n").encode()
            summary = json.loads(current.read_text("delegation-summary.json"))
            records = [record for group in ("subagents", "role_lanes") for record in summary.get(group, [])
                       if record.get("lane_id") == event["lane_id"]]
            documents = {"timeline.jsonl": current.read_bytes("timeline.jsonl") + encoded}
            for record in records:
                trace = record.get("trace")
                if trace and trace != "timeline.jsonl":
                    documents[trace] = current.read_bytes(trace) + encoded
                if "status" in record:
                    record["status"] = event["status"]
            documents["delegation-summary.json"] = encode_json(summary)
            return documents, {}
        return transact(self.run, identifier, {}, mutation)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / 'run'
        self.event = dict(timestamp='2026-01-01T00:00:00Z', stage='final', role='orchestrator',
                          stable_agent_name='Orchestrator', stable_agent_slug='orchestrator', status='blocked',
                          summary='Synthetic blocked acceptance', artifacts=['final.md'], next_step='')

    def fixture(self, *, status='blocked', closed=True):
        self.event['status'] = status
        summary = {'version': 1, 'subagents': [], 'role_lanes': [], 'subagents_used': False,
                   'role_lanes_used': False, 'notes': 'Synthetic analysis',
                   'verification': {'task_kind': 'analysis', 'root_thread_id': 'root', 'qa': 'old-qa',
                                    'reviewer': 'old-review', 'behavioral_checks': [], 'result_files': []}}
        documents = {'final.md': b'# Result\nVerdict: blocked\n', 'empty': None,
                     'timeline.jsonl': b'',
                     'delegation-summary.json': encode_json(summary).encode()}
        initialize_journal(self.run, documents, source_root=self.root)
        if closed:
            # Fixture-only close; production writers close generations through journal finalize.
            raw = (encode_json(self.event) + '\n').encode()
            with sqlite3.connect(self.run / '.journal/state.sqlite3') as db:
                journal_cases.journal_io._put_documents(db, {'timeline.jsonl': raw})
        return JournalSnapshot.open(self.run)

    def args(self):
        diagnosis = diagnose_recovery(self.run)
        snapshot = JournalSnapshot.open(self.run)
        return dict(expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                    expected_generation=snapshot.generation, identifier='recover', reason='Repair acceptance',
                    identity=diagnosis['identity'], final_index=diagnosis['final']['index'] if diagnosis['final'] else None,
                    final_sha256=diagnosis['final']['sha256'] if diagnosis['final'] else None)

    def raw_state(self):
        s = JournalSnapshot.open(self.run)
        return s.revision, s.storage_version, dict(s.documents), dict(s.receipts), dict(s.archives)


    def test_flat_domain_and_cli_refusal_preserves_complete_tree(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            root = Path(temporary).resolve()
            self.assertEqual(root, Path(temporary))
            def tree(run):
                return {p.relative_to(run).as_posix():
                        ('link', str(p.readlink())) if p.is_symlink() else
                        ('directory', None) if p.is_dir() else ('file', p.read_bytes())
                        for p in run.rglob('*')}
            run = root / 'reopen'
            run.mkdir()
            (run / 'run.md').write_text('# Flat archival run\n')
            (run / 'empty').mkdir()
            (run / 'binary').write_bytes(b'\x00\xff')
            (run / 'timeline.jsonl').write_text(encode_json(self.event) + '\n')
            (run / 'final.md').write_text('Verdict: blocked\n')
            snapshot = JournalSnapshot.open(run)
            self.assertFalse(snapshot.durable)
            before = tree(run)
            identity = root / 'identity.json'
            identity.write_text('{}')
            with self.assertRaises(JournalError) as failure:
                recover(run, expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                        expected_generation=snapshot.generation, identifier='flat-reopen',
                        reason='Repair acceptance', identity={}, final_index=1,
                        final_sha256=digest(b'Verdict: blocked\n'))
            self.assertIn(UNSUPPORTED_STORAGE_VERSION, str(failure.exception))
            self.assertEqual(tree(run), before)
            self.assertFalse((run / '.journal').exists())
            command = [sys.executable, '-B', str(SCRIPTS / 'journal.py'), '--run-dir', str(run), 'reopen',
                       '--expected-run-uuid', snapshot.run_uuid, '--expected-revision', str(snapshot.revision),
                       '--expected-generation', str(snapshot.generation), '--operation-id', 'flat-reopen',
                       '--reason', 'Repair acceptance', '--identity-file', str(identity),
                       '--final-index', '1', '--final-sha256', digest(b'Verdict: blocked\n')]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('unsupported journal storage version', result.stderr)
            self.assertEqual(tree(run), before)
            self.assertFalse((run / '.journal').exists())
            for removed in ('upgrade', 'import-legacy', 'classify-legacy'):
                with self.subTest(removed=removed):
                    result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'journal.py'),
                                             '--run-dir', str(run), removed], capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(tree(run), before)
                    self.assertFalse((run / '.journal').exists())

    def test_reopen_archives_generation_and_exports(self):
        old = self.fixture()
        result = recover(self.run, **self.args())
        new = JournalSnapshot.open(self.run)
        self.assertEqual((new.storage_version, new.generation, new.closed), (3, 2, False))
        archived = new.archive(result['archive_id'])
        self.assertEqual(dict(archived.documents), dict(old.documents))
        self.assertEqual(dict(archived.receipts), dict(old.receipts))
        self.assertEqual(archived.revision, old.revision)
        self.assertEqual(archived.package_version, new.package_version)
        self.assertTrue(new.read_bytes('timeline.jsonl').startswith(old.read_bytes('timeline.jsonl')))
        self.assertIsNone(json.loads(new.read_bytes('delegation-summary.json'))['verification']['qa'])
        view = export_snapshot(archived)
        shutil.rmtree(view)
        self.assertEqual((export_snapshot(new.archive(result['archive_id'])) / 'final.md').read_bytes(), old.read_bytes('final.md'))
        event = {**self.event, 'stage': 'checks', 'status': 'pass', 'timestamp': '2026-12-01T00:00:00Z'}
        append_event(self.run / 'timeline.jsonl', event, identifier='new-check')
        self.assertFalse(JournalSnapshot.open(self.run).closed)

    def test_reopen_requires_closed_generation(self):
        self.fixture(closed=False)
        with self.assertRaisesRegex(JournalError, 'reopen requires closed generation'):
            recover(self.run, **self.args())

    def test_reopen_persists_request_before_sql_and_retries(self):
        import journal_recovery
        self.fixture(closed=True)
        args = self.args()
        initial = JournalSnapshot.open(self.run)
        captured = {}
        with self.assertRaisesRegex(JournalError, 'invalid operation ID'):
            recover(self.run, **{**args, 'identifier': '../invalid'})
        original = journal_recovery._transact
        def lost_response(run_dir, identifier, payload, mutation, **kwargs):
            request = Path(run_dir) / '.journal/requests' / identifier
            self.assertEqual(json.loads(request.read_bytes()), {'run_uuid': initial.run_uuid,
                'operation_id': identifier, 'command': 'reopen', 'payload': payload})
            self.assertIsNone(JournalSnapshot.open(run_dir).operation_receipt(identifier))
            captured['request'] = request.read_bytes()
            if not captured.get('precommit_seen'):
                captured['precommit_seen'] = True
                raise OSError('synthetic before SQL')
            captured['result'] = original(run_dir, identifier, payload, mutation, **kwargs)
            raise OSError('synthetic lost response')
        with patch.object(journal_recovery, '_transact', side_effect=lost_response), self.assertRaisesRegex(OSError, 'before SQL'):
            recover(self.run, **args)
        self.assertEqual(JournalSnapshot.open(self.run).revision, initial.revision)
        self.assertIsNone(JournalSnapshot.open(self.run).operation_receipt(args['identifier']))
        with patch.object(journal_recovery, '_transact', side_effect=lost_response), self.assertRaisesRegex(OSError, 'lost response'):
            recover(self.run, **args)
        committed = self.raw_state()
        self.assertEqual(recover(self.run, **args), captured['result'])
        identity = self.root / 'identity.json'
        identity.write_text(json.dumps(args['identity']))
        command = [sys.executable, '-B', str(SCRIPTS / 'journal.py'), '--run-dir', str(self.run),
            'reopen', '--expected-run-uuid', initial.run_uuid,
            '--expected-revision', str(initial.revision), '--expected-generation', '1',
            '--operation-id', args['identifier'], '--reason', args['reason'], '--identity-file', str(identity),
            '--final-index', str(args['final_index']), '--final-sha256', args['final_sha256']]
        cli = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual({k: v for k, v in json.loads(cli.stdout).items() if k != 'operation_id'}, captured['result'])
        self.assertEqual(committed, self.raw_state())
        self.assertEqual((self.run / '.journal/requests' / args['identifier']).read_bytes(), captured['request'])
        with self.assertRaisesRegex(JournalError, 'payload conflict'):
            recover(self.run, **{**args, 'reason': 'different request'})
        self.assertEqual(committed, self.raw_state())

    def test_exact_replay_conflict_and_stale_preconditions(self):
        self.fixture()
        args = self.args()
        result = recover(self.run, **args)
        before = self.raw_state()
        self.assertEqual(recover(self.run, **args), result)
        self.assertEqual(before, self.raw_state())
        with self.assertRaisesRegex(JournalError, 'payload conflict'):
            recover(self.run, **{**args, 'reason':'other'})
        with self.assertRaises(JournalError):
            recover(self.run, **{**args, 'identifier':'again'})

    def test_preconditions_and_final_identity_negatives_are_atomic(self):
        self.fixture()
        args = self.args()
        before = self.raw_state()
        for key,value in [('expected_revision', 90), ('expected_generation', 90), ('expected_run_uuid','wrong'),
                          ('final_index',90), ('final_sha256','0'*64), ('identity',{})]:
            with self.subTest(key=key), self.assertRaises(JournalError):
                recover(self.run, **{**args,key:value, "identifier":"precondition-"+key})
            self.assertTrue((self.run/'.journal/requests'/('precondition-'+key)).is_file())
            self.assertIsNone(JournalSnapshot.open(self.run).operation_receipt('precondition-'+key))
            self.assertEqual(before, self.raw_state())

    def test_positive_final_cannot_reopen(self):
        self.fixture(status='pass')
        with self.assertRaisesRegex(JournalError, 'positive final cannot reopen'):
            recover(self.run, **self.args())

    def test_positive_verdict_text_cannot_reopen(self):
        self.fixture()
        raw = b'# Result\nVerdict: ship\n'
        with sqlite3.connect(self.run / '.journal/state.sqlite3') as db:
            db.execute("UPDATE documents SET content=?, sha256=? WHERE path='final.md'", (raw, digest(raw)))
        with self.assertRaisesRegex(JournalError, 'negative current final'):
            recover(self.run, **self.args())

    def test_arbitrary_receipt_id_blocks_delivery_without_file(self):
        self.fixture()
        result = {'candidate_root':'/tmp/candidate','result_hash':'a'*64,'workspace_id':'w','candidate_id':'c','description':'/tmp/delivery.md'}
        with sqlite3.connect(self.run / '.journal/state.sqlite3') as db:
            db.execute('INSERT INTO operations VALUES (?,?,?,?)', ('random-uuid','f'*64,1,encode_json(result)))
        before = self.raw_state()
        with self.assertRaisesRegex(JournalError, 'structured delivery'):
            recover(self.run, **self.args())
        self.assertEqual(before,self.raw_state())

    def test_delivery_artifact_without_receipt_rejected(self):
        self.fixture()
        with sqlite3.connect(self.run / '.journal/state.sqlite3') as db:
            raw=b'{}'
            for p in ('artifacts','artifacts/workspaces','artifacts/workspaces/w','artifacts/workspaces/w/delivery'):
                db.execute('INSERT INTO documents VALUES (?,"directory",NULL,NULL)',(p,))
            db.execute('INSERT INTO documents VALUES (?,"file",?,?)',('artifacts/workspaces/w/delivery/c.json',raw,digest(raw)))
        with self.assertRaisesRegex(JournalError,'artifact without'):
            recover(self.run, **self.args())

    def test_archive_corruption_refuses_read(self):
        self.fixture()
        recover(self.run, **self.args())
        with sqlite3.connect(self.run / '.journal/state.sqlite3') as db:
            db.execute("UPDATE archives SET content=X'00'")
        with self.assertRaisesRegex(JournalError, 'corrupt journal archive'):
            JournalSnapshot.open(self.run)

    def test_exception_before_commit_and_lost_response_replay(self):
        self.fixture()
        args=self.args()
        before=self.raw_state()
        def crash(point):
            if point=='before-commit':
                raise RuntimeError('simulated interruption')
        with self.assertRaises(RuntimeError):
            recover(self.run, **args, barrier=crash)
        self.assertEqual(before,self.raw_state())
        def lost(point):
            if point=='after-commit':
                raise RuntimeError('lost response')
        with self.assertRaises(RuntimeError):
            recover(self.run, **args, barrier=lost)
        after=self.raw_state()
        recover(self.run, **args)
        self.assertEqual(after,self.raw_state())

    def test_recovered_identity_cannot_be_changed_by_writer(self):
        self.fixture()
        recover(self.run, **self.args())
        before=self.raw_state()
        summary=json.loads(JournalSnapshot.open(self.run).read_text('delegation-summary.json'))
        summary['verification']['root_thread_id']='different-root'
        with self.assertRaisesRegex(JournalError,'identity is immutable'):
            transact(self.run,'different-root',{},lambda s:({'delegation-summary.json':encode_json(summary)},{}))
        self.assertEqual(before,self.raw_state())

    def plain_obligation_fixture(self, *, wrong_duty=False):
        """Open journal with a failed duty and a verified same-duty replacement; no archives involved."""
        records = [{'lane_id': 'old-duty', 'role': 'python-worker', 'status': 'fail',
                    'trace': 'agents/python-worker/trace.jsonl', 'handoff': 'handoffs/old.md',
                    'reason': 'Original failed attempt',
                    'obligation': {'id': 'duty', 'required': True, 'state': 'current'}},
                   {'lane_id': 'replacement', 'role': 'python-worker', 'status': 'pass',
                    'trace': 'agents/replacement/trace.jsonl', 'handoff': 'replacement.md',
                    'reason': 'Verified same duty completion',
                    'obligation': {'id': 'duty', 'required': True, 'state': 'current'}}]
        lanes = [{'id': 'old-duty', 'status': 'fail', 'role': 'python-worker', 'execution_mode': 'role-lane'},
                 {'id': 'replacement', 'status': 'pass', 'role': 'python-worker', 'execution_mode': 'role-lane'}]
        if wrong_duty:
            records.append({'lane_id': 'wrong', 'role': 'python-worker', 'status': 'pass',
                            'trace': 'agents/wrong/trace.jsonl', 'handoff': 'wrong.md',
                            'reason': 'Different duty completion',
                            'obligation': {'id': 'other-duty', 'required': True, 'state': 'current'}})
            lanes.append({'id': 'wrong', 'status': 'pass', 'role': 'python-worker', 'execution_mode': 'role-lane'})
        summary = {'version': 1, 'subagents': [], 'role_lanes': records, 'subagents_used': False,
                   'role_lanes_used': True, 'notes': 'Synthetic obligation fixture',
                   'verification': {'task_kind': 'analysis', 'root_thread_id': 'root', 'qa': None,
                                    'reviewer': None, 'behavioral_checks': [], 'result_files': []}}
        def terminal(record, stage, status):
            return {'timestamp': '2026-01-01T00:00:00Z', 'stage': stage, 'role': record['role'],
                    'stable_agent_name': 'Python Worker', 'stable_agent_slug': 'python-worker',
                    'status': status, 'summary': 'Synthetic duty outcome', 'artifacts': [record['handoff']],
                    'next_step': '', 'execution_mode': 'role-lane', 'lane_id': record['lane_id']}
        events = [terminal(records[0], 'fail', 'fail'), terminal(records[1], 'handoff', 'pass')]
        documents = {'delegation-summary.json': encode_json(summary).encode(),
                     'lane-map.json': encode_json({'schema_version': 2, 'lanes': lanes}).encode(),
                     'timeline.jsonl': ''.join(encode_json(event) + '\n' for event in events).encode(),
                     'agents': None, 'agents/python-worker': None, 'agents/replacement': None,
                     'handoffs': None,
                     'agents/python-worker/trace.jsonl': (encode_json(events[0]) + '\n').encode(),
                     'agents/replacement/trace.jsonl': (encode_json(events[1]) + '\n').encode(),
                     'handoffs/old.md': b'Verdict: fail\nOriginal attempt failed.\n',
                     'replacement.md': b'Verdict: pass\nSame duty verified.\n'}
        if wrong_duty:
            event = terminal(records[2], 'handoff', 'pass')
            documents['timeline.jsonl'] += (encode_json(event) + '\n').encode()
            documents['agents/wrong'] = None
            documents['agents/wrong/trace.jsonl'] = (encode_json(event) + '\n').encode()
            documents['wrong.md'] = b'Verdict: pass\nOther duty verified.\n'
        initialize_journal(self.run, documents, source_root=self.root)
        return dict(lane_id='old-duty', replacement='replacement', reason='Same duty verified',
                    expected_revision=JournalSnapshot.open(self.run).revision, identifier='resolve')

    def test_resolve_obligation_domain_cli_and_retry(self):
        from journal_lifecycle import resolve_obligation
        from verification_evidence import evaluate_obligations
        import journal_io
        for route in ('domain', 'cli'):
            with self.subTest(route=route):
                self.run = self.root / f'run-{route}'
                args = self.plain_obligation_fixture()
                before = JournalSnapshot.open(self.run)
                old = next(record for record in json.loads(before.read_text('delegation-summary.json'))['role_lanes']
                           if record['lane_id'] == 'old-duty')
                argv = [sys.executable, '-B', str(SCRIPTS / 'journal.py'), '--run-dir', str(self.run),
                        'resolve-obligation', '--lane-id', args['lane_id'], '--replacement', args['replacement'],
                        '--reason', args['reason'], '--expected-revision', str(args['expected_revision']),
                        '--operation-id', args['identifier']]
                if route == 'domain':
                    original = journal_io.transact
                    def lose_response(*a, **kw):
                        original(*a, **kw)
                        raise OSError('synthetic lost response')
                    with patch.object(journal_io, 'transact', side_effect=lose_response):
                        with self.assertRaisesRegex(OSError, 'lost response'):
                            resolve_obligation(self.run, **args)
                    resolve_obligation(self.run, **args)
                else:
                    result = subprocess.run(argv, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                committed = self.raw_state()
                result = subprocess.run(argv, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.raw_state(), committed)
                after = JournalSnapshot.open(self.run)
                self.assertEqual(after.revision, before.revision + 1)
                self.assertEqual(dict(after.archives), dict(before.archives))
                summary = json.loads(after.read_text('delegation-summary.json'))
                resolved = next(record for record in summary['role_lanes'] if record['lane_id'] == 'old-duty')
                self.assertEqual({k: v for k, v in old.items() if k != 'obligation'},
                                 {k: v for k, v in resolved.items() if k != 'obligation'})
                self.assertEqual(resolved['obligation']['id'], old['obligation']['id'])
                self.assertTrue(resolved['obligation']['required'])
                self.assertEqual(resolved['obligation']['state'], 'resolved')
                self.assertEqual(evaluate_obligations(after, summary)[1], [])
                for path, raw in before.documents.items():
                    if path not in {'delegation-summary.json', 'lane-map.json'}:
                        self.assertEqual(after.documents[path], raw, path)
                rows = json.loads(after.read_text('lane-map.json'))['lanes']
                old_row = next(row for row in rows if row['id'] == 'old-duty')
                self.assertEqual(old_row['status'], 'replaced')
                self.assertEqual(old_row['replacement'], 'replacement')
                with self.assertRaisesRegex(JournalError, 'payload conflict'):
                    resolve_obligation(self.run, **{**args, 'reason': 'Different request'})
                self.assertEqual(self.raw_state(), committed)

    def test_resolve_obligation_conflicting_rows_and_stale_preconditions_are_atomic(self):
        from journal_lifecycle import resolve_obligation
        # The checked writer rejects a conflicting lane row before resolution runs.
        self.run = self.root / 'identity'
        args = self.plain_obligation_fixture()
        def conflict(snapshot):
            lane_map = json.loads(snapshot.read_text('lane-map.json'))
            lane_map['lanes'][1]['role'] = 'reviewer'
            return {'lane-map.json': encode_json(lane_map).encode()}, {}
        before = self.raw_state()
        with self.assertRaises(JournalError):
            transact(self.run, 'conflict', {}, conflict)
        self.assertEqual(self.raw_state(), before)
        resolve_obligation(self.run, **args)
        # A damaged lane-map row blocks resolution without writes.
        self.run = self.root / 'conflict'
        args = self.plain_obligation_fixture()
        snapshot = JournalSnapshot.open(self.run)
        lane_map = json.loads(snapshot.read_text('lane-map.json'))
        lane_map['lanes'][0]['status'] = 'pass'
        raw = encode_json(lane_map).encode()
        with sqlite3.connect(self.run / '.journal/state.sqlite3') as db:
            db.execute("UPDATE documents SET content=?, sha256=? WHERE path='lane-map.json'", (raw, digest(raw)))
        before = self.raw_state()
        with self.assertRaisesRegex(JournalError, 'conflict'):
            resolve_obligation(self.run, **args)
        self.assertEqual(self.raw_state(), before)
        self.assertTrue((self.run / '.journal/requests/resolve').is_file())
        # Stale preconditions persist the request but never reach SQL.
        self.run = self.root / 'stale'
        args = self.plain_obligation_fixture()
        with self.assertRaisesRegex(JournalError, 'invalid operation ID'):
            resolve_obligation(self.run, **{**args, 'identifier': '../invalid'})
        with self.assertRaisesRegex(JournalError, 'preconditions'):
            resolve_obligation(self.run, **{**args, 'identifier': 'stale-resolve',
                                            'expected_revision': args['expected_revision'] + 1})
        self.assertTrue((self.run / '.journal/requests/stale-resolve').is_file())
        self.assertIsNone(JournalSnapshot.open(self.run).operation_receipt('stale-resolve'))

    def test_resolve_obligation_requires_same_duty_and_no_cycle(self):
        from journal_lifecycle import resolve_obligation
        from verification_evidence import evaluate_obligations
        args = self.plain_obligation_fixture(wrong_duty=True)
        snapshot = JournalSnapshot.open(self.run)
        with self.assertRaisesRegex(JournalError, 'different obligation'):
            resolve_obligation(self.run, lane_id='old-duty', replacement='wrong', reason='Wrong duty',
                               expected_revision=snapshot.revision, identifier='wrong-resolution')
        self.assertEqual(snapshot.revision, JournalSnapshot.open(self.run).revision)
        resolve_obligation(self.run, **args)
        current = JournalSnapshot.open(self.run)
        summary = json.loads(current.read_text('delegation-summary.json'))
        self.assertEqual(evaluate_obligations(current, summary)[1], [])
        replacement = next(record for record in summary['role_lanes'] if record['lane_id'] == 'replacement')
        replacement['obligation'].update(state='resolved', resolution={'original_lane': 'replacement',
            'replacement': 'old-duty', 'reason': 'Invalid cycle',
            'evidence': [{'path': 'replacement.md', 'sha256': digest(current.read_bytes('replacement.md'))}]})
        self.assertTrue(any('cyclic' in e for e in evaluate_obligations(current, summary)[1]))
        with self.assertRaisesRegex(JournalError, 'terminal handoff'):
            transact(self.run, 'rewrite-old', {}, lambda s: ({'handoffs/old.md': b'Verdict: pass'}, {}))

    def test_resolve_obligation_refuses_closed_generation(self):
        from journal_lifecycle import resolve_obligation
        args = self.plain_obligation_fixture()
        snapshot = JournalSnapshot.open(self.run)
        final = {**self.event, 'timestamp': now_iso()}
        raw = snapshot.read_bytes('timeline.jsonl') + (encode_json(final) + '\n').encode()
        # Fixture-only close; resolve must reject before it can change canonical data.
        with sqlite3.connect(self.run / '.journal/state.sqlite3') as db:
            db.execute('UPDATE documents SET content=?,sha256=? WHERE path=?',
                       (raw, digest(raw), 'timeline.jsonl'))
        before = self.raw_state()
        with self.assertRaisesRegex(JournalError, 'closed'):
            resolve_obligation(self.run, **args)
        self.assertEqual(self.raw_state(), before)

    def test_fresh_behavioral_attestation_preserves_original_inputs(self):
        import copy
        self.fixture()
        snapshot=JournalSnapshot.open(self.run)
        summary=json.loads(snapshot.read_text('delegation-summary.json'))
        check={'criterion_id':'D-PLUGIN','strict_inputs':True,'session_thread_id':'original-session',
               'inputs':[{'path':'original-input.txt','sha256':'a'*64,'prepared_event':1,'source_event':2}],
               'outputs':[{'path':'original-output.txt','sha256':'b'*64,'source_event':3}],
               'verifier_thread_id':'old-reviewer','handoff':{'path':'old-review.md','sha256':'c'*64}}
        summary['verification']['behavioral_checks']=[check]
        raw=encode_json(summary).encode()
        with sqlite3.connect(self.run/'.journal/state.sqlite3') as db:
            db.execute("UPDATE documents SET content=?,sha256=? WHERE path='delegation-summary.json'",(raw,digest(raw)))
        recover(self.run,**self.args())
        def attest(snapshot):
            updated=json.loads(snapshot.read_text('delegation-summary.json'))
            item=updated['verification']['behavioral_checks'][0]
            item['verifier_thread_id']='new-reviewer'
            item['handoff']={'path':'new-review.md','sha256':'d'*64}
            return {'delegation-summary.json':encode_json(updated).encode()},{}
        transact(self.run,'fresh-attestation',{},attest)
        snapshot=JournalSnapshot.open(self.run)
        updated=json.loads(snapshot.read_text('delegation-summary.json'))
        self.assertEqual(updated['verification']['behavioral_checks'][0]['inputs'],check['inputs'])
        for name in ('strict','input','drop'):
            candidate=copy.deepcopy(updated)
            items=candidate['verification']['behavioral_checks']
            if name=='strict': items[0]['strict_inputs']=False
            elif name=='input': items[0]['inputs'][0]['sha256']='e'*64
            else: items.clear()
            with self.subTest(name=name),self.assertRaisesRegex(JournalError,'identity is immutable'):
                transact(self.run,'mutate-'+name,{},lambda s:({'delegation-summary.json':encode_json(candidate).encode()},{}))
            self.assertEqual(snapshot.revision,JournalSnapshot.open(self.run).revision)

    def test_damaged_database_never_falls_back_to_flat_files(self):
        self.fixture()
        (self.run/'final.md').write_text('Tempting flat fallback')
        (self.run/'.journal/state.sqlite3').write_bytes(b'not SQLite')
        with self.assertRaises((JournalError,sqlite3.DatabaseError)):
            diagnose_recovery(self.run)

    def test_storage_archive_link_and_manifest_corruption(self):
        self.fixture();recover(self.run,**self.args())
        with sqlite3.connect(self.run/'.journal/state.sqlite3') as db:
            archive_id,raw=db.execute('SELECT archive_id,content FROM archives').fetchone()
            data=json.loads(raw);data['manifest']['final.md']='0'*64
            raw=encode_json(data).encode()
            db.execute('UPDATE archives SET content=?,sha256=?',(raw,digest(raw)))
        with self.assertRaisesRegex(JournalError,'manifest'):
            JournalSnapshot.open(self.run)

    def test_generic_writer_cannot_forge_lifecycle_artifacts(self):
        self.fixture();recover(self.run,**self.args())
        capture = self.root / 'forged.json'
        capture.write_text('{}')
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'journal.py'), '--run-dir', str(self.run),
                                 'publish', '--file', 'artifacts/lifecycle/legacy-classification.json', str(capture)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('writer-owned', result.stderr)
        self.assertFalse(JournalSnapshot.open(self.run).exists('artifacts/lifecycle/legacy-classification.json'))

    def test_two_archives_are_not_recursive_and_generation_links_hold(self):
        self.fixture();recover(self.run,**self.args())
        # A synthetic closed second generation tests storage independently of report validation.
        with sqlite3.connect(self.run/'.journal/state.sqlite3') as db:
            raw=db.execute("SELECT content FROM documents WHERE path='timeline.jsonl'").fetchone()[0]
            raw+=(encode_json({**self.event,'timestamp':'2026-12-01T00:00:00Z'})+'\n').encode()
            db.execute("UPDATE documents SET content=?,sha256=? WHERE path='timeline.jsonl'",(raw,digest(raw)))
        recover(self.run,**{**self.args(),'identifier':'next-generation'})
        current=JournalSnapshot.open(self.run)
        self.assertEqual(current.generation,3)
        self.assertEqual(len(current.archives),2)
        self.assertTrue(all('archives' not in json.loads(raw) for raw in current.archives.values()))

    def test_process_death_before_and_after_commit_replays(self):
        import signal
        self.fixture();args=self.args()
        request=self.root/'request.json';request.write_text(json.dumps(args))
        script="""import json,os,signal,sys
from journal_recovery import recover
args=json.load(open(sys.argv[2]))
def stop(point):
 if point==sys.argv[3]: os.kill(os.getpid(),signal.SIGKILL)
recover(sys.argv[1],**args,barrier=stop)
"""
        before=self.raw_state()
        for point in ('before-commit','after-commit'):
            process=subprocess.run([sys.executable,'-B','-c',script,str(self.run),str(request),point],
                                   cwd=SCRIPTS,capture_output=True,text=True)
            self.assertEqual(process.returncode,-signal.SIGKILL,process.stderr)
            if point=='before-commit': self.assertEqual(before,self.raw_state())
        after=self.raw_state();recover(self.run,**args);self.assertEqual(after,self.raw_state())

    def test_recovery_callback_has_no_external_filesystem_reads(self):
        self.fixture();args=self.args()
        locked=[False];calls=[];previous=sys.getprofile()
        def audit(event,arguments):
            if locked[0] and event in {'open','os.listdir','os.scandir','subprocess.Popen'}:
                calls.append(event)
        def profile(frame,event,arg):
            if event=='call' and frame.f_globals.get('__name__')=='pathlib' and frame.f_code.co_name in {
                    'resolve','stat','exists','is_file','is_dir','is_symlink'}:
                calls.append(frame.f_code.co_name)
        sys.addaudithook(audit)
        def barrier(point):
            if point=='locked':
                locked[0]=True;sys.setprofile(profile)
            if point=='after-commit':
                locked[0]=False;sys.setprofile(previous)
        try: recover(self.run,**args,barrier=barrier)
        finally: locked[0]=False;sys.setprofile(previous)
        self.assertEqual(calls,[])

    def test_new_generation_requires_new_selected_verifiers(self):
        spec=importlib.util.spec_from_file_location('recovery_lifecycle',SCRIPTS/'test-journal-lifecycle.py')
        lifecycle=importlib.util.module_from_spec(spec);spec.loader.exec_module(lifecycle)
        case=lifecycle.LifecycleTests('test_report_formatting_is_audited_without_blocking_verified_product')
        case.setUp();self.addCleanup(case.doCleanups)
        case.root=case.run.parent
        # Build the unaccepted workspace fixture before assigning terminal outcomes.
        for record in case.summary['subagents']:
            record.pop('status',None)
        raw=encode_json(case.summary).encode()
        with sqlite3.connect(case.run/'.journal/state.sqlite3') as db:
            db.execute("UPDATE documents SET content=?,sha256=? WHERE path='delegation-summary.json'",(raw,digest(raw)))
        lifecycle.fixtures.workspace_pack(case)
        for record in case.summary['subagents']: record['status']='pass'
        transact(case.run,'fixture-accepted-statuses',{},lambda s:({'delegation-summary.json':encode_json(case.summary)},{}))
        summary=json.loads(JournalSnapshot.open(case.run).read_text('delegation-summary.json'))
        summary['verification']['blocker']='Synthetic incomplete acceptance'
        transact(case.run,'blocker',{},lambda s:({'delegation-summary.json':encode_json(summary)},{}))
        case.close(final_summary='Synthetic incomplete acceptance requires recovery',verdict='blocked')
        oldrun=self.run;self.run=case.run
        try:
            args=self.args()
            from task_workspace import registered_workspace
            sealed=registered_workspace(JournalSnapshot.open(case.run))['seal']
            target=Path(sealed['candidate_root'])/'result.txt'
            original_bytes=target.read_bytes()
            target.write_bytes(b'different product')
            with self.assertRaisesRegex(JournalError,'candidate changed'):
                recover(case.run,**args)
            target.write_bytes(original_bytes)
            recover(case.run,**args)
        finally: self.run=oldrun
        transact(case.run,'restore-old-pointers',{},lambda s:({'delegation-summary.json':encode_json(summary)},{}))
        with self.assertRaisesRegex(JournalError,'new QA/reviewer'):
            case.close(expected_generation=2,identifier='old-verifiers-final')
        from copy import deepcopy
        from journal_io import now_iso
        documents={};events=[]
        current=JournalSnapshot.open(case.run)
        for key in ('qa','reviewer'):
            original=next(r for r in summary['subagents'] if r['lane_id']==summary['verification'][key])
            record=deepcopy(original)
            record['lane_id']='fresh-'+key
            record['handoff']='handoffs/fresh-'+key+'.md'
            record['completion_turn_id']=original['completion_turn_id']+'-fresh'
            documents[record['handoff']]=current.read_bytes(original['handoff'])
            summary['subagents'].append(record);summary['verification'][key]=record['lane_id']
            thread=record['codex_thread_id'];session=deepcopy(case.source.sessions[thread])
            stamp=now_iso()
            for observation in session:
                if 'timestamp' in observation: observation['timestamp']=stamp
                body=observation.get('payload',{})
                if 'turn_id' in body: body['turn_id']=record['completion_turn_id']
                if body.get('type')=='task_complete':
                    answer=json.loads(body['last_agent_message']);answer['handoff']=record['handoff']
                    body['last_agent_message']=json.dumps(answer)
                if observation.get('type')=='response_item':
                    answer=json.loads(body['content'][0]['text']);answer['handoff']=record['handoff']
                    body['content'][0]['text']=json.dumps(answer)
            case.source.sessions[thread].extend(session[1:])
            lane_events=[{**lifecycle.fixtures.trace_event(record,stage),'timestamp':stamp} for stage in ('spawned','handoff')]
            trace=''.join(encode_json(e)+'\n' for e in lane_events).encode()
            documents[record['trace']]=current.read_bytes(record['trace'])+trace
            events.extend(lane_events)
        documents['delegation-summary.json']=encode_json(summary).encode()
        documents['timeline.jsonl']=current.read_bytes('timeline.jsonl')+''.join(encode_json(e)+'\n' for e in events).encode()
        transact(case.run,'fresh-verifiers',{},lambda s:(documents,{}))
        result=case.close(expected_generation=2,identifier='new-final',final_summary='Fresh generation independently accepted by current verifiers')
        self.assertEqual(result['generation'],2)
        self.assertTrue(JournalSnapshot.open(case.run).closed)
        from task_workspace import delivery
        delivered=delivery(case.run,session_source=case.source)
        self.assertEqual(delivered['generation'],2)
        case.source.sessions[record['codex_thread_id']].append({'type':'event_msg','payload':{'type':'task_started','turn_id':'late'}})
        with self.assertRaisesRegex(JournalError,'stale|unfinished'):
            delivery(case.run,session_source=case.source)

    def test_full_harness_consumer_accepts_reclosed_negative_generation(self):
        spec=importlib.util.spec_from_file_location('recovery_evidence',SCRIPTS/'test-verification-evidence.py')
        evidence=importlib.util.module_from_spec(spec);spec.loader.exec_module(evidence)
        from journal_lifecycle import finalize
        from harness_promotion import validate_run,PromotionError
        run=self.root/'harness-run';run.mkdir()
        (run/'run.md').write_text('# Synthetic compact change\n')
        (run/'checks.md').write_text('# Checks\n\nFixture assertions.\n')
        summary,source=evidence.acceptance_pack(run)
        for record in summary['subagents']:
            record['status']='pass'
            record['obligation']={'id':record['role'],'required':True,'state':'current'}
        summary['verification']['blocker']='Synthetic unresolved local check'
        evidence.write_json(run/'delegation-summary.json',summary)
        # Full-mode document set for the harness consumer, which forces mode=full.
        for name in ('manifest.md','route.md','definition-of-done.md','decisions.md'):
            (run/name).write_text('# Full fixture\n\nVerdict: blocked\n')
        (run/'artifacts').mkdir(exist_ok=True)
        (run/'checks').mkdir(exist_ok=True)
        (run/'checks'/'smoke.md').write_text('# Synthetic checks\n')
        (run/'artifacts.json').write_text('[]\n')
        timeline=run/'timeline.jsonl'
        lines=timeline.read_bytes().splitlines(keepends=True)
        timeline.write_bytes(b''.join(line for line in lines if json.loads(line).get('stage')!='final'))
        (run/'final.md').unlink()
        documents,identities=journal_cases.journal_io.capture_flat(run)
        snapshot=initialize_journal(run,documents,source_root=self.root,identities=identities,result_contract_version=1)
        finalize(run,expected_run_uuid=snapshot.run_uuid,expected_revision=snapshot.revision,
                 expected_generation=1,identifier='gen1',final_summary='Unresolved local checks remain blocked',
                 verdict='blocked',session_source=source)
        oldrun=self.run;self.run=run
        try: recover(run,**self.args())
        finally: self.run=oldrun
        with patch('verification_evidence.CodexSessionSource',return_value=source):
            with self.assertRaises(PromotionError): validate_run(run)
        check={'timestamp':now_iso(),'stage':'checks','status':'pass','summary':'Current negative report checked',
               'role':'orchestrator','stable_agent_name':'Orchestrator','stable_agent_slug':'orchestrator','artifacts':[],'next_step':''}
        append_event(run/'timeline.jsonl',check,identifier='fresh-check')
        current=JournalSnapshot.open(run)
        finalize(run,expected_run_uuid=current.run_uuid,expected_revision=current.revision,expected_generation=2,
                 identifier='gen2',final_summary='Current unresolved local checks remain blocked',verdict='blocked',
                 session_source=source)
        with patch('verification_evidence.CodexSessionSource',return_value=source):
            _,output=validate_run(run)
        self.assertIn('PASS revision',output)

    def test_public_diagnosis_reopen_and_archive_read(self):
        old=self.fixture()
        command=[sys.executable,'-B',str(SCRIPTS/'journal.py'),'--run-dir',str(self.run)]
        diagnosis=subprocess.run(command+['diagnose-recovery'],capture_output=True,text=True,check=True)
        report=json.loads(diagnosis.stdout)
        identity=self.root/'identity.json';identity.write_text(json.dumps(report['identity']))
        reason='Repair same resultPreserve caller meaning¨Verdict: ship©## Evidence'
        result=subprocess.run(command+['reopen','--expected-run-uuid',old.run_uuid,
            '--expected-revision','1','--expected-generation','1','--operation-id','cli-recover',
            '--reason',reason,'--identity-file',str(identity),'--final-index',str(report['final']['index']),
            '--final-sha256',report['final']['sha256']],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        current=JournalSnapshot.open(self.run)
        events=[json.loads(line) for line in current.read_text('timeline.jsonl').splitlines()]
        self.assertEqual(events[-1]['stage'],'reopen')
        self.assertEqual(events[-1]['summary'],reason)
        archive=json.loads(result.stdout)['archive_id']
        read=subprocess.run(command+['read','final.md','--archive',archive],capture_output=True,check=True)
        self.assertEqual(read.stdout,old.read_bytes('final.md'))

class TraceModeCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / 'run'
        self.root_id = '11111111-1111-4111-8111-111111111111'
        self.source_dir = self.root / 'sessions'; self.source_dir.mkdir()
        from verification_evidence import CodexSessionSource
        self.source = CodexSessionSource()
        self.source.session_dir = lambda: self.source_dir
        spec = importlib.util.spec_from_file_location('mode_recorder', SCRIPTS / 'record-agent-trace.py')
        self.recorder = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.recorder)
        event = dict(timestamp='2026-01-01T00:00:00Z', stage='verification-ready', role='orchestrator',
                     stable_agent_name='orchestrator', stable_agent_slug='orchestrator', status='pass',
                     summary='Root metadata prepared', artifacts=[], next_step='', execution_mode='subagent',
                     agent_trace='agents/orchestrator/trace.jsonl', agent_artifact_dir='artifacts/agents/orchestrator')
        raw = (encode_json(event)+'\n').encode()
        self.payload = {k: event[k] for k in ('role', 'stage', 'status', 'summary', 'execution_mode')}
        self.payload.update(artifact=[], verification_json=encode_json({'root_thread_id': self.root_id}))
        summary = {'verification': {'root_thread_id': self.root_id}, 'subagents': [], 'role_lanes': []}
        initialize_journal(self.run, {'timeline.jsonl':raw, 'agents':None, 'agents/orchestrator':None,
            'agents/orchestrator/trace.jsonl':raw, 'delegation-summary.json':encode_json(summary).encode()}, source_root=self.root)
        result = {'revision':1,'completion_fields':{},'indexed_artifacts':0,'result_hash':'a'*64}
        # Model the already persisted faulty registration; new ordinary writers must reject it.
        with sqlite3.connect(self.run / '.journal/state.sqlite3') as db:
            db.execute('INSERT INTO operations VALUES (?,?,?,?)', ('old-mode',digest(encode_json(self.payload).encode()),1,encode_json(result)))
        snapshot = JournalSnapshot.open(self.run)
        request = {'run_uuid':snapshot.run_uuid,'operation_id':'old-mode','command':'record-agent-trace','payload':self.payload}
        import shlex
        self.argv = ['python3','-B',str(self.root/'agent-flow/scripts/record-agent-trace.py'),'--run-dir',str(self.run),
                     '--operation-id','old-mode','--role','orchestrator','--stage','verification-ready','--status','pass',
                     '--verification-json',str(self.root/'metadata.json'),'--summary','Root metadata prepared']
        self.command = "python3 -B - <<'PY'\nprint('preparation')\nPY\n" + shlex.join(self.argv)
        output = {'exit_code':0,'output':'receipt: '+encode_json({'operation_id':'old-mode',**result})+'\n'}
        self.events = [dict(type='session_meta',payload={'id':self.root_id,'session_id':self.root_id,'source':'vscode','cwd':str(self.root)}),
            dict(type='response_item',payload={'type':'custom_tool_call','name':'exec','call_id':'call-original',
                'input':'text(await tools.exec_command('+json.dumps({'cmd':self.command})+'));'}),
            dict(type='response_item',payload={'type':'custom_tool_call_output','call_id':'call-original',
                'output':[{'type':'input_text','text':json.dumps(output)}]})]
        self.source_path = self.source_dir / (self.root_id+'.jsonl')
        self.write_source()
        ref = lambda i: {'index':i,'sha256':digest(self.source_path.read_bytes().splitlines(keepends=True)[i-1])}
        self.proof = {'run_uuid':snapshot.run_uuid,'generation':1,'revision':snapshot.revision,'root_thread_id':self.root_id,
            'operation_id':'correct-mode','reason':'Source proves root metadata',
            'timeline':{'path':'timeline.jsonl','size':len(raw),'sha256':digest(raw)},
            'trace':{'path':'agents/orchestrator/trace.jsonl','size':len(raw),'sha256':digest(raw)},
            'targets':[{'timeline_index':1,'trace_index':1,'sha256':digest(raw),'old':{'execution_mode':'subagent'},
                'new':{'execution_mode':'role-lane'},'request':request,'source':[{'call':ref(2),'output':ref(3)}]}]}

    def write_source(self):
        self.source_path.write_text(''.join(encode_json(e)+'\n' for e in self.events))

    def correct(self, proof=None, **kwargs):
        proof = proof or self.proof
        return self.recorder.record_mode_correction(self.run, proof, identifier=proof['operation_id'], session_source=self.source, **kwargs)

    def state(self):
        s=JournalSnapshot.open(self.run)
        return s.revision,dict(s.documents),dict(s.receipts),dict(s.archives)

    def test_append_preserves_raw_history_and_retries_after_close(self):
        from verification_evidence import effective_trace_modes
        before=JournalSnapshot.open(self.run)
        def barrier(phase):
            if phase=='locked':
                self.assertTrue((self.run/'.journal/requests/correct-mode').is_file())
        result=self.correct(barrier=barrier)
        current=JournalSnapshot.open(self.run)
        self.assertEqual(current.revision,before.revision+1)
        self.assertEqual(current.generation,before.generation)
        for path,raw in before.documents.items():
            if path in ('timeline.jsonl','agents/orchestrator/trace.jsonl'):
                self.assertTrue(current.read_bytes(path).startswith(raw))
            else:self.assertEqual(current.documents[path],raw)
        self.assertEqual(effective_trace_modes(current,self.source),{('agents/orchestrator/trace.jsonl',1):'role-lane'})
        validator_spec=importlib.util.spec_from_file_location('mode_validator',SCRIPTS/'validate-run.py')
        validator=importlib.util.module_from_spec(validator_spec);validator_spec.loader.exec_module(validator)
        self.assertEqual(validator.validate_agent_traces(self.run,snapshot=current,session_source=self.source),[])
        self.assertEqual(self.correct(),result)
        final={**json.loads(before.read_bytes('timeline.jsonl')),'stage':'final','status':'blocked'}
        # Safe fixture-only close simulation, never delivery or the real recovery journal.
        with sqlite3.connect(self.run/'.journal/state.sqlite3') as db:
            path='timeline.jsonl';raw=current.read_bytes(path)+(encode_json(final)+'\n').encode()
            db.execute('UPDATE documents SET content=?,sha256=? WHERE path=?',(raw,digest(raw),path))
        closed=self.state();self.assertTrue(JournalSnapshot.open(self.run).closed)
        self.assertEqual(self.correct(),result);self.assertEqual(self.state(),closed)
        import copy
        changed=copy.deepcopy(self.proof);changed['reason']='different'
        with self.assertRaisesRegex(JournalError,'conflict'):self.correct(changed)
        self.assertEqual(self.state(),closed)
        changed['operation_id']='new-after-close'
        with self.assertRaisesRegex(JournalError,'closed'):self.correct(changed)
        self.assertEqual(self.state(),closed)

    def test_captured_source_survives_change_before_sql(self):
        def barrier(phase):
            if phase=='captured':self.source_path.write_text('source removed after capture')
        self.assertEqual(self.correct(barrier=barrier)['corrected'],1)
        from verification_evidence import effective_trace_modes
        with self.assertRaises(JournalError):effective_trace_modes(JournalSnapshot.open(self.run),self.source)

    def test_precommit_and_lost_response_are_retryable(self):
        before=self.state()
        def stop(phase):
            if phase=='locked':raise OSError('before commit')
        with self.assertRaisesRegex(OSError,'before commit'):self.correct(barrier=stop)
        self.assertEqual(self.state(),before)
        self.assertTrue((self.run/'.journal/requests/correct-mode').exists())
        def lost(phase):
            if phase=='after-commit':raise OSError('lost response')
        with self.assertRaisesRegex(OSError,'lost response'):self.correct(barrier=lost)
        committed=self.state();self.correct();self.assertEqual(self.state(),committed)

    def test_cas_and_duplicate_target_refuse(self):
        import copy
        before=self.state()
        for field,value in [('revision',0),('generation',2),('run_uuid','22222222-2222-4222-8222-222222222222')]:
            proof=copy.deepcopy(self.proof);proof[field]=value;proof['operation_id']='stale-'+field
            with self.assertRaisesRegex(JournalError,'preconditions'):self.correct(proof)
            self.assertEqual(self.state(),before)
        self.correct();current=self.state()
        proof=copy.deepcopy(self.proof);proof['operation_id']='duplicate';proof['revision']=current[0]
        with self.assertRaisesRegex(JournalError,'already'):self.correct(proof)
        self.assertEqual(self.state(),current)

    def test_false_proof_fields_and_source_links_refuse_atomically(self):
        import copy
        changes=[lambda p:p.update(extra=True),lambda p:p['targets'].append(copy.deepcopy(p['targets'][0])),
            lambda p:p['targets'][0].update(sha256='0'*64),lambda p:p['targets'][0].update(trace_index=2),
            lambda p:p['trace'].update(path='different'),lambda p:p['targets'][0]['new'].update(status='pass'),
            lambda p:p['targets'][0]['source'][0]['call'].update(sha256='0'*64),
            lambda p:p['targets'][0]['request'].update(operation_id='missing'),
            lambda p:p['targets'][0]['request']['payload'].update(summary='changed'),
            lambda p:p.update(root_thread_id='22222222-2222-4222-8222-222222222222')]
        before=self.state()
        for index,change in enumerate(changes):
            with self.subTest(index=index):
                proof=copy.deepcopy(self.proof);proof['operation_id']='invalid-'+str(index);change(proof)
                with self.assertRaises(JournalError):self.correct(proof)
                self.assertEqual(self.state(),before)

    def test_flat_cli_and_api_create_no_requests(self):
        flat=self.root/'flat';flat.mkdir();(flat/'run.md').write_text('Legacy')
        def tree():
            return {str(p.relative_to(flat)):('link',str(p.readlink())) if p.is_symlink() else
                    ('dir',None) if p.is_dir() else ('file',p.read_bytes()) for p in flat.rglob('*')}
        before=tree()
        with self.assertRaisesRegex(JournalError,'unsupported journal storage version'):
            self.recorder.record_mode_correction(flat,self.proof,identifier='correct-mode',session_source=self.source)
        capture=self.root/'correction.json';capture.write_text(json.dumps(self.proof))
        result=subprocess.run([sys.executable,'-B',str(SCRIPTS/'record-agent-trace.py'),'--run-dir',str(flat),
            '--correction-file',str(capture),'--operation-id','correct-mode'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertIn('unsupported journal storage version',result.stderr)
        self.assertEqual(tree(),before);self.assertFalse((flat/'.journal').exists())

    def test_orphan_not_hidden_by_unrelated_spawn(self):
        spec=importlib.util.spec_from_file_location('orphan_validator',SCRIPTS/'validate-run.py')
        validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)
        self.correct()
        s=JournalSnapshot.open(self.run)
        event=json.loads(s.read_bytes('agents/orchestrator/trace.jsonl').splitlines()[0]);event['summary']='new orphan'
        spawned={**event,'stage':'spawned','codex_thread_id':'22222222-2222-4222-8222-222222222222'}
        from dataclasses import replace
        documents=dict(s.documents)
        for path in ('timeline.jsonl','agents/orchestrator/trace.jsonl'):
            documents[path]+=(encode_json(spawned)+'\n'+encode_json(event)+'\n').encode()
        future=replace(s,documents=documents)
        errors=validator.validate_agent_traces(self.run,snapshot=future,session_source=self.source)
        self.assertTrue(any('own spawned identity' in e for e in errors),errors)

    def test_source_wrapper_and_argv_reject_expressions_and_quotes(self):
        from verification_evidence import trace_source_calls,trace_recorder_argv
        for wrapper in ['"'+self.events[1]['payload']['input']+'"',
                        self.events[1]['payload']['input']+'extra()',
                        'text(await tools.exec_command({cmd:dynamic}));',
                        'text(await tools.exec_command({cmd:"literal",unknown:1}));']:
            with self.subTest(wrapper=wrapper),self.assertRaises((JournalError,ValueError)):
                trace_source_calls(wrapper)
        for command in [self.command+'; echo injected',self.command.replace('--role orchestrator','--role $(whoami)'),
                        "python3 -B - <<'PY'\ncommand=['not executed']\nPY"]:
            with self.subTest(command=command),self.assertRaises(JournalError):trace_recorder_argv(command,str(self.root))


    def test_python_preparation_whitelist_rejects_rebinding_and_side_effects(self):
        from verification_evidence import trace_recorder_argv
        code = "import sys,subprocess\nfrom pathlib import Path\ncommand=[sys.executable,'-B','agent-flow/scripts/record-agent-trace.py']\nc=subprocess.run(command,capture_output=True,text=True)"
        self.assertEqual(trace_recorder_argv("python3 -B - <<'PY'\n"+code+"\nPY",str(self.root)),
                         ['python3','-B','agent-flow/scripts/record-agent-trace.py'])
        for extra in ["setattr(subprocess,'run',print)","globals()['subprocess']=None","__import__('os')",
                      "exec('pass')","unknown_side_effect()","sys.path.insert(0,'untrusted')","alias=subprocess;alias.run([])",
                      "subprocess.run=print","Path=lambda x:x","sys=None","import subprocess as other",
                      "Path('agent-flow/scripts/record-agent-trace.py').write_text('changed')"]:
            source="python3 -B - <<'PY'\n"+extra+'\n'+code+'\nPY'
            with self.subTest(extra=extra),self.assertRaises(JournalError):trace_recorder_argv(source,str(self.root))
        for extra in ["alias=command", "command[0]='other'", "command.append('extra')"]:
            source="python3 -B - <<'PY'\n"+code.replace('c=subprocess.run',extra+'\nc=subprocess.run')+'\nPY'
            with self.subTest(extra=extra),self.assertRaises(JournalError):trace_recorder_argv(source,str(self.root))
        for prefix in ["import os\nos.environ['PATH']='other'", "Path('python3').write_text('changed')",
                       "exec('pass')", "unknown_side_effect()"]:
            with self.subTest(prefix=prefix),self.assertRaises(JournalError):
                trace_recorder_argv(self.command.replace("print('preparation')",prefix),str(self.root))

    def test_assignment_targets_cannot_be_reclassified(self):
        import copy
        from dataclasses import replace
        from verification_evidence import verify_trace_mode_correction
        before=JournalSnapshot.open(self.run)
        for fields in [{'codex_thread_id':self.root_id},{'lane_id':'real'},{'completion_turn_id':'turn'},
                       {'handoff':'handoff.md'},{'artifacts':['handoff.md']},{'stage':'spawned'},
                       {'stage':'handoff'},{'status':'fail'},{'status':'blocked'}]:
            with self.subTest(fields=fields):
                event=json.loads(before.read_bytes('timeline.jsonl'));event.update(fields)
                raw=(encode_json(event)+'\n').encode();documents=dict(before.documents)
                proof=copy.deepcopy(self.proof)
                for key in ('timeline','trace'):
                    path=proof[key]['path'];documents[path]=raw;proof[key].update(size=len(raw),sha256=digest(raw))
                proof['targets'][0]['sha256']=digest(raw)
                with self.assertRaisesRegex(JournalError,'not root verification metadata'):
                    verify_trace_mode_correction(replace(before,documents=documents),proof,self.source)

    def test_native_cli_correction_and_source_output_negatives(self):
        import copy,os
        capture=self.root/'correction.json';capture.write_text(json.dumps(self.proof))
        argv=[sys.executable,'-B',str(SCRIPTS/'record-agent-trace.py'),'--run-dir',str(self.run),
              '--correction-file',str(capture),'--operation-id','correct-mode']
        # CODEX_HOME is confined to this child fixture process; user configuration is untouched.
        env={**os.environ,'CODEX_HOME':str(self.root),'PYTHONDONTWRITEBYTECODE':'1'}
        result=subprocess.run(argv,capture_output=True,text=True,env=env)
        self.assertEqual(result.returncode,0,result.stderr)
        state=self.state();retry=subprocess.run(argv,capture_output=True,text=True,env=env)
        self.assertEqual(retry.returncode,0,retry.stderr);self.assertEqual(self.state(),state)
        from verification_evidence import effective_trace_modes
        original=copy.deepcopy(self.events)
        for mutate in [lambda e:e[2]['payload'].update(call_id='wrong'),
                       lambda e:e[1]['payload'].update(name='other'),
                       lambda e:e[1]['payload'].update(input='"quoted command"'),
                       lambda e:e[0]['payload'].update(id='wrong'),
                       lambda e:e[2]['payload'].update(output=[{'type':'input_text','text':'{"exit_code":1,"output":""}'}])]:
            self.events=copy.deepcopy(original);mutate(self.events);self.write_source()
            with self.assertRaises(JournalError):effective_trace_modes(JournalSnapshot.open(self.run),self.source)
            self.assertEqual(self.state(),state)

    def test_sql_uses_only_captured_inputs(self):
        import sys
        active=[False]
        def audit(event,args):
            if active[0] and event in {'open','os.listdir','os.scandir','subprocess.Popen'}:
                raise AssertionError('external I/O inside correction SQL callback: '+event)
        sys.addaudithook(audit)
        real=self.recorder.transact
        def monitored(run,identifier,payload,mutation):
            def callback(snapshot):
                active[0]=True
                try:return mutation(snapshot)
                finally:active[0]=False
            return real(run,identifier,payload,callback)
        with patch.object(self.recorder,'transact',monitored):self.correct()
        from verification_evidence import effective_trace_modes
        self.assertEqual(len(effective_trace_modes(JournalSnapshot.open(self.run),self.source)),1)


    def test_same_metadata_different_timestamp_is_ambiguous(self):
        import copy
        from dataclasses import replace
        from verification_evidence import verify_trace_mode_correction
        snapshot=JournalSnapshot.open(self.run);first=snapshot.read_bytes('timeline.jsonl')
        event=json.loads(first);event['timestamp']='2026-01-01T00:00:01Z'
        second=(encode_json(event)+'\n').encode();raw=first+second
        documents=dict(snapshot.documents);proof=copy.deepcopy(self.proof)
        for key in ('timeline','trace'):
            path=proof[key]['path'];documents[path]=raw;proof[key].update(size=len(raw),sha256=digest(raw))
        proof['targets'][0].update(timeline_index=2,trace_index=2,sha256=digest(second))
        with self.assertRaisesRegex(JournalError,'ambiguous original metadata'):
            verify_trace_mode_correction(replace(snapshot,documents=documents),proof,self.source)

    def test_concurrent_append_leaves_no_partial_correction(self):
        concurrent=[]
        def barrier(phase):
            if phase=='captured':
                snapshot=JournalSnapshot.open(self.run)
                event=json.loads(snapshot.read_bytes('timeline.jsonl'))
                event.update(stage='checks',status='pass',execution_mode='role-lane',summary='Concurrent check')
                append_event(self.run/'timeline.jsonl',event,identifier='competitor')
                concurrent.append(self.state())
        with self.assertRaisesRegex(JournalError,'preconditions'):
            self.correct(barrier=barrier)
        self.assertEqual(self.state(),concurrent[0])
        self.assertIsNone(JournalSnapshot.open(self.run).operation_receipt('correct-mode'))

    def test_source_adapter_preserves_raw_line_hash_and_filters_unselected_input(self):
        self.source_path.write_bytes(b'\r\n'.join(encode_json(e).encode() for e in self.events)+b'\r\n')
        ordinary=self.source.read(self.root_id)
        self.assertNotIn('input',ordinary[1]['payload'])
        selected=self.source.read(self.root_id,event_indices=[2])
        raw=self.source_path.read_bytes().splitlines(keepends=True)[1]
        self.assertEqual(selected[1]['source_sha256'],digest(raw))
        self.assertNotEqual(selected[1]['source_sha256'],digest(raw.replace(b'\r\n',b'\n')))
        self.assertEqual(selected[1]['payload']['input'],self.events[1]['payload']['input'])

    def test_flat_diagnostic_retains_old_spawn_interpretation(self):
        from dataclasses import replace
        spec=importlib.util.spec_from_file_location('flat_mode_validator',SCRIPTS/'validate-run.py')
        validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)
        s=JournalSnapshot.open(self.run);documents=dict(s.documents)
        event=json.loads(s.read_bytes('timeline.jsonl'));event.update(stage='spawned',codex_thread_id=self.root_id)
        for path in ('timeline.jsonl','agents/orchestrator/trace.jsonl'):
            documents[path]+=(encode_json(event)+'\n').encode()
        flat=replace(s,durable=False,documents=documents)
        self.assertEqual(validator.validate_agent_traces(self.run,snapshot=flat,session_source=self.source),[])
        current=replace(s,documents=documents)
        self.assertTrue(any('own spawned identity' in e for e in validator.validate_agent_traces(self.run,snapshot=current,session_source=self.source)))

    def test_ordinary_api_refuses_missing_identity_before_preparation(self):
        from types import SimpleNamespace
        snapshot=JournalSnapshot.open(self.run);before=self.state()
        args=SimpleNamespace(execution_mode='subagent',codex_thread_id=None,lane_id=None,
                             resolve_session=False,role='orchestrator',stage='verification-ready',summary='Identity is required before preparation',status='active')
        with patch.object(self.recorder,'prepare_summary',side_effect=AssertionError('preparation must not run')):
            with self.assertRaisesRegex(JournalError,'explicit --execution-mode role-lane'):
                self.recorder.record_event(self.run,args,[],self.source,snapshot)
        self.assertEqual(self.state(),before)


    def test_published_audit_without_correction_receipt_is_rejected(self):
        from verification_evidence import effective_trace_modes
        snapshot=JournalSnapshot.open(self.run)
        event=json.loads(snapshot.read_bytes('timeline.jsonl'))
        event.update(stage='trace-mode-corrected',status='done',execution_mode='role-lane',
                     mode_correction=self.proof,generation=1)
        raw=(encode_json(event)+'\n').encode()
        transact(self.run,'generic-publish',{'command':'publish'},lambda current:({
            path:current.read_bytes(path)+raw for path in ('timeline.jsonl','agents/orchestrator/trace.jsonl')},{}))
        with self.assertRaisesRegex(JournalError,'receipt missing or conflicting'):
            effective_trace_modes(JournalSnapshot.open(self.run),self.source)

if __name__ == '__main__':
    unittest.main()
