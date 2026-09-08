#!/usr/bin/env python3
"""Create or verify a Commons code candidate offline, without deployment authority."""
import argparse
import json
from pathlib import Path
import sys

import commons_artifact as artifact
from site_artifact import ArtifactError


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ArtifactError('invalid_arguments')


def main(argv=None):
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
    create = commands.add_parser('create')
    create.add_argument('--source-dir', type=Path, required=True)
    create.add_argument('--commit', required=True)
    create.add_argument('--out', type=Path, required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('--candidate', type=Path, required=True)
    verify.add_argument('--expected-commit', required=True)
    verify.add_argument('--expected-schema-sha256', required=True)
    verify.add_argument('--rebuild-source', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'create':
        result = artifact.create(args.source_dir, args.commit, args.out)
    else:
        result = artifact.verify(args.candidate, args.expected_commit, args.expected_schema_sha256, args.rebuild_source)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ArtifactError as error:
        print(json.dumps({'error': error.code}), file=sys.stderr)
        raise SystemExit(1) from None
    except OSError:
        print(json.dumps({'error': 'unsafe_or_unavailable_path'}), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print(json.dumps({'error': 'verification_failed'}), file=sys.stderr)
        raise SystemExit(1) from None
