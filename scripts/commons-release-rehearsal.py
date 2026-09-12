#!/usr/bin/env python3
"""Observe a canonical Commons code rehearsal without deploying it."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys

import commons_rehearsal as rehearsal
from site_artifact import ArtifactError


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ArtifactError('invalid_arguments')


def main(argv=None, environ=None, fetch=rehearsal.shared.github_get):
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
    gate = commands.add_parser('gate')
    gate.add_argument('--checkout-commit', required=True)
    receipt = commands.add_parser('receipt')
    receipt.add_argument('--checkout-commit', required=True)
    receipt.add_argument('--artifact-id', required=True)
    receipt.add_argument('--artifact-digest', required=True)
    receipt.add_argument('--candidate', type=Path, required=True)
    receipt.add_argument('--source-dir', type=Path, required=True)
    receipt.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    if args.command == 'gate':
        return rehearsal.gate(args.checkout_commit, environ, fetch)
    return rehearsal.receipt(args.checkout_commit, args.artifact_id, args.artifact_digest,
                             args.candidate, args.source_dir, args.out, environ, fetch)


def cli(argv=None, environ=None, fetch=rehearsal.shared.github_get):
    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            result = main(argv, environ, fetch)
    except SystemExit as error:
        if error.code == 0:
            print(output.getvalue(), end='')
            return 0
        result = {'error': 'invalid_arguments'}
    except ArtifactError as error:
        result = {'error': error.code if error.code in {
            'invalid_arguments', 'invalid_commit', 'invalid_identity', 'invalid_digest', 'untrusted_context',
            'unprotected_main', 'stale_main', 'artifact_identity_mismatch', 'artifact_unavailable_or_changed',
            'rebuild_mismatch', 'missing_api_authentication', 'github_read_failed'} else 'rehearsal_failed'}
    except Exception:
        result = {'error': 'rehearsal_failed'}
    print(json.dumps(result, sort_keys=True, indent=2), file=sys.stderr if 'error' in result else sys.stdout)
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(cli())
