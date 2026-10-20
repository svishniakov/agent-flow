#!/usr/bin/env python3
import argparse
from pathlib import Path
from journal_io import JournalError, JournalSnapshot
from verification_evidence import validate_acceptance


def validate_run(run_dir, *, snapshot=None, session_source=None):
    return validate_acceptance(snapshot or JournalSnapshot.open(run_dir), source=session_source)


def main():
    parser = argparse.ArgumentParser(description='Validate current Agent Flow reports and result')
    parser.add_argument('--run-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        errors = validate_run(args.run_dir)
    except (JournalError, OSError) as exc:
        errors = [str(exc)]
    for error in errors:
        print('FAIL ' + error)
    if not errors:
        print('PASS Agent Flow acceptance')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
