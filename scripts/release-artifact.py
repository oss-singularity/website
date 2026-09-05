#!/usr/bin/env python3
"""Create or verify a static artifact descriptor offline; never authorize deployment."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import re
import sys

from site_artifact import ArtifactError, MAX_FILES, MAX_TOTAL_BYTES, capture, private_copy, read_external, require, summary, write_descriptor

REPOSITORY = 'oss-singularity/website'
KIND = 'static-site'
VERSION = 1
FIELDS = {'schema_version', 'repository', 'kind', 'commit', 'manifest_sha256', 'file_count', 'total_bytes'}
PENDING_GATES = ['trusted-github-run', 'protected-current-commit', 'artifact-provenance', 'serialized-promotion',
                 'api-compatibility', 'provider-authorization', 'live-verification', 'rollback']


def trusted_module(name, filename):
    # Import the checker shipped beside this trusted CLI, never from the payload.
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


site = trusted_module('artifact_site_checker', 'check-site.py')
agent_data = trusted_module('artifact_agent_checker', 'check-agent-data.py')


def commit(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{40}', value) is not None, 'invalid_commit')
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_descriptor_key')
        result[key] = value
    return result


def reject_constant(_value):
    raise ArtifactError('invalid_descriptor')


def validate_descriptor(value):
    require(type(value) is dict and set(value) == FIELDS, 'invalid_descriptor')
    require(type(value['schema_version']) is int and value['schema_version'] == VERSION, 'invalid_descriptor')
    require(value['repository'] == REPOSITORY and value['kind'] == KIND, 'invalid_descriptor')
    commit(value['commit'])
    require(isinstance(value['manifest_sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', value['manifest_sha256']) is not None, 'invalid_descriptor')
    for key, limit in [('file_count', MAX_FILES), ('total_bytes', MAX_TOTAL_BYTES)]:
        require(type(value[key]) is int and 0 < value[key] <= limit, 'invalid_descriptor')
    return value


def descriptor(path):
    try:
        value = json.loads(read_external(path, 4096).decode('utf-8'), object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ArtifactError('invalid_descriptor') from None
    return validate_descriptor(value)


def inspect(root):
    files = capture(root, site.required_files)
    # The product checks retain their own ordinary budgets and data rules. Their
    # detailed messages can contain rejected input, so never forward those here.
    try:
        with private_copy(files) as snapshot, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            site.check_product(snapshot)
            agent_data.check(snapshot)
    except (SystemExit, ValueError, OSError, KeyError, TypeError):
        raise ArtifactError('product_checks_failed') from None
    return files


def make_descriptor(files, sha):
    return {'schema_version': VERSION, 'repository': REPOSITORY, 'kind': KIND, 'commit': commit(sha), **summary(files)}


def plan(value, reproducibility='not_checked'):
    return {**value, 'artifact_verified': True, 'reproducibility': reproducibility,
            'deployment_authorized': False, 'pending_gates': list(PENDING_GATES)}


def create(root, sha, destination):
    commit(sha)
    files = inspect(root)
    value = make_descriptor(files, sha)
    write_descriptor(destination, (json.dumps(value, sort_keys=True, indent=2) + '\n').encode(), root)
    return plan(value)


def verify(root, metadata, expected_commit, rebuild=None):
    commit(expected_commit)
    value = descriptor(metadata)
    require(value['commit'] == expected_commit, 'commit_mismatch')
    files = inspect(root)
    require(value == make_descriptor(files, expected_commit), 'descriptor_mismatch')
    if rebuild is not None:
        rebuilt = inspect(rebuild)
        require(files == rebuilt, 'rebuild_mismatch')
    return plan(value, 'matched' if rebuild is not None else 'not_checked')


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ArtifactError('invalid_arguments')


def main(argv=None):
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
    create_parser = commands.add_parser('create', help='Verify bytes and write a new descriptor outside the payload')
    create_parser.add_argument('--artifact-dir', type=Path, required=True)
    create_parser.add_argument('--commit', required=True)
    create_parser.add_argument('--out', type=Path, required=True)
    verify_parser = commands.add_parser('verify', help='Verify bytes against a descriptor and caller-supplied commit')
    verify_parser.add_argument('--artifact-dir', type=Path, required=True)
    verify_parser.add_argument('--descriptor', type=Path, required=True)
    verify_parser.add_argument('--expected-commit', required=True)
    verify_parser.add_argument('--rebuild-dir', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'create':
        result = create(args.artifact_dir, args.commit, args.out)
    else:
        result = verify(args.artifact_dir, args.descriptor, args.expected_commit, args.rebuild_dir)
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
        # Keep unexpected checker/parser failures fail-closed and content-free.
        print(json.dumps({'error': 'verification_failed'}), file=sys.stderr)
        raise SystemExit(1) from None
