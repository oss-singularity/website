#!/usr/bin/env python3
"""Check one canonical main rehearsal; never authorize production deployment."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import urllib.request

from site_artifact import ArtifactError, absolute_path, open_directory, read_external, require

REPOSITORY = 'oss-singularity/website'
REPOSITORY_ID = 1351274990
API_URL = 'https://api.github.com'
WORKFLOW_REF = REPOSITORY + '/.github/workflows/static-release-rehearsal.yml@refs/heads/main'
BRANCH_ROUTE = '/repos/' + REPOSITORY + '/branches/main'
ARTIFACT_ROUTE = '/repos/' + REPOSITORY + '/actions/artifacts/'
MAX_JSON = 131072
PENDING_GATES = ['successful-github-run', 'required-github-checks', 'trusted-provenance-consumption',
                 'fresh-protected-head-at-promotion', 'serialized-promotion', 'api-compatibility',
                 'provider-authorization', 'preserved-overlays', 'live-verification', 'rollback']

spec = importlib.util.spec_from_file_location('rehearsal_artifact', Path(__file__).with_name('release-artifact.py'))
artifact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifact)


def decimal(value):
    require(type(value) is str and re.fullmatch(r'[1-9][0-9]{0,18}', value) is not None, 'invalid_identity')
    return int(value)


def digest(value):
    require(type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) is not None, 'invalid_digest')
    return value


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'invalid_json')
        value[key] = item
    return value


def reject_constant(_value):
    raise ArtifactError('invalid_json')


def decode_object(raw, limit):
    require(type(raw) is bytes and len(raw) <= limit, 'invalid_json')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise ArtifactError('invalid_json') from None
    require(type(value) is dict, 'invalid_json')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request, _response, _code, _message, _headers, _url):
        return None


def github_get(route, environ):
    """Only two fixed canonical resources; credentials never follow redirects."""
    require(route == BRANCH_ROUTE or re.fullmatch(re.escape(ARTIFACT_ROUTE) + r'[1-9][0-9]{0,18}', route),
            'invalid_api_route')
    authorization = environ.get('GH_TOKEN')
    require(type(authorization) is str and re.fullmatch(r'[\x21-\x7e]{1,8192}', authorization) is not None,
            'missing_api_authentication')
    url = API_URL + route
    try:
        request = urllib.request.Request(url, headers={
            'Authorization': 'Bearer ' + authorization,
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2026-03-10',
            'Cache-Control': 'no-cache',
            'User-Agent': 'oss-singularity-static-rehearsal',
        }, method='GET')
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=10) as response:
            require(response.status == 200 and response.geturl() == url, 'github_read_failed')
            raw = response.read(MAX_JSON + 1)
    except ArtifactError:
        raise
    except Exception:
        raise ArtifactError('github_read_failed') from None
    return decode_object(raw, MAX_JSON)


def context(checkout_commit, environ):
    sha = artifact.commit(checkout_commit)
    fixed = {
        'GITHUB_SERVER_URL': 'https://github.com', 'GITHUB_API_URL': API_URL,
        'GITHUB_REPOSITORY': REPOSITORY, 'GITHUB_REPOSITORY_ID': str(REPOSITORY_ID),
        'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main', 'GITHUB_REF_PROTECTED': 'true',
        'GITHUB_SHA': sha, 'GITHUB_WORKFLOW_SHA': sha, 'GITHUB_WORKFLOW_REF': WORKFLOW_REF,
    }
    require(all(type(environ.get(key)) is str and environ[key] == value for key, value in fixed.items()),
            'untrusted_context')
    return {'repository': REPOSITORY, 'repository_id': REPOSITORY_ID, 'event': 'push', 'ref': 'refs/heads/main',
            'commit': sha, 'workflow_ref': WORKFLOW_REF, 'workflow_sha': sha,
            'run_id': decimal(environ.get('GITHUB_RUN_ID')), 'run_attempt': decimal(environ.get('GITHUB_RUN_ATTEMPT'))}


def gate(checkout_commit, environ, fetch=github_get):
    value = context(checkout_commit, environ)
    main = fetch(BRANCH_ROUTE, environ)
    require(type(main) is dict and main.get('name') == 'main' and main.get('protected') is True,
            'unprotected_main')
    require(type(main.get('commit')) is dict and main['commit'].get('sha') == value['commit'], 'stale_main')
    return {**value, 'observed_main_sha': value['commit'], 'observed_main_protected': True,
            'deployment_authorized': False}


def validate_uploaded(value, artifact_id, artifact_digest, observed):
    require(type(value) is dict and type(value.get('id')) is int and value['id'] == artifact_id,
            'artifact_identity_mismatch')
    require(value.get('digest') == 'sha256:' + artifact_digest and value.get('expired') is False,
            'artifact_unavailable_or_changed')
    expected_name = f"static-candidate-{observed['commit']}-{observed['run_id']}-{observed['run_attempt']}"
    require(value.get('name') == expected_name, 'artifact_identity_mismatch')
    expiry = value.get('expires_at')
    require(type(expiry) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z', expiry),
            'artifact_unavailable_or_changed')
    try:
        expiration = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
    except ValueError:
        raise ArtifactError('artifact_unavailable_or_changed') from None
    require(expiration > datetime.now(timezone.utc), 'artifact_unavailable_or_changed')
    run = value.get('workflow_run')
    require(type(run) is dict, 'artifact_identity_mismatch')
    for key, expected in [('id', observed['run_id']), ('repository_id', REPOSITORY_ID), ('head_repository_id', REPOSITORY_ID)]:
        require(type(run.get(key)) is int and run[key] == expected, 'artifact_identity_mismatch')
    require(run.get('head_branch') == 'main' and run.get('head_sha') == observed['commit'], 'artifact_identity_mismatch')


def roundtrip(metadata_path, report_path, sha):
    raw = read_external(metadata_path, 4096)
    metadata = artifact.validate_descriptor(decode_object(raw, 4096))
    require(metadata['commit'] == sha, 'commit_mismatch')
    report = decode_object(read_external(report_path, 16384), 16384)
    expected = artifact.plan(metadata, 'matched')
    require(set(report) == set(expected), 'invalid_roundtrip_report')
    artifact.validate_descriptor({key: report[key] for key in artifact.FIELDS})
    require(report.get('artifact_verified') is True and report.get('deployment_authorized') is False
            and report == expected, 'invalid_roundtrip_report')
    return metadata, hashlib.sha256(raw).hexdigest()


def write_receipt(path, value):
    target = absolute_path(path)
    parent = open_directory(target.parent)
    try:
        fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'w') as output:
            output.write(json.dumps(value, sort_keys=True, indent=2) + '\n')
            output.flush()
            os.fsync(output.fileno())
        os.fsync(parent)
    finally:
        os.close(parent)


def receipt(checkout_commit, artifact_id, artifact_digest, metadata_path, report_path, output, environ, fetch=github_get):
    # Validate local inputs before making authenticated requests.
    context(checkout_commit, environ)
    number, checksum = decimal(artifact_id), digest(artifact_digest)
    metadata, metadata_hash = roundtrip(metadata_path, report_path, checkout_commit)
    observed = gate(checkout_commit, environ, fetch)
    uploaded = fetch(ARTIFACT_ROUTE + str(number), environ)
    validate_uploaded(uploaded, number, checksum, observed)
    value = {'schema_version': 1, 'kind': 'static-release-rehearsal', **observed,
             'artifact': {'id': number, 'digest_sha256': checksum, 'metadata_verified': True},
             'descriptor': metadata, 'descriptor_sha256': metadata_hash,
             'checks': {'artifact_verified': True, 'reproducibility': 'matched', 'transport_roundtrip': True},
             'pending_gates': list(PENDING_GATES)}
    write_receipt(output, value)
    return value


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ArtifactError('invalid_arguments')


def main(argv=None, environ=None, fetch=github_get):
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
    gate_parser = commands.add_parser('gate', help='Observe protected current main for this exact checkout')
    gate_parser.add_argument('--checkout-commit', required=True)
    receipt_parser = commands.add_parser('receipt', help='Record a checked roundtrip and observed GitHub artifact identity')
    receipt_parser.add_argument('--checkout-commit', required=True)
    receipt_parser.add_argument('--artifact-id', required=True)
    receipt_parser.add_argument('--artifact-digest', required=True)
    receipt_parser.add_argument('--descriptor', type=Path, required=True)
    receipt_parser.add_argument('--roundtrip-report', type=Path, required=True)
    receipt_parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    if args.command == 'gate':
        return gate(args.checkout_commit, environ, fetch)
    return receipt(args.checkout_commit, args.artifact_id, args.artifact_digest, args.descriptor,
                   args.roundtrip_report, args.out, environ, fetch)


def cli(argv=None, environ=None, fetch=github_get):
    output = io.StringIO()
    try:
        # Error messages and incidental dependency diagnostics must not copy input.
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            result = main(argv, environ, fetch)
    except SystemExit as error:
        if error.code == 0:
            print(output.getvalue(), end='')
            return 0
        print(json.dumps({'error': 'invalid_arguments'}), file=sys.stderr)
        return 1
    except ArtifactError as error:
        print(json.dumps({'error': error.code}), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({'error': 'unsafe_or_unavailable_path'}), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({'error': 'rehearsal_failed'}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(cli())
