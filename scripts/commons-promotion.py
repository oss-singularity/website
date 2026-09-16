"""Fixed operator command for one Commons worker promotion (procedure steps 3-7).

Derives everything the engine needs from the live provider state — the
installed bindings become inherit entries, the compatibility date, predecessor
version and current release identity are read from the active version — and
takes only the candidate packet (the provider's own multipart form), its commit
and the account identifiers from the environment. With --from-rehearsal the
command also consumes the canonical rehearsal candidate (procedure steps 1-2)
through the implemented contracts and plans against the live predecessor
before promoting. It deliberately performs no provider mutation outside the
engine and no candidate download outside this wiring.

Environment: COMMONS_CF_TOKEN (provider token), COMMONS_ACCOUNT_ID,
COMMONS_ZONE_ID, COMMONS_D1_ID, STATIC_ORIGIN_IP (live acceptance), and for
--from-rehearsal additionally GH_TOKEN (artifact transport and provenance
reads). Account identifiers stay outside the repository by the hosting
boundary.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile

import commons_artifact as artifact
import commons_candidate as consumer
import commons_cloudflare as adapter
import commons_plan as planner
import commons_promotion as engine
import commons_rehearsal as rehearsal
from release_deployment import Deployments
from release_http import HTTP
from release_source import token, write_private
from site_artifact import ArtifactError, require

SCRIPT_NAME = 'oss-singularity-commons'
SOURCE = Path(__file__).resolve().parent.parent / 'services/commons'

KNOWN_ERRORS = {
    'invalid_arguments', 'invalid_candidate', 'provider_state_unverified',
    'unfinished_promotion', 'promotion_record_closed', 'invalid_promotion_record',
    'promotion_record_unconfirmed', 'promotion_history_unverified',
    'promotion_unresolved', 'invalid_promotion_outcome', 'invalid_plan',
    'staged_version_unverified', 'staged_bindings_changed', 'staged_modules_changed',
    'staged_settings_changed', 'invalid_bindings', 'provider_request_failed',
    'ambiguous_staged_version', 'live_acceptance_failed', 'invalid_bindings',
    'missing_credential', 'invalid_origin', 'http_transport_failed', 'api_unverified',
    'deployment_record_unconfirmed', 'invalid_deployment_route',
}
CONSUMPTION_ERRORS = {
    'invalid_rehearsal_artifacts', 'artifact_transport_failed', 'github_read_failed',
    'stale_main', 'run_identity_mismatch', 'run_not_successful', 'ambiguous_workflow_runs',
    'state_changed', 'artifact_identity_mismatch', 'unsuccessful_check', 'partial_attempt',
    'check_mismatch', 'invalid_receipt', 'source_identity_mismatch', 'invalid_identity',
    'source_module_allowlist_mismatch', 'untrusted_local_contract', 'rebuild_mismatch',
    'schema_profile_changed', 'invalid_policy', 'invalid_baseline', 'invalid_observation',
    'target_mismatch', 'invalid_deployment', 'partial_deployment', 'unowned_pending_version',
    'settings_drift', 'version_settings_mismatch', 'route_drift', 'schedule_drift',
    'subdomain_drift', 'installed_code_mismatch', 'installed_schema_mismatch',
    'baseline_observation_mismatch', 'baseline_policy_mismatch', 'candidate_packet_mismatch',
    'predecessor_packet_mismatch', 'candidate_commit_reused', 'input_limit',
}


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ArtifactError('invalid_arguments')


def packet_modules(content):
    """Return the module names of a multipart candidate packet, in order."""
    lines = content.split(b'\r\n', 1)
    require(len(lines) == 2 and lines[0].startswith(b'--'), 'invalid_candidate')
    names = []
    for part in content.split(b'--' + lines[0][2:])[1:-1]:
        head = part.partition(b'\r\n\r\n')[0].decode('utf-8', 'replace')
        if 'name="metadata"' in head:
            continue
        found = head.split('name="', 1)[1].split('"', 1)[0] if 'name="' in head else None
        require(found is not None, 'invalid_candidate')
        names.append(found)
    require(0 < len(names) <= 32 and len(set(names)) == len(names), 'invalid_candidate')
    return names


def derive_plan(observation, active_detail, message, tag):
    """Build the engine plan from live state: inherit bindings, current identity."""
    require(type(observation) is dict and observation.get('active_version'),
            'provider_state_unverified')
    detail = active_detail
    resources = detail.get('resources')
    require(type(resources) is dict, 'provider_state_unverified')
    installed = resources.get('bindings')
    engine.binding_fingerprint(installed)
    runtime = resources.get('script_runtime')
    require(type(runtime) is dict and type(runtime.get('compatibility_date')) is str,
            'provider_state_unverified')
    release_sha = None
    for item in installed:
        if item.get('name') == 'RELEASE_SHA':
            release_sha = item.get('text')
    require(type(release_sha) is str and len(release_sha) == 40, 'provider_state_unverified')
    return {
        'predecessor_version': observation['active_version'], 'release_sha': release_sha,
        'message': message, 'tag': tag,
        'bindings': [{'name': item['name'], 'type': 'inherit'} for item in installed],
        'installed_bindings': installed, 'main_module': None,
        'compatibility_date': runtime['compatibility_date'],
        'plan_sha256': None,
    }


def consume_and_plan(commit, run_id, attempt, message, tag, environ, provider, github,
                     fetch=None):
    """Consume the canonical rehearsal candidate (steps 1-2) and plan against live state.

    Locates the rehearsal's packet and receipt by their exact names, downloads
    both through the repository's bounded transport, verifies them with the
    implemented candidate consumer against the checkout's own source, rebuilds
    the predecessor packet from the live script bytes, composes the planner
    observation and baseline, and returns the verified candidate plus the
    engine plan mapped from the planner's authoritative output.
    """
    commit = artifact.commit(commit)
    ids = engine.rehearsal_artifacts(github, commit, run_id, attempt)
    with tempfile.TemporaryDirectory(prefix='oss-commons-promotion-') as folder:
        root = Path(folder)
        raws = {}
        for role, limit in [('candidate', artifact.MAX_PACKET + consumer.candidate.MAX_CENTRAL + 1024),
                            ('receipt', consumer.MAX_RECEIPT + consumer.candidate.MAX_CENTRAL + 1024)]:
            raw = github.download(ids[role], limit)
            write_private(root / (role + '.zip'), raw)
            raws[role] = raw
        report = consumer.verify(commit, str(run_id), str(attempt), str(ids['candidate']),
                                 root / 'candidate.zip', str(ids['receipt']), root / 'receipt.zip',
                                 SOURCE, root / 'candidate-verification.json', environ, fetch)
        packet = consumer.archive_member(raws['candidate'], 'commons.json', artifact.MAX_PACKET)
        require(artifact.digest(raws['candidate']) == report['artifacts']['candidate']['digest_sha256']
                and artifact.digest(packet) == report['packet_sha256'], 'artifact_identity_mismatch')
        live = provider.observe()
        require(type(live) is dict and type(live.get('active_version')) is str,
                'provider_state_unverified')
        active = live['active_version']
        detail = provider.version_detail(active)
        installed = detail.get('resources', {}).get('bindings') if type(detail) is dict else None
        require(type(installed) is list, 'provider_state_unverified')
        release_shas = [item.get('text') for item in installed
                        if type(item) is dict and item.get('name') == 'RELEASE_SHA']
        require(len(release_shas) == 1 and type(release_shas[0]) is str, 'provider_state_unverified')
        release_sha = release_shas[0]
        predecessor_packet = engine.reconstruct_predecessor(provider.script_content(), release_sha)
        _files, descriptor = artifact.unpack(predecessor_packet, release_sha, rehearsal.SCHEMA_SHA256)
        captured = engine.capture_observation(provider, descriptor, artifact.SCHEMA_QUERY)
        matching = [route for route in captured['observation']['routes']
                    if route.get('script') == SCRIPT_NAME and route.get('pattern') == planner.ROUTE]
        require(len(matching) == 1, 'provider_state_unverified')
        database_ids = [binding.get('id') for name, binding
                        in captured['observation']['settings']['bindings'].items()
                        if name == 'DB']
        require(len(database_ids) == 1, 'provider_state_unverified')
        policy = {'schema_version': 1, 'target': planner.TARGET,
                  'account_id': captured['observation']['account_id'],
                  'zone_id': captured['observation']['zone_id'], 'script_name': SCRIPT_NAME,
                  'database_id': database_ids[0], 'route_id': matching[0]['id']}
        planner.validate_policy(policy)
        baseline = engine.plan_baseline(captured['generation'], release_sha, predecessor_packet,
                                        descriptor, policy, captured['observation'])
        planned = planner.build_plan(candidate_packet=packet, expected_candidate_commit=commit,
                                     expected_candidate_packet_sha256=artifact.digest(packet),
                                     predecessor_packet=predecessor_packet, baseline=baseline,
                                     observation=captured['observation'], target_policy=policy)
        plan = engine.engine_plan(commit, planned, message, tag)
        return {'commit': commit, 'content': packet,
                'modules': sorted(report['descriptor']['modules'])}, plan


def main(argv=None, environ=None, github=None, fetch=None, provider=None, intent=None,
         accept=None):
    environ = os.environ if environ is None else environ
    parser = Parser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--packet', help='Path to the candidate packet (multipart)')
    group.add_argument('--from-rehearsal', nargs=3, metavar=('RUN_ID', 'ATTEMPT', 'COMMIT'),
                       help='Consume the canonical rehearsal candidate and plan against live state')
    parser.add_argument('--commit', help='Candidate commit (with --packet)')
    parser.add_argument('--message', required=True)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args(argv)
    account_id = token(environ, 'COMMONS_ACCOUNT_ID')
    zone_id = token(environ, 'COMMONS_ZONE_ID')
    d1_id = token(environ, 'COMMONS_D1_ID')
    cf_token = token(environ, 'COMMONS_CF_TOKEN')
    origin_ip = token(environ, 'STATIC_ORIGIN_IP')
    a = provider if provider is not None else adapter.CloudflareAdapter(
        token=cf_token, account_id=account_id, zone_id=zone_id, script_name=SCRIPT_NAME,
        d1_uuid=d1_id)
    if args.from_rehearsal is not None:
        run_id = rehearsal.shared.decimal(args.from_rehearsal[0])
        attempt = rehearsal.shared.decimal(args.from_rehearsal[1])
        transport = github if github is not None else consumer.CommonsGitHub(environ)
        candidate, plan = consume_and_plan(args.from_rehearsal[2], run_id, attempt,
                                           args.message, args.tag, environ, a, transport, fetch)
    else:
        require(type(args.commit) is str, 'invalid_arguments')
        content = Path(args.packet).read_bytes()
        candidate = {'commit': artifact.commit(args.commit), 'content': content,
                     'modules': packet_modules(content)}
        observation = a.observe()
        plan = derive_plan(observation, a.version_detail(observation['active_version']),
                           args.message, args.tag)
        plan['main_module'] = 'worker.mjs'
    http = HTTP(origin_ip)
    records = intent if intent is not None else engine.PromotionIntent(environ)
    return engine.promote(a, records, plan, candidate,
                          accept if accept is not None else (lambda sha: http.api(sha) == sha))


def cli(argv=None, environ=None):
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
            result = main(argv, environ)
    except SystemExit as error:
        if error.code == 0:
            print(buffer.getvalue(), end='')
            return 0
        result = {'error': 'invalid_arguments', 'promoted': False}
    except ArtifactError as error:
        allowed = KNOWN_ERRORS | CONSUMPTION_ERRORS
        result = {'error': error.code if error.code in allowed else 'promotion_failed',
                  'promoted': False}
    except Exception:
        result = {'error': 'promotion_failed', 'promoted': False}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    raise SystemExit(cli())
