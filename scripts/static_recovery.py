"""Close one blocked static publication record after verifying the live result.

The publication path deliberately blocks while its durable deployment record is
not closed. This module implements that bounded exceptional recovery: it re-reads
the original recorded intent, reconciles the retained remote attempt under its
original identity, verifies the resulting live bytes and only then closes the
record as a verified rollback, or as success when only maintenance or record
bookkeeping failed after an already healthy verified site. It never creates a
deployment, starts a publication, adopts a foreign attempt or resets endpoint
state. Recovery deliberately does not require STATIC_PUBLISH_ENABLED: an
operator may disable automatic publication during an incident and still close a
blocked record.
"""
from pathlib import Path

import release_source as source
from release_source import checks, candidate, digest, historical_access
from release_deployment import Deployments
from release_edge import Cloudflare
from release_http import HTTP
from release_transport import RemoteFailure, SSH
import static_plan as plan
import static_publication as publication
import site_artifact as tree
from site_artifact import ArtifactError, require

WORKFLOW = '.github/workflows/static-publication.yml'
TERMINAL_ROLLBACK = {'rolled_back', 'aborted'}
INTENT_FIELDS = {'schema_version', 'kind', 'workflow_run_id', 'workflow_run_attempt', 'identity',
                 'commit', 'descriptor_sha256', 'predecessor', 'runtime_sha256', 'artifacts',
                 'candidate_run_id', 'candidate_run_attempt'}


