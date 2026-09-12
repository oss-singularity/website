"""Join reviewed source, durable intent, fixed static writes and live acceptance."""
import base64
import os
from pathlib import Path
import re
import secrets
import subprocess

import release_source as source
from release_source import checks, candidate, artifact, digest, encode, write_private
from release_deployment import Deployments
from release_edge import Cloudflare
from release_http import HTTP
from release_transport import SSH, RemoteFailure, validate_report
import site_artifact as tree
from site_artifact import ArtifactError, require

WORKFLOW = '.github/workflows/static-publication.yml'
CLOSED = {'empty', 'verified', 'rolled_back', 'aborted'}
FAILURE_STAGES = {'prepare', 'apply', 'purge', 'http_acceptance', 'provider_check', 'reconcile',
                  'recovery_status', 'recovery_reconcile', 'rollback', 'rollback_purge',
                  'rollback_http', 'rollback_provider_check', 'maintenance'}
FAILURE_CODES = {'stale_main', 'http_bytes_mismatch', 'http_mime_mismatch', 'http_cache_mismatch',
                 'http_security_mismatch', 'http_compression_missing', 'http_transport_failed',
                 'cache_transition_failed', 'redirect_mismatch', 'tls_unverified', 'api_unverified',
                 'unexpected_http_cookie', 'edge_unverified', 'security_text_mismatch',
                 'provider_request_failed', 'provider_configuration_changed', 'cache_purge_unconfirmed',
                 'remote_outcome_unconfirmed', 'remote_host_identity_failed', 'remote_authentication_failed',
                 'remote_connection_failed', 'remote_identity_mismatch', 'filesystem_preparation_unconfirmed',
                 'filesystem_promotion_unconfirmed', 'rollback_unconfirmed', 'maintenance_unconfirmed'}


def failure_detail(stage, error):
    """Keep useful failure categories without disclosing exception text or paths."""
    code = error.code if isinstance(error, ArtifactError) else None
    return {'stage': stage if stage in FAILURE_STAGES else 'unconfirmed',
            'code': code if type(code) is str and code in FAILURE_CODES else 'unconfirmed'}


class PublicationFailure(ArtifactError):
    def __init__(self, restored, stage, error, recovery_stage=None, recovery_error=None):
        super().__init__('publication_rolled_back' if restored else 'publication_requires_reconciliation')
        self.failure = failure_detail(stage, error)
        self.recovery_failure = (failure_detail(recovery_stage, recovery_error)
                                 if recovery_error is not None else None)


def context(sha, mode, environ):
    expected = {'GITHUB_ACTIONS': 'true', 'GITHUB_SERVER_URL': 'https://github.com',
                'GITHUB_API_URL': 'https://api.github.com', 'GITHUB_REPOSITORY': source.checks.REPOSITORY,
                'GITHUB_REPOSITORY_ID': str(checks.REPOSITORY_ID), 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_REF_PROTECTED': 'true', 'GITHUB_SHA': sha, 'GITHUB_WORKFLOW_SHA': sha,
                'GITHUB_WORKFLOW_REF': checks.REPOSITORY + '/' + WORKFLOW + '@refs/heads/main'}
    require(all(environ.get(name) == value for name, value in expected.items()), 'untrusted_workflow')
    require(mode in {'plan', 'publish'} and environ.get('GITHUB_EVENT_NAME') in {'workflow_dispatch', 'workflow_run'},
            'untrusted_workflow')
    if mode == 'publish':
        require(environ.get('STATIC_PUBLISH_ENABLED') == 'true', 'publication_disabled')
    event = checks.decode(tree.read_external(Path(environ['GITHUB_EVENT_PATH']), checks.MAX_JSON))
    if environ['GITHUB_EVENT_NAME'] == 'workflow_run':
        require(mode == 'publish' and type(event) is dict, 'untrusted_workflow')
        run = event.get('workflow_run')
        checks.run_value(run, 345834976, sha)
    else:
        require(type(event) is dict and type(event.get('inputs')) is dict
                and event['inputs'].get('mode') == mode, 'untrusted_workflow')
    run_id = candidate.rehearsal.decimal(environ.get('GITHUB_RUN_ID'))
    attempt = candidate.rehearsal.decimal(environ.get('GITHUB_RUN_ATTEMPT'))
    return {'workflow_run_id': run_id, 'workflow_run_attempt': attempt}


def baseline_matches(report, baseline):
    return all(report.get(name) == baseline[name] for name in ['baseline_commit', 'baseline_manifest_sha256'])


