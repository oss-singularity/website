"""Pure, bounded planning for a version-bound Commons code transition.

Trusted callers independently select a verified candidate digest, known baseline
and target policy, then capture a fresh complete observation without races. This
module cannot establish those prerequisites, semantic compatibility or authority.
It performs no I/O, SQL, code execution, locking or provider operations. Plans
contain private target identifiers; they are not public release receipts.
"""
import json
import re

from commons_artifact import (MODULES, RUNTIME, commit, digest, encode, hash_value,
                              require, schema_hash, unpack)
from commons_rehearsal import SCHEMA_SHA256

TARGET = 'oss-commons'
SCRIPT = 'oss-singularity-commons'
ORIGIN = 'https://oss-singularity.io'
ROUTE = 'oss-singularity.io/api/*'
CRON = '17 * * * *'
MAX_INPUT = 512 * 1024
MAX_NODES = 4096
POLICY_FIELDS = {'schema_version', 'target', 'account_id', 'zone_id',
                 'script_name', 'database_id', 'route_id'}
BASELINE_FIELDS = {'schema_version', 'target', 'generation', 'commit',
                   'packet_sha256', 'policy_sha256', 'observation_sha256',
                   'version_id', 'deployment_id'}
OBSERVATION_FIELDS = {'schema_version', 'target', 'account_id', 'zone_id',
                      'script_name', 'deployment', 'version', 'latest_version_id',
                      'settings', 'routes', 'schedules', 'subdomain', 'modules', 'schema'}
PENDING = ['fresh-canonical-candidate-and-checks', 'application-and-rollback-compatibility',
           'scoped-provider-access', 'opaque-secret-preservation',
           'serialized-promotion', 'durable-staging-and-deployment-receipts',
           'fresh-preconditions-before-each-write', 'uncertain-outcome-reconciliation',
           'live-acceptance-and-conditional-rollback']


def bounded(value):
    """Reject cycles, excessive depth/size and non-JSON types before serialization."""
    nodes, text_bytes = 0, 0
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        nodes += 1
        require(nodes <= MAX_NODES and depth <= 12, 'input_limit')
        kind = type(item)
        require(kind in (dict, list, str, int, bool, type(None)), 'invalid_input')
        if kind is dict:
            require(len(item) <= 256 and all(type(key) is str for key in item), 'input_limit')
            pending.extend((part, depth + 1) for pair in item.items() for part in pair)
        elif kind is list:
            require(len(item) <= 256, 'input_limit')
            pending.extend((part, depth + 1) for part in item)
        elif kind is str:
            try:
                text_bytes += len(item.encode('utf-8'))
            except UnicodeError:
                require(False, 'invalid_input')
            require(text_bytes <= MAX_INPUT, 'input_limit')
        elif kind is int:
            require(-(2**63) <= item < 2**63, 'input_limit')
    require(len(encode(value)) <= MAX_INPUT, 'input_limit')


def fields(value, expected, code):
    require(type(value) is dict and set(value) == expected, code)


def same(value, expected, code):
    # Canonical JSON also distinguishes booleans from integers (False != 0).
    require(encode(value) == encode(expected), code)


