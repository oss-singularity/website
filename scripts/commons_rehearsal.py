"""Canonical Commons artifact rehearsal, with no provider or deployment authority."""
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import re

import commons_artifact as artifact
from site_artifact import read_external, require

# Reuse bounded JSON, fixed read-only GitHub transport and exclusive receipt I/O.
# This module comes from the trusted checkout, never from the downloaded packet.
spec = importlib.util.spec_from_file_location('commons_rehearsal_transport',
                                            Path(__file__).with_name('release-rehearsal.py'))
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)

REPOSITORY = 'oss-singularity/website'
REPOSITORY_ID = 1351274990
WORKFLOW_REF = REPOSITORY + '/.github/workflows/commons-release-rehearsal.yml@refs/heads/main'
SCHEMA_SHA256 = '1d1800a100d598b2076c4932ad866e4e254595326eb7fb387522b51775f01278'
PENDING = ['successful-github-run', 'required-github-checks', 'trusted-provenance-consumption',
           'fresh-protected-head-at-promotion', 'fresh-installed-schema', 'scoped-provider-access',
           'serialized-promotion', 'durable-recovery', 'live-verification']


def context(checkout_commit, environ):
    sha = artifact.commit(checkout_commit)
    fixed = {'GITHUB_ACTIONS': 'true', 'GITHUB_SERVER_URL': 'https://github.com',
             'GITHUB_API_URL': shared.API_URL, 'GITHUB_REPOSITORY': REPOSITORY,
             'GITHUB_REPOSITORY_ID': str(REPOSITORY_ID), 'GITHUB_EVENT_NAME': 'push',
             'GITHUB_REF': 'refs/heads/main', 'GITHUB_REF_PROTECTED': 'true', 'GITHUB_SHA': sha,
             'GITHUB_WORKFLOW_SHA': sha, 'GITHUB_WORKFLOW_REF': WORKFLOW_REF}
    require(all(type(environ.get(key)) is str and environ[key] == value for key, value in fixed.items()),
            'untrusted_context')
    return {'repository': REPOSITORY, 'repository_id': REPOSITORY_ID, 'event': 'push', 'ref': 'refs/heads/main',
            'commit': sha, 'workflow_ref': WORKFLOW_REF, 'workflow_sha': sha,
            'run_id': shared.decimal(environ.get('GITHUB_RUN_ID')),
            'run_attempt': shared.decimal(environ.get('GITHUB_RUN_ATTEMPT'))}


def gate(checkout_commit, environ, fetch=shared.github_get):
    observed = context(checkout_commit, environ)
    main = fetch(shared.BRANCH_ROUTE, environ)
    require(type(main) is dict and main.get('name') == 'main' and main.get('protected') is True,
            'unprotected_main')
    require(type(main.get('commit')) is dict and main['commit'].get('sha') == observed['commit'], 'stale_main')
    return {**observed, 'observed_main_sha': observed['commit'], 'observed_main_protected': True,
            'deployment_authorized': False}


def uploaded(value, number, checksum, observed):
    require(type(value) is dict and type(value.get('id')) is int and value['id'] == number,
            'artifact_identity_mismatch')
    expected_name = f"commons-candidate-{observed['commit']}-{observed['run_id']}-{observed['run_attempt']}"
    require(value.get('name') == expected_name, 'artifact_identity_mismatch')
    require(value.get('digest') == 'sha256:' + checksum and value.get('expired') is False,
            'artifact_unavailable_or_changed')
    expiry = value.get('expires_at')
    require(type(expiry) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z', expiry),
            'artifact_unavailable_or_changed')
    try:
        expires = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
    except ValueError:
        require(False, 'artifact_unavailable_or_changed')
    require(expires > datetime.now(timezone.utc), 'artifact_unavailable_or_changed')
    run = value.get('workflow_run')
    require(type(run) is dict, 'artifact_identity_mismatch')
    for key, expected in [('id', observed['run_id']), ('repository_id', REPOSITORY_ID), ('head_repository_id', REPOSITORY_ID)]:
        require(type(run.get(key)) is int and run[key] == expected, 'artifact_identity_mismatch')
    require(run.get('head_branch') == 'main' and run.get('head_sha') == observed['commit'], 'artifact_identity_mismatch')


def receipt(checkout_commit, artifact_id, artifact_digest, packet_path, source, output,
            environ, fetch=shared.github_get):
    # Check the downloaded bytes against independently captured source before
    # making authenticated requests. No downloaded module or SQL is executed.
    context(checkout_commit, environ)
    number, checksum = shared.decimal(artifact_id), shared.digest(artifact_digest)
    raw = read_external(packet_path, artifact.MAX_PACKET)
    files, descriptor = artifact.unpack(raw, checkout_commit, SCHEMA_SHA256)
    expected, schema = artifact.source_files(source)
    require(files == expected and schema == SCHEMA_SHA256, 'rebuild_mismatch')
    observed = gate(checkout_commit, environ, fetch)
    uploaded(fetch(shared.ARTIFACT_ROUTE + str(number), environ), number, checksum, observed)
    value = {'schema_version': 1, 'kind': 'commons-release-rehearsal', **observed,
             'artifact': {'id': number, 'digest_sha256': checksum, 'metadata_verified': True},
             'packet_sha256': artifact.digest(raw), 'descriptor': descriptor,
             'checks': {'artifact_verified': True, 'rebuild_matched': True, 'transport_roundtrip': True},
             'pending_gates': list(PENDING)}
    shared.write_receipt(output, value)
    return value
