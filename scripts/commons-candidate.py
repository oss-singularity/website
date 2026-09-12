#!/usr/bin/env python3
"""Independently verify completed Commons archives, source and required checks."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys

import commons_candidate as candidate
from site_artifact import ArtifactError


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ArtifactError('invalid_arguments')


def main(argv=None, environ=None, fetch=None):
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
    verify = commands.add_parser('verify', help='Verify an explicit canonical run and caller-supplied archives')
    for name in ['expected-commit', 'run-id', 'run-attempt', 'candidate-id', 'receipt-id']:
        verify.add_argument('--' + name, required=True)
    for name in ['candidate-archive', 'receipt-archive', 'source-dir', 'out']:
        verify.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    return candidate.verify(args.expected_commit, args.run_id, args.run_attempt,
                            args.candidate_id, args.candidate_archive, args.receipt_id, args.receipt_archive,
                            args.source_dir, args.out, os.environ if environ is None else environ, fetch)


def cli(argv=None, environ=None, fetch=None):
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
            result = main(argv, environ, fetch)
    except SystemExit as error:
        if error.code == 0:
            print(buffer.getvalue(), end='')
            return 0
        result = {'error': 'invalid_arguments'}
    except ArtifactError as error:
        result = {'error': error.code if error.code in candidate.ERROR_CODES else 'commons_candidate_failed'}
    except OSError:
        result = {'error': 'unsafe_or_unavailable_path'}
    except Exception:
        result = {'error': 'commons_candidate_failed'}
    print(json.dumps(result, sort_keys=True, indent=2), file=sys.stderr if 'error' in result else sys.stdout)
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(cli())