def uuid(value, code):
    require(type(value) is str and re.fullmatch(
        r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', value) is not None, code)


def profile(value, code):
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['target'] == TARGET, code)


def validate_policy(value):
    bounded(value)
    fields(value, POLICY_FIELDS, 'invalid_policy')
    profile(value, 'invalid_policy')
    for name in ('account_id', 'zone_id', 'route_id'):
        require(type(value[name]) is str and re.fullmatch(r'[a-f0-9]{32}', value[name]), 'invalid_policy')
    uuid(value['database_id'], 'invalid_policy')
    require(value['script_name'] == SCRIPT, 'invalid_policy')


def validate_baseline(value):
    bounded(value)
    fields(value, BASELINE_FIELDS, 'invalid_baseline')
    profile(value, 'invalid_baseline')
    require(type(value['generation']) is int and 1 <= value['generation'] < 2**63 - 1, 'invalid_baseline')
    commit(value['commit'])
    for name in ('packet_sha256', 'policy_sha256', 'observation_sha256'):
        hash_value(value[name])
    for name in ('version_id', 'deployment_id'):
        uuid(value[name], 'invalid_baseline')


def settings(policy, sha):
    """Profile 1's complete normalized configuration; secret values are opaque."""
    return {
        'bindings': {
            'DB': {'type': 'd1', 'id': policy['database_id']},
            'PUBLIC_ORIGIN': {'type': 'plain_text', 'text': ORIGIN},
            'RELEASE_SHA': {'type': 'plain_text', 'text': sha},
            'ADMIN_TOKEN': {'type': 'secret_text'},
            'IP_HMAC_SECRET': {'type': 'secret_text'},
        },
        'compatibility_date': RUNTIME['compatibility_date'], 'compatibility_flags': [],
        'usage_model': 'standard', 'logpush': False, 'observability': {'enabled': False},
        'placement': {}, 'tags': ['commons', 'oss-singularity'], 'tail_consumers': [],
    }


def observed_state(observation, policy, predecessor_descriptor):
    """Validate normalized metadata and replace DDL text with its fingerprint.

    A trusted observer must reject unknown provider fields before normalizing;
    complete settings/version/route inventories cannot be inferred here. This
    helper may establish an initial baseline only in a separate operator step,
    never by adopting a failing fresh observation during promotion.
    """
    validate_policy(policy)
    bounded(observation)
    fields(observation, OBSERVATION_FIELDS, 'invalid_observation')
    profile(observation, 'invalid_observation')
    for name in ('account_id', 'zone_id', 'script_name'):
        require(observation[name] == policy[name], 'target_mismatch')
    deployment, version = observation['deployment'], observation['version']
    fields(deployment, {'id', 'strategy', 'versions'}, 'invalid_deployment')
    uuid(deployment['id'], 'invalid_deployment')
    fields(version, {'id', 'etag', 'bindings', 'runtime'}, 'invalid_version')
    uuid(version['id'], 'invalid_version')
    hash_value(version['etag'])
    same(deployment['versions'], [{'version_id': version['id'], 'percentage': 100}], 'partial_deployment')
    require(deployment['strategy'] == 'percentage', 'invalid_deployment')
    require(observation['latest_version_id'] == version['id'], 'unowned_pending_version')
    expected = settings(policy, predecessor_descriptor['commit'])
    same(observation['settings'], expected, 'settings_drift')
    same(version['bindings'], expected['bindings'], 'version_settings_mismatch')
    same(version['runtime'], {key: expected[key] for key in
                             ('compatibility_date', 'compatibility_flags', 'usage_model')},
         'version_settings_mismatch')
    same(observation['routes'], [{'id': policy['route_id'], 'pattern': ROUTE,
                                 'script': SCRIPT, 'request_limit_fail_open': False}], 'route_drift')
    same(observation['schedules'], [CRON], 'schedule_drift')
    same(observation['subdomain'], {'enabled': False, 'previews_enabled': False}, 'subdomain_drift')
    same(observation['modules'], predecessor_descriptor['modules'], 'installed_code_mismatch')
    installed_schema = schema_hash(observation['schema'])
    require(installed_schema == SCHEMA_SHA256, 'installed_schema_mismatch')
    # New object graph; callers cannot mutate the observation through the plan.
    return json.loads(encode({**observation, 'schema': {'profile': 1, 'sha256': installed_schema}}))


def build_plan(*, candidate_packet, expected_candidate_commit, expected_candidate_packet_sha256,
               predecessor_packet, baseline, observation, target_policy):
    """Return a deterministic plan. Independently selected pins are mandatory.

    A caller must not recompute the baseline from a changed observation or accept
    a candidate-supplied digest as the expected identity. The result describes a
    possible transition, not a provider request or evidence that it is safe to run.
    """
    validate_baseline(baseline)
    validate_policy(target_policy)
    candidate_files, candidate = unpack(candidate_packet, expected_candidate_commit, SCHEMA_SHA256)
    hash_value(expected_candidate_packet_sha256)
    require(digest(candidate_packet) == expected_candidate_packet_sha256, 'candidate_packet_mismatch')
    _old_files, predecessor = unpack(predecessor_packet, baseline['commit'], SCHEMA_SHA256)
    require(digest(predecessor_packet) == baseline['packet_sha256'], 'predecessor_packet_mismatch')
    require(candidate['commit'] != predecessor['commit'], 'candidate_commit_reused')
    require(digest(encode(target_policy)) == baseline['policy_sha256'], 'baseline_policy_mismatch')
    state = observed_state(observation, target_policy, predecessor)
    require(digest(encode(state)) == baseline['observation_sha256']
            and state['version']['id'] == baseline['version_id']
            and state['deployment']['id'] == baseline['deployment_id'], 'baseline_observation_mismatch')
    changes = [{'name': name,
                'operation': 'keep' if candidate['modules'][name] == predecessor['modules'][name] else 'replace',
                'before': predecessor['modules'][name], 'after': candidate['modules'][name]}
               for name in sorted(candidate_files)]
    require({item['name'] for item in changes} == MODULES, 'module_allowlist_mismatch')
    desired_settings = settings(target_policy, candidate['commit'])
    desired_version = {'modules': candidate['modules'], 'runtime': state['version']['runtime'],
                       'bindings': desired_settings['bindings']}
    plan = {
        'schema_version': 1, 'kind': 'commons-code-plan', 'target': TARGET,
        'plan_only': True, 'deployment_authorized': False,
        'expected_generation': baseline['generation'],
        'baseline_sha256': digest(encode(baseline)), 'policy_sha256': baseline['policy_sha256'],
        'observation_sha256': baseline['observation_sha256'], 'target_policy': target_policy,
        'candidate': {**candidate, 'packet_sha256': expected_candidate_packet_sha256},
        'predecessor': {**predecessor, 'packet_sha256': baseline['packet_sha256'],
                        'version_id': state['version']['id'], 'etag': state['version']['etag'],
                        'deployment_id': state['deployment']['id']},
        'module_changes': changes,
        'release_sha_change': {'before': predecessor['commit'], 'after': candidate['commit']},
        'desired_version': desired_version,
        'preserved': {'settings_except_release_sha': {**desired_settings, 'bindings': {
            name: value for name, value in desired_settings['bindings'].items() if name != 'RELEASE_SHA'}},
                     'routes': state['routes'], 'schedules': state['schedules'],
                     'subdomain': state['subdomain'], 'schema': state['schema']},
        'activation': {'strategy': 'percentage', 'percentage': 100, 'staged_version_id': None,
                       'expected_predecessor_deployment_id': state['deployment']['id'],
                       'requires_verified_staging_receipt': True},
        'rollback': {'kind': 'conditional-code-only', 'version_id': state['version']['id'],
                     'requires_same_attempt_forward_receipt': True,
                     'requires_fresh_candidate_and_preserved_state_match': True,
                     'requires_compatible_current_schema_and_data': True,
                     'database_restore': False},
        'pending_gates': list(PENDING),
    }
    return json.loads(encode({**plan, 'plan_sha256': digest(encode(plan))}))
