from journal_io import JournalError, JournalSnapshot, encode_json, require, transact
from verification_evidence import result_fingerprint, validate_acceptance


def finalize(run_dir, *, identifier, status, summary, reason=None, unverified=None, source=None):
    require(status in {'completed', 'failed'}, 'final status must be completed or failed')
    require(isinstance(summary, str) and bool(summary.strip()), 'final summary required')
    if status == 'failed':
        require(isinstance(reason, str) and bool(reason.strip()), 'failed final reason required')
        require(isinstance(unverified, list) and all(isinstance(item, str) and item.strip() for item in unverified), 'failed final requires unverified list')
    payload = {'command': 'finalize', 'status': status, 'summary': summary, 'reason': reason, 'unverified': unverified}
    snapshot = JournalSnapshot.open(run_dir)
    if snapshot.operation_receipt(identifier) is not None:
        return transact(run_dir, identifier, payload, lambda current: ({}, {}))
    try:
        fingerprint = result_fingerprint(snapshot)
    except (JournalError, OSError):
        require(status == 'failed', 'current result cannot be verified')
        fingerprint = None
    if status == 'completed':
        errors = validate_acceptance(snapshot, source=source)
        require(not errors, 'finalize validation failed: ' + '; '.join(errors))
    def publish(current):
        require(not current.exists('final.json'), 'task is already closed')
        if fingerprint is not None:
            require(result_fingerprint(current) == fingerprint, 'result changed before final publication')
        if status == 'completed':
            errors = validate_acceptance(current, source=source)
            require(not errors, 'finalize validation failed: ' + '; '.join(errors))
        final = {'status': status, 'summary': summary, 'fingerprint': fingerprint,
                 'reports': [name for name in sorted(current.documents) if name.startswith('reports/') and name.endswith('.json')]}
        if status == 'failed':
            final.update(reason=reason, unverified=unverified)
        return {'final.json': encode_json(final).encode()}, {'command': 'finalize', 'status': status, 'path': 'final.json'}
    return transact(run_dir, identifier, payload, publish)