def context(sha, environ):
    """Bind recovery to one trusted dispatch of the canonical publication workflow."""
    expected = {'GITHUB_ACTIONS': 'true', 'GITHUB_SERVER_URL': 'https://github.com',
                'GITHUB_API_URL': 'https://api.github.com', 'GITHUB_REPOSITORY': source.checks.REPOSITORY,
                'GITHUB_REPOSITORY_ID': str(checks.REPOSITORY_ID), 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_REF_PROTECTED': 'true', 'GITHUB_SHA': sha, 'GITHUB_WORKFLOW_SHA': sha,
                'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'GITHUB_WORKFLOW_REF': checks.REPOSITORY + '/' + WORKFLOW + '@refs/heads/main'}
    require(all(environ.get(name) == value for name, value in expected.items()), 'untrusted_workflow')
    event = checks.decode(tree.read_external(Path(environ['GITHUB_EVENT_PATH']), checks.MAX_JSON))
    require(type(event) is dict and type(event.get('inputs')) is dict
            and event['inputs'].get('mode') == 'recover', 'untrusted_workflow')
    run_id = candidate.rehearsal.decimal(environ.get('GITHUB_RUN_ID'))
    attempt = candidate.rehearsal.decimal(environ.get('GITHUB_RUN_ATTEMPT'))
    return {'workflow_run_id': run_id, 'workflow_run_attempt': attempt}


def intent(payload):
    """Validate the sanitized intent exactly as the publication client recorded it."""
    plan.object_fields(payload, INTENT_FIELDS, 'invalid_recovery_record')
    require(payload['schema_version'] == 1 and payload['kind'] == 'static-publication-intent',
            'invalid_recovery_record')
    for name, length in [('identity', 32), ('commit', 40), ('descriptor_sha256', 64),
                         ('runtime_sha256', 64)]:
        plan.hex_value(payload[name], length, 'invalid_recovery_record')
    for name in ['workflow_run_id', 'workflow_run_attempt', 'candidate_run_id', 'candidate_run_attempt']:
        plan.integer(payload[name], 1, 2**63 - 1, 'invalid_recovery_record')
    require(type(payload['artifacts']) is dict, 'invalid_recovery_record')
    predecessor = payload['predecessor']
    plan.object_fields(predecessor, {'baseline_commit', 'baseline_manifest_sha256', 'generation'},
                       'invalid_recovery_record')
    plan.hex_value(predecessor['baseline_commit'], 40, 'invalid_recovery_record')
    plan.hex_value(predecessor['baseline_manifest_sha256'], 64, 'invalid_recovery_record')
    plan.integer(predecessor['generation'], 1, 2**63 - 3, 'invalid_recovery_record')
    return payload


def inspect(remote, payload):
    """Return the retained attempt report, or None when its identity never staged.

    An absent retained attempt means preparation never reached the endpoint. The
    endpoint must then still be closed at exactly the recorded predecessor and
    generation; any other state is not adopted or repaired here.
    """
    try:
        return remote.status(payload['identity'], payload['descriptor_sha256'])
    except RemoteFailure as error:
        if error.code != 'remote_stale_attempt':
            raise
    current = remote.status()
    require(current['phase'] in publication.CLOSED
            and publication.baseline_matches(current, payload['predecessor'])
            and current['generation'] == payload['predecessor']['generation'], 'rollback_unconfirmed')
    return None


def complete_maintenance(remote, payload):
    """Finish bounded retention bookkeeping when an earlier maintain was interrupted."""
    report = remote.status(payload['identity'], payload['descriptor_sha256'])
    require(report['phase'] in {'verified', 'rolled_back'}, 'unfinished_publication')
    if not report['maintenance_pending']:
        return report
    ticket = report['ticket']
    try:
        maintained = remote.operation('maintain', ticket)
    except RemoteFailure:
        remote.status(payload['identity'], payload['descriptor_sha256'])
        maintained = remote.operation('maintain', ticket)
    require(maintained['phase'] == report['phase'] and maintained['maintenance_pending'] is False
            and type(maintained.get('retained_attempts')) is int and maintained['retained_attempts'] == 1,
            'maintenance_unconfirmed')
    return maintained


def reconcile_attempt(remote, payload, report):
    """Bring one non-terminal retained attempt to a terminal rollback phase.

    The sequence mirrors the publication client's own recovery: reconcile the
    journal, roll back the attempt, and observe rather than repeat a lost
    rollback response. Operator-modified files refuse to roll back server-side
    and leave the record blocked for explicit operator treatment.
    """
    ticket = report['ticket']
    require(ticket['generation'] == payload['predecessor']['generation'], 'remote_identity_mismatch')
    result = remote.operation('reconcile', ticket)
    if result['phase'] != 'aborted':
        try:
            result = remote.operation('rollback', ticket)
        except RemoteFailure:
            result = remote.status(payload['identity'], payload['descriptor_sha256'])
    require(result['phase'] in TERMINAL_ROLLBACK
            and publication.baseline_matches(result, payload['predecessor']), 'rollback_unconfirmed')
    return result


def recover(payload, number, github, remote, http, edge, deployments, loader, api_sha):
    """Reconcile the original attempt under its recorded identity and close the record."""
    require(payload['runtime_sha256'] == remote.runtime, 'runtime_changed')
    report = inspect(remote, payload)
    if report is not None and report['phase'] not in TERMINAL_ROLLBACK | {'verified'}:
        report = reconcile_attempt(remote, payload, report)
    if report is not None and report['phase'] == 'verified':
        # Live acceptance already passed; only maintenance or the record itself
        # failed to close. Complete bookkeeping, re-verify the recorded candidate
        # bytes live, then close the original record as success.
        complete_maintenance(remote, payload)
        product = loader(payload['commit'])
        require(product.sha == payload['commit']
                and digest(product.descriptor) == payload['descriptor_sha256'], 'artifact_identity_mismatch')
        edge_before = edge.observe(product.files['.well-known/security.txt'])
        live = http.verify(product.files, edge_before, api_sha)
        require(edge.observe(product.files['.well-known/security.txt']) == edge_before,
                'provider_configuration_changed')
        outcome, attempt = 'success', 'verified'
    else:
        if report is not None:
            require(publication.baseline_matches(report, payload['predecessor']), 'rollback_unconfirmed')
            complete_maintenance(remote, payload)
        access = historical_access(github, payload['predecessor']['baseline_commit'])
        files = http.predecessor(payload['predecessor'], access)
        edge_before = edge.observe(files['.well-known/security.txt'])
        # The interrupted publication may have died before its own purge.
        edge.purge()
        live = http.verify(files, edge_before, api_sha, historical=True)
        require(edge.observe(files['.well-known/security.txt']) == edge_before,
                'provider_configuration_changed')
        outcome = 'rolled_back'
        attempt = 'absent' if report is None else report['phase']
    deployments.finish(number, outcome)
    return {'outcome': outcome, 'attempt': attempt, 'live': live, 'recovery_verified': True}


def run(sha, folder, environ):
    """Load the blocked record through trusted workflow access and recover it."""
    run_ids = context(sha, environ)
    github = source.GitHub(environ)
    deployments = Deployments(environ)
    number, payload = deployments.unresolved()
    intent(payload)
    remote = SSH(environ, folder)
    http, edge = HTTP(environ['STATIC_ORIGIN_IP']), Cloudflare(environ)
    api_sha = http.api(source.artifact.commit(environ.get('STATIC_API_RELEASE_SHA')))

    def loader(commit):
        return source.consume(github, commit, folder)

    result = {'schema_version': 1, 'kind': 'static-publication-recovery', **run_ids,
              'deployment': number, 'commit': payload['commit']}
    return {**result, **recover(payload, number, github, remote, http, edge, deployments, loader, api_sha)}
