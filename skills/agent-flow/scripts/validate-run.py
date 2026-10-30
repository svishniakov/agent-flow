#!/usr/bin/env python3
import argparse
from pathlib import Path
from journal_io import JournalError, JournalSnapshot
from verification_evidence import validate_acceptance


def validate_run(run_dir, *, snapshot=None, session_source=None, report_id=None):
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    if report_id is None and snapshot.exists('final.json'):
        report_id = snapshot.json('final.json').get('report_id')
    return validate_acceptance(snapshot, source=session_source, report_id=report_id)


def main():
    parser = argparse.ArgumentParser(description='Validate current Agent Flow reports and result')
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--report-id')
    args = parser.parse_args()
    try:
        errors = validate_run(args.run_dir, report_id=args.report_id)
    except (JournalError, OSError) as exc:
        errors = [str(exc)]
    for error in errors:
        print('FAIL ' + error)
    if not errors:
        print('PASS Agent Flow acceptance')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
