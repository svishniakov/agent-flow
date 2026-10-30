import json
from pathlib import Path
import subprocess

from journal_io import encode_json, require


def transfer_records(snapshot):
    return [json.loads(data) for path, data in snapshot.documents.items() if path.startswith('transfers/')]


def transferred_assignments(snapshot):
    return {record['from_assignment_id'] for record in transfer_records(snapshot)}


def current_authored_paths(snapshot):
    from journal_structure import assignments
    known = assignments(snapshot)
    edges = {}
    for key, assignment in known.items():
        previous = assignment.get('previous_assignment_id')
        if assignment['kind'] == 'author' and previous:
            edges.setdefault(previous, []).append((key, None))
    retired = set()
    for transfer in transfer_records(snapshot):
        old = transfer['from_assignment_id']
        if transfer['purpose'] == 'verification_only':
            retired.add(old)
        else:
            edges.setdefault(old, []).append((transfer['to_assignment_id'], transfer['root_map']))
    result = set()
    for name, data in snapshot.documents.items():
        if not name.startswith('reports/'):
            continue
        key = Path(name).stem
        if known[key]['kind'] != 'author':
            continue
        pending = [(key, path) for path in json.loads(data).get('authored_paths', [])]
        seen = set()
        while pending:
            current, raw = pending.pop()
            if (current, raw) in seen or current in retired:
                continue
            seen.add((current, raw))
            successors = edges.get(current, [])
            if not successors:
                result.add(raw)
            for successor, mapping in successors:
                if mapping is None:
                    pending.append((successor, raw))
                    continue
                path = Path(raw)
                for pair in mapping:
                    left, right = Path(pair['from']), Path(pair['to'])
                    if path == left or path.is_relative_to(left):
                        pending.append((successor, str(right / path.relative_to(left))))
    return sorted(result)


def git_root(path):
    path = Path(path)
    require(path.is_absolute() and path == path.resolve(), 'transfer root must be canonical')
    result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--show-toplevel', '--path-format=absolute', '--git-common-dir'],
                            capture_output=True, text=True, check=False)
    require(result.returncode == 0, 'transfer root must be a Git worktree')
    values = result.stdout.splitlines()
    require(len(values) == 2 and Path(values[0]).resolve() == path, 'transfer root must be the worktree root')
    return Path(values[1]).resolve()


def prepare_transfer(snapshot, request, source, identifier):
    from journal_structure import assignment_record, assignments, record_id, validate_request
    from verification_evidence import native_assignment_stopped, revalidate_report, verify_evidence
    from workspace_scope import path_allowed
    old_id = record_id(request.get('from_assignment_id'))
    known = assignments(snapshot)
    require(old_id in known and known[old_id]['kind'] == 'author', 'transfer requires an author assignment')
    require(old_id not in transferred_assignments(snapshot), 'assignment was transferred')
    old = known[old_id]
    purpose = request.get('purpose', 'continue')
    require(purpose in {'continue', 'verification_only'}, 'invalid transfer purpose')
    body = {'from_assignment_id': old_id, 'purpose': purpose}
    updates = {}
    if purpose == 'verification_only':
        body['stopped'] = native_assignment_stopped(source, old)
        require(not request.get('new_assignment') and not request.get('root_map'), 'verification-only transfer has no successor')
        reason = request.get('reason')
        require(isinstance(reason, str) and bool(reason.strip()), 'verification-only reason required')
        reviewer_id = request.get('reviewer_assignment_id')
        require(isinstance(reviewer_id, str) and reviewer_id in known and known[reviewer_id]['kind'] == 'reviewer', 'verification-only reviewer required')
        report = snapshot.json('reports/' + reviewer_id + '.json')
        require(report.get('status') == 'completed' and report.get('outcome') == 'passed'
                and old_id in report.get('verification_only', []), 'reviewer must attest verification-only assignment')
        revalidate_report(snapshot, known[reviewer_id], report, source)
        verify_evidence(snapshot, request.get('evidence'), source, root_id=snapshot.identities['root_agent_id'], success_required=True)
        body.update(reason=reason, evidence=request['evidence'], reviewer_assignment_id=reviewer_id)
    else:
        mapping = request.get('root_map')
        require(isinstance(mapping, list) and bool(mapping), 'transfer root map required')
        scope = snapshot.json('scope.json')
        roots = {entry['path'] for entry in scope['roots']}
        pairs = []
        for entry in mapping:
            require(isinstance(entry, dict) and set(entry) == {'from', 'to'}, 'root map requires from and to')
            require(all(isinstance(value, str) for value in entry.values()), 'transfer roots must be paths')
            left, right = Path(entry['from']), Path(entry['to'])
            require(str(left) in roots and str(right) in roots, 'transfer roots outside product scope')
            require(git_root(left) == git_root(right), 'transfer roots must share Git common-dir')
            require(left != right, 'transfer requires a different worktree')
            pairs.append((left, right))
        require(len({left for left, _ in pairs}) == len(pairs), 'duplicate transfer source root')
        mapped = []
        for raw in old['allowed_paths']:
            path = Path(raw)
            candidates = [(left, right) for left, right in pairs if path == left or path.is_relative_to(left)]
            require(len(candidates) == 1 and path_allowed(scope, raw), 'transfer does not cover predecessor path')
            left, right = candidates[0]
            target = right / path.relative_to(left)
            require(path_allowed(scope, str(target)) and target == target.resolve(), 'mapped path escapes worktree')
            mapped.append(target)
        incoming = request.get('new_assignment')
        require(isinstance(incoming, dict), 'new assignment required')
        validate_request('assignment', incoming)
        require(incoming.get('kind') == 'author' and not incoming.get('previous_assignment_id'), 'transfer successor must be a new author')
        successor = assignment_record(snapshot, incoming, source, transfer_from=old_id)
        require(all(any(path == Path(allowed) or path.is_relative_to(Path(allowed)) for allowed in successor['allowed_paths'])
                    for path in mapped), 'transfer successor must cover mapped predecessor paths')
        body['stopped'] = native_assignment_stopped(source, old,
            before_turn_id=successor['start_turn_id'] if successor['agent_id'] == old['agent_id'] else None)
        successor['transfer_from_assignment_id'] = old_id
        body.update(to_assignment_id=successor['id'], root_map=mapping)
        updates['assignments/' + successor['id'] + '.json'] = encode_json(successor).encode()
    path = 'transfers/' + record_id(identifier) + '.json'
    updates[path] = encode_json(body).encode()
    return updates, {'path': path, 'from_assignment_id': old_id, 'to_assignment_id': body.get('to_assignment_id')}