def applied(report, product, baseline):
    require(report['phase'] == 'verified' and report['baseline_commit'] == product.sha
            and report['baseline_manifest_sha256'] == product.report['descriptor']['manifest_sha256']
            and report['generation'] == baseline['generation'] + 1, 'filesystem_promotion_unconfirmed')


def transition(product, baseline, previous_files, edge_before, api_sha, run, folder,
               remote, http, edge, deployments, previous_deployment, fresh):
    """Publish one candidate. Recovery always keeps the original attempt identity."""
    identity = secrets.token_hex(16)
    description_digest = digest(product.descriptor)
    intent = {'schema_version': 1, 'kind': 'static-publication-intent', **run,
              'identity': identity, 'commit': product.sha, 'descriptor_sha256': description_digest,
              'predecessor': {name: baseline[name] for name in
                              ['baseline_commit', 'baseline_manifest_sha256', 'generation']},
              'runtime_sha256': remote.runtime, 'artifacts': product.report['artifacts'],
              'candidate_run_id': product.run_id, 'candidate_run_attempt': product.attempt}
    write_private(folder / 'intent.json', encode(intent))
    fresh('before_intent')
    number = deployments.start(product.sha, intent, previous_deployment)
    request = {'schema': 1, 'operation': 'prepare', 'identity': identity,
               'expected_generation': baseline['generation'],
               'expected_predecessor_commit': baseline['baseline_commit'],
               'expected_manifest_sha256': baseline['baseline_manifest_sha256'],
               'candidate_commit': product.sha, 'candidate_descriptor': base64.b64encode(product.descriptor).decode(),
               'files': {name: base64.b64encode(raw).decode() for name, raw in product.files.items()}}
    ticket, prepare_sent = None, False

    def status():
        result = remote.status(identity, description_digest)
        require(result['candidate_commit'] == product.sha, 'remote_identity_mismatch')
        if ticket is not None:
            require(result['ticket'] == ticket, 'remote_identity_mismatch')
        return result

    stage = 'prepare'
    try:
        fresh('before_prepare')
        prepare_sent = True
        try:
            prepared = remote.call(request)
            validate_report(prepared, remote.runtime, identity, description_digest)
        except RemoteFailure:
            # A lost response is queried by the already retained identity.
            # An absent/foreign attempt is not adopted or replayed automatically.
            prepared = status()
        ticket = prepared['ticket']
        require(ticket['generation'] == baseline['generation'] and prepared['phase'] == 'prepared',
                'filesystem_preparation_unconfirmed')
        write_private(folder / 'ticket.json', encode(ticket))
        stage = 'apply'
        fresh('before_apply')
        try:
            result = remote.operation('apply', ticket)
        except RemoteFailure:
            result = status()
            if result['phase'] != 'verified':
                result = remote.operation('reconcile', ticket)
                require(result['phase'] in {'prepared', 'applying', 'applied'}, 'filesystem_promotion_unconfirmed')
                fresh('before_recovered_apply')
                result = remote.operation('apply', ticket)
        applied(result, product, baseline)
        # Purge immediately after application, before warming or public reads.
        stage = 'purge'
        edge.purge()
        stage = 'http_acceptance'
        live = http.verify(product.files, edge_before, api_sha)
        stage = 'provider_check'
        require(edge.observe(product.files['.well-known/security.txt']) == edge_before,
                'provider_configuration_changed')
        stage = 'reconcile'
        applied(remote.operation('reconcile', ticket), product, baseline)
    except Exception as failure:
        restored = False
        recovery_stage, recovery_error = 'recovery_status', None
        try:
            if prepare_sent:
                report = status()
                if ticket is None:
                    ticket = report['ticket']
                    require(ticket['generation'] == baseline['generation'], 'remote_identity_mismatch')
                if report['phase'] not in {'rolled_back', 'aborted'}:
                    recovery_stage = 'recovery_reconcile'
                    report = remote.operation('reconcile', ticket)
                    if report['phase'] != 'aborted':
                        recovery_stage = 'rollback'
                        try:
                            report = remote.operation('rollback', ticket)
                        except RemoteFailure:
                            report = status()
                require(report['phase'] in {'rolled_back', 'aborted'} and baseline_matches(report, baseline),
                        'rollback_unconfirmed')
            else:
                report = remote.status()
                require(report['phase'] in CLOSED and baseline_matches(report, baseline)
                        and report['generation'] == baseline['generation'], 'rollback_unconfirmed')
            recovery_stage = 'rollback_purge'
            edge.purge()
            recovery_stage = 'rollback_http'
            http.verify(previous_files, edge_before, api_sha, historical=True)
            recovery_stage = 'rollback_provider_check'
            require(edge.observe(previous_files['.well-known/security.txt']) == edge_before,
                    'provider_configuration_changed')
            restored = True
        except Exception as error:
            recovery_error = error
        deployments.finish(number, 'rolled_back' if restored else 'unresolved')
        raise PublicationFailure(restored, stage, failure, recovery_stage, recovery_error) from None

    # A maintenance/bookkeeping failure does not undo a healthy verified site.
    # Keep the durable intent unresolved until this housekeeping is reconciled.
    try:
        try:
            maintained = remote.operation('maintain', ticket)
        except RemoteFailure:
            status()
            maintained = remote.operation('maintain', ticket)
        applied(maintained, product, baseline)
        require(maintained.get('maintenance_pending') is False and type(maintained.get('retained_attempts')) is int
                and maintained['retained_attempts'] == 1,
                'maintenance_unconfirmed')
    except Exception as failure:
        deployments.finish(number, 'unresolved')
        raise PublicationFailure(False, 'maintenance', failure) from None
    deployments.finish(number, 'success')
    return {'deployment_id': number, 'plan_sha256': ticket['plan_sha256'], 'live': live,
            'rollback_retained': True, 'publication_verified': True}


