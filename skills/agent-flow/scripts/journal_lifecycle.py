from journal_io import JournalSnapshot, encode_json, require, require_writable, transact
from verification_evidence import CodexSessionSource, revalidate_report, validate_acceptance


def finalize(run_dir, *, identifier, report_id, source=None):
    require(isinstance(report_id, str) and bool(report_id.strip()), 'final report ID required')
    payload = {'command': 'finalize', 'report_id': report_id}
    snapshot = JournalSnapshot.open(run_dir)
    require_writable(snapshot)
    if snapshot.operation_receipt(identifier) is not None:
        return transact(run_dir, identifier, payload, lambda current: ({}, {}))
    require(not snapshot.exists('final.json'), 'task is already closed')
    source = source or CodexSessionSource()
    require(snapshot.exists('assignments/' + report_id + '.json')
            and snapshot.exists('reports/' + report_id + '.json'), 'final report missing')
    assignment = snapshot.json('assignments/' + report_id + '.json')
    report = snapshot.json('reports/' + report_id + '.json')
    if report.get('outcome') == 'passed':
        errors = validate_acceptance(snapshot, source=source, report_id=report_id)
        require(not errors, 'finalize validation failed: ' + '; '.join(errors))
    else:
        require(assignment['agent_id'] == snapshot.identities['root_agent_id'], 'terminal nonpass report must belong to root')
        revalidate_report(snapshot, assignment, report, source)
        require(isinstance(report.get('reason'), str) and bool(report['reason'].strip()), 'terminal reason required')
        require(isinstance(report.get('unverified'), list) and all(isinstance(item, str) and item.strip() for item in report['unverified']), 'terminal report requires unverified list')
    def publish(current):
        require(not current.exists('final.json'), 'task is already closed')
        final = {'status': report['status'], 'outcome': report['outcome'], 'summary': report['summary'],
                 'report_id': report_id}
        if report['outcome'] != 'passed':
            final.update(reason=report['reason'], unverified=report['unverified'])
        return {'final.json': encode_json(final).encode()}, {'command': 'finalize', 'outcome': report['outcome'], 'path': 'final.json'}
    return transact(run_dir, identifier, payload, publish, expected_revision=snapshot.revision)
