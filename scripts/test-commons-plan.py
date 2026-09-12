"""Offline adversarial checks using real Commons packets and synthetic targets."""
import copy
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import commons_artifact as artifact
import commons_plan as planner
from site_artifact import ArtifactError

OLD = '1' * 40
NEW = '2' * 40
VERSION = '11111111-1111-4111-8111-111111111111'
DEPLOYMENT = '22222222-2222-4222-8222-222222222222'
OTHER_VERSION = '33333333-3333-4333-8333-333333333333'
DATABASE = '44444444-4444-4444-8444-444444444444'


def fixture():
    files, migrations = artifact.source_inputs(Path(__file__).resolve().parents[1] / 'services/commons')
    schema = artifact.expected_schema(migrations)
    # Already pinned by expected_schema; only synthetic in-memory state follows.
    with sqlite3.connect(':memory:') as database:
        database.row_factory = sqlite3.Row
        for name in sorted(migrations):
            database.executescript(migrations[name].decode())
        rows = [dict(row) for row in database.execute(artifact.SCHEMA_QUERY)]
    previous = artifact.packet(files, OLD, schema)
    changed = {**files, 'worker.mjs': files['worker.mjs'] + b'\n// synthetic candidate\n'}
    candidate = artifact.packet(changed, NEW, schema)
    _files, old = artifact.unpack(previous, OLD, schema)
    policy = {'schema_version': 1, 'target': planner.TARGET, 'account_id': 'a' * 32,
              'zone_id': 'b' * 32, 'script_name': planner.SCRIPT,
              'database_id': DATABASE, 'route_id': 'c' * 32}
    settings = {
        'bindings': {'DB': {'type': 'd1', 'id': DATABASE},
                     'PUBLIC_ORIGIN': {'type': 'plain_text', 'text': 'https://oss-singularity.io'},
                     'RELEASE_SHA': {'type': 'plain_text', 'text': OLD},
                     'ADMIN_TOKEN': {'type': 'secret_text'}, 'IP_HMAC_SECRET': {'type': 'secret_text'}},
        'compatibility_date': '2026-09-04', 'compatibility_flags': [], 'usage_model': 'standard',
        'logpush': False, 'observability': {'enabled': False}, 'placement': {},
        'tags': ['commons', 'oss-singularity'], 'tail_consumers': [],
    }
    observation = {
        'schema_version': 1, 'target': planner.TARGET,
        **{key: policy[key] for key in ('account_id', 'zone_id', 'script_name')},
        'deployment': {'id': DEPLOYMENT, 'strategy': 'percentage',
                       'versions': [{'version_id': VERSION, 'percentage': 100}]},
        'version': {'id': VERSION, 'etag': 'd' * 64, 'bindings': copy.deepcopy(settings['bindings']),
                    'runtime': {'compatibility_date': '2026-09-04', 'compatibility_flags': [],
                                'usage_model': 'standard'}},
        'latest_version_id': VERSION, 'settings': settings,
        'routes': [{'id': 'c' * 32, 'pattern': 'oss-singularity.io/api/*',
                    'script': 'oss-singularity-commons', 'request_limit_fail_open': False}],
        'schedules': ['17 * * * *'], 'subdomain': {'enabled': False, 'previews_enabled': False},
        'modules': old['modules'], 'schema': rows,
    }
    # Initial fixture checkpoint is independent of subsequent candidate mutations.
    state = {**observation, 'schema': {'profile': 1, 'sha256': schema}}
    baseline = {'schema_version': 1, 'target': planner.TARGET, 'generation': 7, 'commit': OLD,
                'packet_sha256': artifact.digest(previous), 'policy_sha256': artifact.digest(artifact.encode(policy)),
                'observation_sha256': artifact.digest(artifact.encode(state)),
                'version_id': VERSION, 'deployment_id': DEPLOYMENT}
    return {'candidate_packet': candidate, 'expected_candidate_commit': NEW,
            'expected_candidate_packet_sha256': artifact.digest(candidate),
            'predecessor_packet': previous, 'baseline': baseline,
            'observation': observation, 'target_policy': policy}


class PlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.initial = fixture()

    def setUp(self):
        self.case = copy.deepcopy(self.initial)

    def reject(self, code=None):
        with self.assertRaises(ArtifactError) as failure:
            planner.build_plan(**self.case)
        if code:
            self.assertEqual(failure.exception.code, code)

    def test_exact_code_and_release_identity_transition(self):
        result = planner.build_plan(**self.case)
        changes = {item['name']: item for item in result['module_changes']}
        self.assertEqual(set(changes), artifact.MODULES)
        self.assertEqual([name for name, item in changes.items() if item['operation'] == 'replace'], ['worker.mjs'])
        self.assertEqual(sum(item['operation'] == 'keep' for item in changes.values()), 5)
        self.assertNotEqual(changes['worker.mjs']['before'], changes['worker.mjs']['after'])
        self.assertEqual(result['release_sha_change'], {'before': OLD, 'after': NEW})
        self.assertEqual(result['desired_version']['bindings']['RELEASE_SHA']['text'], NEW)
        self.assertEqual(result['predecessor']['version_id'], VERSION)
        self.assertEqual(result['predecessor']['deployment_id'], DEPLOYMENT)
        self.assertEqual(result['expected_generation'], 7)
        self.assertIsNone(result['activation']['staged_version_id'])
        self.assertTrue(result['activation']['requires_verified_staging_receipt'])
        self.assertTrue(result['plan_only'])
        self.assertFalse(result['deployment_authorized'])

    def test_every_preserved_binding_and_setting(self):
        result = planner.build_plan(**self.case)
        preserved = result['preserved']
        expected = copy.deepcopy(self.case['observation']['settings'])
        del expected['bindings']['RELEASE_SHA']
        self.assertEqual(preserved['settings_except_release_sha'], expected)
        for name in ('routes', 'schedules', 'subdomain'):
            self.assertEqual(preserved[name], self.case['observation'][name])
        self.assertEqual(preserved['schema'], {'profile': 1, 'sha256': planner.SCHEMA_SHA256})
        self.assertEqual(result['desired_version']['runtime'], self.case['observation']['version']['runtime'])
        for name in expected['bindings']:
            self.assertEqual(result['desired_version']['bindings'][name], expected['bindings'][name])

    def test_rollback_retains_version_without_claiming_data_compatibility(self):
        result = planner.build_plan(**self.case)
        self.assertEqual(result['rollback'], {
            'kind': 'conditional-code-only', 'version_id': VERSION,
            'requires_same_attempt_forward_receipt': True,
            'requires_fresh_candidate_and_preserved_state_match': True,
            'requires_compatible_current_schema_and_data': True, 'database_restore': False})
        for gate in ('application-and-rollback-compatibility', 'opaque-secret-preservation',
                     'durable-staging-and-deployment-receipts', 'uncertain-outcome-reconciliation'):
            self.assertIn(gate, result['pending_gates'])

    def test_reproducible_digest_and_equivalent_schema_formatting(self):
        first = planner.build_plan(**self.case)
        self.case['observation']['schema'].reverse()
        self.case['observation']['schema'][0]['sql'] += '\n -- ignored formatting\n'
        self.case['target_policy'] = dict(reversed(list(self.case['target_policy'].items())))
        self.assertEqual(first, planner.build_plan(**self.case))
        body = {key: value for key, value in first.items() if key != 'plan_sha256'}
        self.assertEqual(first['plan_sha256'], artifact.digest(artifact.encode(body)))

    def test_no_io_execution_or_sql_and_no_aliases(self):
        saved = copy.deepcopy(self.case)
        def forbidden(*_args, **_kwargs):
            raise AssertionError('planner attempted I/O or execution')
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name in ('builtins.open', 'os.open', 'os.stat', 'os.mkdir', 'os.replace', 'os.remove',
                         'os.system', 'subprocess.Popen', 'socket.socket', 'sqlite3.connect',
                         'commons_artifact.expected_schema', 'commons_artifact.source_inputs'):
                stack.enter_context(patch(name, forbidden))
            result = planner.build_plan(**self.case)
        result['target_policy']['account_id'] = 'e' * 32
        result['desired_version']['bindings']['ADMIN_TOKEN']['type'] = 'changed'
        result['module_changes'][0]['before']['size'] = 1
        result['preserved']['routes'][0]['pattern'] = 'changed'
        self.assertEqual(self.case, saved)
        self.assertNotIn('text', result['preserved']['settings_except_release_sha']['bindings']['ADMIN_TOKEN'])

    def test_new_commit_with_identical_modules_is_metadata_only(self):
        files, _descriptor = artifact.unpack(self.case['predecessor_packet'], OLD, planner.SCHEMA_SHA256)
        raw = artifact.packet(files, NEW, planner.SCHEMA_SHA256)
        self.case.update(candidate_packet=raw, expected_candidate_packet_sha256=artifact.digest(raw))
        result = planner.build_plan(**self.case)
        self.assertTrue(all(item['operation'] == 'keep' for item in result['module_changes']))
        self.assertEqual(result['release_sha_change']['after'], NEW)

    def test_wrong_candidate_pin_and_different_commit(self):
        self.case['expected_candidate_packet_sha256'] = '0' * 64
        self.reject('candidate_packet_mismatch')
        self.case = copy.deepcopy(self.initial)
        self.case['expected_candidate_commit'] = '3' * 40
        self.reject('descriptor_mismatch')

    def test_predecessor_bytes_cannot_be_adopted_from_candidate(self):
        files, _descriptor = artifact.unpack(self.case['candidate_packet'], NEW, planner.SCHEMA_SHA256)
        self.case['predecessor_packet'] = artifact.packet(files, OLD, planner.SCHEMA_SHA256)
        self.reject('predecessor_packet_mismatch')

    def test_same_commit_rejected_even_for_different_code(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                files, _descriptor = artifact.unpack(self.initial['candidate_packet' if changed else 'predecessor_packet'],
                                                     NEW if changed else OLD, planner.SCHEMA_SHA256)
                raw = artifact.packet(files, OLD, planner.SCHEMA_SHA256)
                self.case.update(candidate_packet=raw, expected_candidate_commit=OLD,
                                 expected_candidate_packet_sha256=artifact.digest(raw))
                self.reject('candidate_commit_reused')

    def test_invalid_packets_runtime_and_schema(self):
        for which in ('candidate_packet', 'predecessor_packet'):
            for mutation in ('runtime', 'schema', 'extra', 'size', 'duplicate', 'oversized'):
                with self.subTest(which=which, mutation=mutation):
                    self.case = copy.deepcopy(self.initial)
                    value = json.loads(self.case[which])
                    if mutation == 'runtime':
                        value['descriptor']['runtime']['compatibility_flags'] = ['nodejs_compat']
                    elif mutation == 'schema':
                        value['descriptor']['schema']['sha256'] = '0' * 64
                    elif mutation == 'extra':
                        value['files']['extra.mjs'] = 'ZXh0cmE='
                    elif mutation == 'size':
                        value['descriptor']['modules']['worker.mjs']['size'] = True
                    raw = artifact.encode(value)
                    if mutation == 'duplicate':
                        raw = b'{"files":{},' + raw[1:]
                    elif mutation == 'oversized':
                        raw = b' ' * (artifact.MAX_PACKET + 1)
                    self.case[which] = raw
                    if which == 'candidate_packet':
                        self.case['expected_candidate_packet_sha256'] = artifact.digest(raw)
                    self.reject()

    def test_installed_module_drift_and_unknown_modules(self):
        for mutation in ('hash', 'size', 'missing', 'extra'):
            with self.subTest(mutation=mutation):
                self.case = copy.deepcopy(self.initial)
                modules = self.case['observation']['modules']
                if mutation == 'hash':
                    modules['worker.mjs']['sha256'] = 'e' * 64
                elif mutation == 'size':
                    modules['worker.mjs']['size'] += 1
                elif mutation == 'missing':
                    del modules['security.mjs']
                else:
                    modules['foreign.mjs'] = modules['worker.mjs']
                self.reject('installed_code_mismatch')

    def test_observed_schema_change(self):
        self.case['observation']['schema'].append({
            'type': 'table', 'name': 'unowned', 'tbl_name': 'unowned', 'sql': 'CREATE TABLE unowned (id TEXT)'})
        self.reject('installed_schema_mismatch')

    def test_target_policy_and_baseline_are_independent(self):
        self.case['target_policy']['account_id'] = 'e' * 32
        self.case['observation']['account_id'] = 'e' * 32
        self.reject('baseline_policy_mismatch')
        self.case = copy.deepcopy(self.initial)
        self.case['observation']['zone_id'] = 'e' * 32
        self.reject('target_mismatch')

    def test_missing_unknown_and_invalid_contract_fields(self):
        for name in ('baseline', 'target_policy', 'observation'):
            for mutation in ('missing', 'extra', 'boolean_profile', 'target'):
                with self.subTest(name=name, mutation=mutation):
                    self.case = copy.deepcopy(self.initial)
                    value = self.case[name]
                    if mutation == 'missing':
                        del value['schema_version']
                    elif mutation == 'extra':
                        value['provider_option'] = True
                    elif mutation == 'boolean_profile':
                        value['schema_version'] = True
                    else:
                        value['target'] = 'different-target'
                    self.reject()

    def test_generation_and_malformed_identities(self):
        for generation in (True, 0, -1, 2**63 - 1, '7'):
            with self.subTest(generation=generation):
                self.case = copy.deepcopy(self.initial)
                self.case['baseline']['generation'] = generation
                self.reject('invalid_baseline')
        self.case = copy.deepcopy(self.initial)
        self.case['target_policy']['route_id'] = '../route'
        self.reject('invalid_policy')

    def test_newer_pending_version_blocks(self):
        self.case['observation']['latest_version_id'] = OTHER_VERSION
        self.reject('unowned_pending_version')

    def test_partial_or_multiple_active_versions_block(self):
        for versions in ([], [{'version_id': VERSION, 'percentage': 99}],
                         [{'version_id': VERSION, 'percentage': True}],
                         [{'version_id': VERSION, 'percentage': '100'}],
                         [{'version_id': VERSION, 'percentage': 50},
                          {'version_id': OTHER_VERSION, 'percentage': 50}]):
            with self.subTest(versions=versions):
                self.case = copy.deepcopy(self.initial)
                self.case['observation']['deployment']['versions'] = versions
                self.reject('partial_deployment')

    def test_fresh_active_version_and_deployment_must_match_baseline(self):
        self.case['observation']['deployment']['id'] = OTHER_VERSION
        self.reject('baseline_observation_mismatch')
        self.case = copy.deepcopy(self.initial)
        state = self.case['observation']
        state['version']['id'] = OTHER_VERSION
        state['latest_version_id'] = OTHER_VERSION
        state['deployment']['versions'][0]['version_id'] = OTHER_VERSION
        self.reject('baseline_observation_mismatch')

    def test_etag_and_baseline_identity_mismatch(self):
        for name in ('version_id', 'deployment_id', 'observation_sha256'):
            with self.subTest(name=name):
                self.case = copy.deepcopy(self.initial)
                self.case['baseline'][name] = 'f' * 64 if name.endswith('sha256') else OTHER_VERSION
                self.reject('baseline_observation_mismatch')
        self.case = copy.deepcopy(self.initial)
        self.case['observation']['version']['etag'] = 'e' * 64
        self.reject('baseline_observation_mismatch')

    def test_bindings_do_not_accept_additions_secrets_or_foreign_database(self):
        for mutation in ('secret', 'new_binding', 'database', 'origin', 'release', 'missing'):
            with self.subTest(mutation=mutation):
                self.case = copy.deepcopy(self.initial)
                bindings = self.case['observation']['settings']['bindings']
                if mutation == 'secret':
                    bindings['ADMIN_TOKEN']['text'] = 'PRIVATE_FIXTURE_MARKER'
                elif mutation == 'new_binding':
                    bindings['OTHER'] = {'type': 'plain_text', 'text': 'unowned'}
                elif mutation == 'database':
                    bindings['DB']['id'] = OTHER_VERSION
                elif mutation == 'origin':
                    bindings['PUBLIC_ORIGIN']['text'] = 'https://example.invalid'
                elif mutation == 'release':
                    bindings['RELEASE_SHA']['text'] = NEW
                else:
                    del bindings['IP_HMAC_SECRET']
                self.reject('settings_drift')

    def test_complete_runtime_and_settings_profile(self):
        changes = {'compatibility_date': '2026-09-05', 'compatibility_flags': ['nodejs_compat'],
                   'usage_model': 'bundled', 'logpush': True, 'observability': {'enabled': True},
                   'placement': {'mode': 'smart'}, 'tail_consumers': [{'service': 'unowned'}],
                   'limits': {'cpu_ms': 30000}, 'tags': ['unowned']}
        for key, value in changes.items():
            with self.subTest(key=key):
                self.case = copy.deepcopy(self.initial)
                self.case['observation']['settings'][key] = value
                self.reject('settings_drift')

    def test_active_version_resources_must_match_settings(self):
        self.case['observation']['version']['bindings']['RELEASE_SHA']['text'] = NEW
        self.reject('version_settings_mismatch')
        self.case = copy.deepcopy(self.initial)
        self.case['observation']['version']['runtime']['limits'] = {'cpu_ms': 1}
        self.reject('version_settings_mismatch')

    def test_route_schedule_and_subdomain_preservation(self):
        cases = [('routes', [], 'route_drift'),
                 ('routes', self.initial['observation']['routes'] * 2, 'route_drift'),
                 ('schedules', ['* * * * *'], 'schedule_drift'),
                 ('subdomain', {'enabled': True, 'previews_enabled': False}, 'subdomain_drift'),
                 ('subdomain', {'enabled': False, 'previews_enabled': True}, 'subdomain_drift')]
        for key, value, code in cases:
            with self.subTest(key=key, value=value):
                self.case = copy.deepcopy(self.initial)
                self.case['observation'][key] = value
                self.reject(code)

    def test_boolean_and_integer_values_are_distinct(self):
        self.case['observation']['settings']['logpush'] = 0
        self.reject('settings_drift')
        self.case = copy.deepcopy(self.initial)
        self.case['observation']['routes'][0]['request_limit_fail_open'] = 0
        self.reject('route_drift')

    def test_bounded_data_rejects_cycles_depth_size_and_non_json_types(self):
        cyclic = []
        cyclic.append(cyclic)
        for value in (cyclic, b'bytes', float('nan'), 1.5, '\ud800',
                      'x' * (planner.MAX_INPUT + 1), list(range(257)), 2**100, {'x': object()}):
            with self.subTest(kind=type(value).__name__):
                self.case = copy.deepcopy(self.initial)
                self.case['observation']['extra'] = value
                self.reject()

    def test_no_private_values_in_failure(self):
        self.case['observation']['settings']['bindings']['ADMIN_TOKEN']['text'] = 'PRIVATE_FIXTURE_MARKER'
        try:
            planner.build_plan(**self.case)
        except ArtifactError as error:
            self.assertNotIn('PRIVATE_FIXTURE_MARKER', str(error))
            self.assertEqual(error.code, 'settings_drift')
        else:
            self.fail('unexpectedly accepted secret material')


if __name__ == '__main__':
    unittest.main()