def run(sha, mode, root, folder, environ):
    artifact.commit(sha)
    identity = context(sha, mode, environ)
    expected_tree = source.checked_source(sha, root)
    github = source.GitHub(environ)
    deployments = Deployments(environ)
    previous_deployment = deployments.previous()
    source.wait_for_sources(github, sha)
    check_report = checks.verify(sha, folder / 'required-checks.json', {}, github.get)
    product = source.consume(github, sha, folder)
    rebuilt = subprocess.run([str(root / 'scripts/build-site.sh'), str(folder / 'rebuild')], cwd=root,
        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'TZ': 'UTC'},
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=False)
    require(rebuilt.returncode == 0 and artifact.inspect(folder / 'rebuild') == product.files, 'rebuild_mismatch')
    remote = SSH(environ, folder)
    baseline = remote.status()
    require(baseline['phase'] in CLOSED and baseline['maintenance_pending'] is False, 'unfinished_publication')
    http, edge = HTTP(environ['STATIC_ORIGIN_IP']), Cloudflare(environ)
    expected_api = artifact.commit(environ.get('STATIC_API_RELEASE_SHA'))
    api_sha = http.api(expected_api)
    compatible = source.compatibility(github, api_sha, expected_tree)
    edge_before = edge.observe(product.files['.well-known/security.txt'])
    previous_access = source.historical_access(github, baseline['baseline_commit'])
    previous_files = http.predecessor(baseline, previous_access)
    http.verify(previous_files, edge_before, api_sha, cache_probe=False, historical=True)
    require(edge.observe(product.files['.well-known/security.txt']) == edge_before,
            'provider_configuration_changed')
    result = {'schema_version': 1, 'kind': 'static-publication', 'mode': mode, 'commit': sha,
              'candidate_run_id': product.run_id, 'candidate_run_attempt': product.attempt,
              'manifest_sha256': product.report['descriptor']['manifest_sha256'], 'file_count': len(product.files),
              'api_compatibility': compatible, 'required_policy_sha256': check_report['policy_sha256'],
              'source_rebuilt': True, 'publication_verified': False, 'publication_attempted': False}
    if mode == 'plan':
        return result

    def fresh(stage):
        # All policy/run/check observations are performed anew. Artifact metadata
        # must still identify the captured producer attempt and exact ZIP bytes.
        checks.verify(sha, folder / ('required-checks-' + stage + '.json'), {}, github.get)
        current = candidate.observe(sha, product.run_id, product.attempt,
            product.report['artifacts']['candidate']['id'], tree.read_external(folder / 'candidate.zip', candidate.MAX_ARCHIVE),
            product.report['artifacts']['receipt']['id'], tree.read_external(folder / 'receipt.zip',
                candidate.MAX_RECEIPT + candidate.MAX_CENTRAL + 1024),
            {}, github.get)
        require(current == product.report['artifacts'], 'artifact_identity_mismatch')
        http.api(api_sha)
        require(edge.observe(product.files['.well-known/security.txt']) == edge_before,
                'provider_configuration_changed')

    published = transition(product, baseline, previous_files, edge_before, api_sha, identity, folder,
                           remote, http, edge, deployments, previous_deployment, fresh)
    return {**result, **published, 'publication_attempted': True}
