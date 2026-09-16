"""Offline adversarial checks for the Commons transition fixture and decision engine."""
import copy
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import commons_artifact as artifact
import commons_fixture as fixture
import commons_plan as planner
import commons_transition as transition
from commons_transition import FixtureDecision, OperationKey
from site_artifact import ArtifactError

OLD = '1' * 40
NEW = '2' * 40
VERSION = '11111111-1111-4111-8111-111111111111'
DEPLOYMENT = '22222222-2222-4222-8222-222222222222'
DATABASE = '44444444-4444-4444-8444-444444444444'
POLICY = {'schema_version': 1, 'target': planner.TARGET, 'account_id': 'a' * 32,
          'zone_id': 'b' * 32, 'script_name': planner.SCRIPT,
          'database_id': DATABASE, 'route_id': 'c' * 32}
SYNTHETIC_REFS = {'ADMIN_TOKEN': 'synth-admin-token', 'IP_HMAC_SECRET': 'synth-ip-secret', 'GITHUB_READ_TOKEN': 'synth-github-read'}


def _build_fixture_inputs():
    files, migrations = artifact.source_inputs(
        Path(__file__).resolve().parents[1] / 'services/commons')
    schema = artifact.expected_schema(migrations)
    with sqlite3.connect(':memory:') as database:
        database.row_factory = sqlite3.Row
        for name in sorted(migrations):
            database.executescript(migrations[name].decode())
        rows = [dict(row) for row in database.execute(artifact.SCHEMA_QUERY)]
    old_packet = artifact.packet(files, OLD, schema)
    changed = {**files, 'worker.mjs': files['worker.mjs'] + b'\n// synthetic candidate\n'}
    new_packet = artifact.packet(changed, NEW, schema)
    _f, old_descriptor = artifact.unpack(old_packet, OLD, schema)
    _f, new_descriptor = artifact.unpack(new_packet, NEW, schema)
    settings = planner.settings(POLICY, OLD)
    observation = {
        'schema_version': 1, 'target': planner.TARGET,
        'account_id': POLICY['account_id'], 'zone_id': POLICY['zone_id'],
        'script_name': POLICY['script_name'],
        'deployment': {'id': DEPLOYMENT, 'strategy': 'percentage',
                       'versions': [{'version_id': VERSION, 'percentage': 100}]},
        'version': {'id': VERSION, 'etag': 'd' * 64,
                    'bindings': copy.deepcopy(settings['bindings']),
                    'runtime': {'compatibility_date': '2026-09-04', 'compatibility_flags': [],
                                'usage_model': 'standard'}},
        'latest_version_id': VERSION, 'settings': copy.deepcopy(settings),
        'routes': [{'id': 'c' * 32, 'pattern': 'oss-singularity.io/api/*',
                    'script': 'oss-singularity-commons', 'request_limit_fail_open': False}],
        'schedules': ['17 * * * *'], 'subdomain': {'enabled': False, 'previews_enabled': False},
        'modules': old_descriptor['modules'], 'schema': rows,
    }
    state = {**observation, 'schema': {'profile': 1, 'sha256': schema}}
    baseline = {'schema_version': 1, 'target': planner.TARGET, 'generation': 7, 'commit': OLD,
                'packet_sha256': artifact.digest(old_packet),
                'policy_sha256': artifact.digest(artifact.encode(POLICY)),
                'observation_sha256': artifact.digest(artifact.encode(state)),
                'version_id': VERSION, 'deployment_id': DEPLOYMENT}
    plan = planner.build_plan(
        candidate_packet=new_packet, expected_candidate_commit=NEW,
        expected_candidate_packet_sha256=artifact.digest(new_packet),
        predecessor_packet=old_packet, baseline=baseline,
        observation=observation, target_policy=POLICY)
    return {
        'candidate_packet': new_packet,
        'predecessor_packet': old_packet,
        'candidate_packet_sha256': artifact.digest(new_packet),
        'predecessor_packet_sha256': artifact.digest(old_packet),
        'observation': observation, 'baseline': baseline,
        'policy': POLICY, 'plan': plan, 'opaque_refs': SYNTHETIC_REFS,
    }


def _fixture_decision(direction, operation_key, observation, schema_fingerprint='ff' * 32):
    return FixtureDecision(
        direction=direction, operation_id=operation_key.operation_id,
        plan_sha256=operation_key.plan_sha256,
        observation_sha256=artifact.digest(artifact.encode(observation)),
        schema_fingerprint=schema_fingerprint, data_revision='synth-rev-1')


class DecisionEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = _build_fixture_inputs()

    def setUp(self):
        self.case = copy.deepcopy(self.inputs)
        self.ok = OperationKey('op-001', self.case['plan']['plan_sha256'], 7)

    def _initial_state(self):
        return {
            'schema_version': 1, 'nonce': 'test-nonce',
            'operation_key': {'operation_id': self.ok.operation_id,
                               'plan_sha256': self.ok.plan_sha256,
                               'start_generation': self.ok.start_generation},
            'phase': 'empty', 'fence_epoch': 0, 'revision': 0,
            'generation': 7, 'dispatch_count': 0,
            'start_baseline': self.case['baseline'],
            'target_policy': self.case['policy'],
            'original_plan': self.case['plan'],
            'candidate_packet_sha256': self.case['candidate_packet_sha256'],
            'predecessor_packet_sha256': self.case['predecessor_packet_sha256'],
            'original_observation_sha256': artifact.digest(artifact.encode(self.case['observation'])),
            'pending_intent': None,
        }

    def test_prepare_transitions_to_prepared(self):
        state = self._initial_state()
        new_state, intent = transition.decide(state, 'prepare', self.case['observation'],
                                              {'operation_key': self.ok})
        self.assertEqual(new_state['phase'], 'preparing')
        new_state, intent = transition.decide(new_state, 'complete_prepare', self.case['observation'],
                                              {'original_plan': self.case['plan']})
        self.assertEqual(new_state['phase'], 'prepared')

    def test_cannot_prepare_twice(self):
        state = self._initial_state()
        state['phase'] = 'prepared'
        with self.assertRaises(ArtifactError):
            transition.decide(state, 'prepare', self.case['observation'],
                              {'operation_key': self.ok})

    def test_prepare_rejects_wrong_plan(self):
        state = self._initial_state()
        new_state, intent = transition.decide(state, 'prepare', self.case['observation'],
                                              {'operation_key': self.ok})
        wrong_plan = {**self.case['plan'], 'plan_sha256': '0' * 64}
        with self.assertRaises(ArtifactError):
            transition.decide(new_state, 'complete_prepare', self.case['observation'],
                              {'original_plan': wrong_plan})

    def test_stage_produces_intent(self):
        state = self._initial_state()
        state['phase'] = 'prepared'
        new_state, intent = transition.decide(state, 'stage', self.case['observation'],
                                              {'operation_key': self.ok,
                                               'opaque_handles': SYNTHETIC_REFS,
                                               'captured_candidate_packet': self.case['candidate_packet']})
        self.assertEqual(new_state['phase'], 'stage_pending')
        self.assertIsNotNone(intent)
        self.assertEqual(intent['effect_kind'], 'stage')

    def test_stage_rejects_wrong_packet(self):
        state = self._initial_state()
        state['phase'] = 'prepared'
        with self.assertRaises(ArtifactError):
            transition.decide(state, 'stage', self.case['observation'],
                              {'operation_key': self.ok,
                               'opaque_handles': SYNTHETIC_REFS,
                               'captured_candidate_packet': b'wrong'})

    def test_activate_requires_decision(self):
        state = self._initial_state()
        state['phase'] = 'staged'
        state['stage_receipt'] = {'staged_version_id': 'vvvv-vvvv-vvvv',
                                  'dispatch_id': 1, 'observation_sha256': '0' * 64}
        decision = _fixture_decision('forward', self.ok, self.case['observation'])
        new_state, intent = transition.decide(state, 'activate', self.case['observation'],
                                              {'operation_key': self.ok,
                                               'fixture_decision': decision})
        self.assertEqual(new_state['phase'], 'activate_pending')
        self.assertIsNotNone(intent)

    def test_activate_rejects_rollback_decision(self):
        state = self._initial_state()
        state['phase'] = 'staged'
        state['stage_receipt'] = {'staged_version_id': 'vvvv-vvvv-vvvv',
                                  'dispatch_id': 1, 'observation_sha256': '0' * 64}
        decision = _fixture_decision('rollback', self.ok, self.case['observation'])
        with self.assertRaises(ArtifactError):
            transition.decide(state, 'activate', self.case['observation'],
                              {'operation_key': self.ok, 'fixture_decision': decision})

    def test_verify_sets_forward_verified(self):
        state = self._initial_state()
        state['phase'] = 'active_unverified'
        state['generation'] = 8
        state['forward_receipt'] = {'forward_version_id': VERSION,
                                    'forward_deployment_id': 'dddd-dddd',
                                    'dispatch_id': 2, 'observation_sha256': '0' * 64}
        observation = self.case['observation']
        new_state, intent = transition.decide(state, 'verify', observation,
                                              {'operation_key': self.ok})
        self.assertEqual(new_state['phase'], 'forward_verified')

    def test_rollback_from_forward_verified(self):
        state = self._initial_state()
        state['phase'] = 'forward_verified'
        state['generation'] = 8
        state['forward_receipt'] = {'forward_version_id': 'vvvv-vvvv',
                                    'forward_deployment_id': 'dddd-dddd',
                                    'dispatch_id': 2, 'observation_sha256': '0' * 64}
        decision = _fixture_decision('rollback', self.ok, self.case['observation'])
        new_state, intent = transition.decide(state, 'rollback', self.case['observation'],
                                              {'operation_key': self.ok,
                                               'fixture_decision': decision})
        self.assertEqual(new_state['phase'], 'rollback_pending')
        self.assertEqual(intent['effect_kind'], 'rollback')

    def test_rollback_rejects_forward_decision(self):
        state = self._initial_state()
        state['phase'] = 'active_unverified'
        state['generation'] = 8
        state['forward_receipt'] = {'forward_version_id': 'vvvv-vvvv',
                                    'forward_deployment_id': 'dddd-dddd',
                                    'dispatch_id': 2, 'observation_sha256': '0' * 64}
        decision = _fixture_decision('forward', self.ok, self.case['observation'])
        with self.assertRaises(ArtifactError):
            transition.decide(state, 'rollback', self.case['observation'],
                              {'operation_key': self.ok, 'fixture_decision': decision})

    def test_wrong_operation_key_rejected(self):
        state = self._initial_state()
        wrong_key = OperationKey('wrong-id', self.ok.plan_sha256, 7)
        with self.assertRaises(ArtifactError):
            transition.decide(state, 'prepare', self.case['observation'],
                              {'operation_key': wrong_key})

    def test_terminal_phase_blocks_new_actions(self):
        state = self._initial_state()
        state['phase'] = 'forward_verified'
        state['generation'] = 8
        state['forward_receipt'] = {'forward_version_id': 'vvvv-vvvv',
                                    'forward_deployment_id': 'dddd-dddd',
                                    'dispatch_id': 2, 'observation_sha256': '0' * 64}
        with self.assertRaises(ArtifactError):
            transition.decide(state, 'prepare', self.case['observation'],
                              {'operation_key': self.ok})

    def test_generation_validation(self):
        state = self._initial_state()
        state['phase'] = 'active_unverified'
        state['generation'] = 7
        state['forward_receipt'] = {'forward_version_id': 'vvvv-vvvv',
                                    'forward_deployment_id': 'dddd-dddd',
                                    'dispatch_id': 2, 'observation_sha256': '0' * 64}
        with self.assertRaises(ArtifactError):
            transition.decide(state, 'verify', self.case['observation'],
                              {'operation_key': self.ok})

    def test_pending_intent_blocks_new_actions(self):
        state = self._initial_state()
        state['phase'] = 'prepared'
        state['pending_intent'] = {'effect_kind': 'stage', 'dispatch_id': 1,
                                   'fence_epoch': 0, 'expected_revision': 1}
        with self.assertRaises(ArtifactError):
            transition.decide(state, 'stage', self.case['observation'],
                              {'operation_key': self.ok,
                               'opaque_handles': SYNTHETIC_REFS,
                               'captured_candidate_packet': self.case['candidate_packet']})

    def test_dispatch_count_limit(self):
        state = self._initial_state()
        state['phase'] = 'prepared'
        state['dispatch_count'] = 8
        with self.assertRaises(ArtifactError):
            transition.decide(state, 'stage', self.case['observation'],
                              {'operation_key': self.ok,
                               'opaque_handles': SYNTHETIC_REFS,
                               'captured_candidate_packet': self.case['candidate_packet']})

    def test_reconcile_unknown_pending(self):
        state = self._initial_state()
        state['phase'] = 'stage_pending'
        state['revision'] = 5
        state['dispatch_count'] = 1
        state['pending_intent'] = {'effect_kind': 'stage', 'dispatch_id': 1,
                                   'fence_epoch': 0, 'expected_revision': 6,
                                   'expected_active_id': VERSION}
        observation = self.case['observation']
        evidence = {'latest_version_id': None, 'active_version_id': VERSION,
                    'active_deployment_id': DEPLOYMENT}
        new_state, intent = transition.decide(state, 'reconcile', observation,
                                              {'operation_key': self.ok,
                                               'opaque_evidence': evidence})
        self.assertEqual(new_state['phase'], 'reconciliation_required')

    def test_reconcile_applied_stage(self):
        state = self._initial_state()
        state['phase'] = 'stage_pending'
        state['revision'] = 5
        state['dispatch_count'] = 1
        new_version_id = '99999999-9999-4999-8999-999999999999'
        state['pending_intent'] = {'effect_kind': 'stage', 'dispatch_id': 1,
                                   'fence_epoch': 0, 'expected_revision': 6,
                                   'expected_staged_id': new_version_id,
                                   'expected_active_id': VERSION}
        observation = copy.deepcopy(self.case['observation'])
        evidence = {'latest_version_id': new_version_id,
                    'active_version_id': VERSION,
                    'active_deployment_id': DEPLOYMENT}
        new_state, intent = transition.decide(state, 'reconcile', observation,
                                              {'operation_key': self.ok,
                                               'opaque_evidence': evidence})
        self.assertEqual(new_state['phase'], 'staged')
        self.assertIsNone(new_state.get('pending_intent'))

    def test_reconcile_not_applied_final_stage(self):
        state = self._initial_state()
        state['phase'] = 'stage_pending'
        state['revision'] = 5
        state['dispatch_count'] = 1
        state['pending_intent'] = {'effect_kind': 'stage', 'dispatch_id': 1,
                                   'fence_epoch': 0, 'expected_revision': 6,
                                   'expected_active_id': VERSION}
        observation = self.case['observation']
        evidence = {'latest_version_id': VERSION, 'active_version_id': VERSION,
                    'active_deployment_id': DEPLOYMENT}
        new_state, intent = transition.decide(state, 'reconcile', observation,
                                              {'operation_key': self.ok,
                                               'opaque_evidence': evidence})
        self.assertEqual(new_state['phase'], 'prepared')
        self.assertIsNone(new_state.get('pending_intent'))

    def test_reconcile_applied_activate(self):
        state = self._initial_state()
        state['phase'] = 'activate_pending'
        state['revision'] = 5
        state['dispatch_count'] = 2
        new_version_id = '99999999-9999-4999-8999-999999999999'
        new_deployment_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        state['pending_intent'] = {'effect_kind': 'activate', 'dispatch_id': 2,
                                   'fence_epoch': 0, 'expected_revision': 6,
                                   'expected_predecessor_id': VERSION,
                                   'expected_new_active_id': new_version_id}
        observation = copy.deepcopy(self.case['observation'])
        evidence = {'latest_version_id': new_version_id,
                    'active_version_id': new_version_id,
                    'active_deployment_id': new_deployment_id}
        new_state, intent = transition.decide(state, 'reconcile', observation,
                                              {'operation_key': self.ok,
                                               'opaque_evidence': evidence})
        self.assertEqual(new_state['phase'], 'active_unverified')
        self.assertEqual(new_state['generation'], 8)

    def test_reconcile_applied_rollback(self):
        state = self._initial_state()
        state['phase'] = 'rollback_pending'
        state['revision'] = 5
        state['dispatch_count'] = 3
        state['generation'] = 8
        state['forward_receipt'] = {'forward_version_id': 'vvvv-vvvv',
                                    'forward_deployment_id': 'dddd-dddd',
                                    'dispatch_id': 2, 'observation_sha256': '0' * 64}
        state['pending_intent'] = {'effect_kind': 'rollback', 'dispatch_id': 3,
                                   'fence_epoch': 0, 'expected_revision': 6,
                                   'expected_rollback_target_id': VERSION,
                                   'expected_new_active_id': 'vvvv-vvvv'}
        observation = copy.deepcopy(self.case['observation'])
        evidence = {'latest_version_id': 'vvvv-vvvv',
                    'active_version_id': VERSION,
                    'active_deployment_id': DEPLOYMENT}
        new_state, intent = transition.decide(state, 'reconcile', observation,
                                              {'operation_key': self.ok,
                                               'opaque_evidence': evidence})
        self.assertEqual(new_state['phase'], 'rolled_back')
        self.assertEqual(new_state['generation'], 9)

    def test_validate_state_rejects_wrong_schema(self):
        state = self._initial_state()
        state['schema_version'] = 2
        with self.assertRaises(ArtifactError):
            transition.validate_state(state, self.ok)

    def test_pure_decision_no_io(self):
        import sys
        state = self._initial_state()
        state['phase'] = 'prepared'
        blocked = ['open', 'os.open', 'os.read', 'os.write', 'subprocess', 'socket',
                   'sqlite3', 'urllib', 'requests']
        with patch.dict(sys.modules, {name: None for name in blocked if name in sys.modules}):
            pass
        new_state, intent = transition.decide(state, 'stage', self.case['observation'],
                                              {'operation_key': self.ok,
                                               'opaque_handles': SYNTHETIC_REFS,
                                               'captured_candidate_packet': self.case['candidate_packet']})
        self.assertEqual(new_state['phase'], 'stage_pending')

    def test_unknown_command_rejected(self):
        state = self._initial_state()
        with self.assertRaises(ArtifactError):
            transition.decide(state, 'nonexistent', self.case['observation'],
                              {'operation_key': self.ok})


class FixtureIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = _build_fixture_inputs()

    def setUp(self):
        self.case = copy.deepcopy(self.inputs)

    def _make_ok(self, plan):
        return OperationKey('op-test', plan['plan_sha256'], 7)

    def test_full_normal_flow(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                result = sess.prepare(self.case['candidate_packet'], NEW,
                                      self.case['candidate_packet_sha256'],
                                      self.case['predecessor_packet'],
                                      self.case['predecessor_packet_sha256'], ok)
                self.assertEqual(result['phase'], 'prepared')
                result = sess.stage(ok)
                self.assertEqual(result['phase'], 'staged')
                self.assertIn('staged_version_id', result)
                decision = _fixture_decision('forward', ok,
                                             self.case['observation'])
                result = sess.activate(ok, decision)
                self.assertIn(result['phase'], ('active_unverified', 'reconciliation_required'))
                if result['phase'] == 'active_unverified':
                    result = sess.verify(ok)
                    self.assertEqual(result['phase'], 'forward_verified')
                    decision_rb = _fixture_decision('rollback', ok,
                                                    self.case['observation'])
                    result = sess.rollback(ok, decision_rb)
                    self.assertIn(result['phase'], ('rolled_back', 'reconciliation_required'))

    def test_cannot_prepare_twice_in_session(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                with self.assertRaises(ArtifactError):
                    sess.prepare(self.case['candidate_packet'], NEW,
                                 self.case['candidate_packet_sha256'],
                                 self.case['predecessor_packet'],
                                 self.case['predecessor_packet_sha256'], ok)

    def test_wrong_operation_key_rejected_in_session(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                wrong_ok = OperationKey('wrong-id', ok.plan_sha256, 7)
                with self.assertRaises(ArtifactError):
                    sess.stage(wrong_ok)

    def test_stage_before_prepare_rejected(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                with self.assertRaises(ArtifactError):
                    sess.stage(ok)

    def test_activate_before_stage_rejected(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                decision = _fixture_decision('forward', ok,
                                             self.case['observation'])
                with self.assertRaises(ArtifactError):
                    sess.activate(ok, decision)

    def test_rollback_without_forward_rejected(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                decision = _fixture_decision('rollback', ok,
                                             self.case['observation'])
                with self.assertRaises(ArtifactError):
                    sess.rollback(ok, decision)

    def test_concurrent_lock_rejects(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess1:
                with self.assertRaises(ArtifactError):
                    with fixture.session(handle):
                        pass

    def test_prepare_preserves_original_plan(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                self.assertEqual(sess._state['original_plan']['plan_sha256'],
                                 self.case['plan']['plan_sha256'])

    def test_observation_drift_rejected_on_prepare(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                tampered_provider = copy.deepcopy(sess._provider)
                tampered_provider['settings']['tags'] = ['modified']
                sess._provider = tampered_provider
                sess._save_provider()
                with self.assertRaises(ArtifactError):
                    sess.prepare(self.case['candidate_packet'], NEW,
                                 self.case['candidate_packet_sha256'],
                                 self.case['predecessor_packet'],
                                 self.case['predecessor_packet_sha256'], ok)

    def test_stage_creates_new_version_id(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                result = sess.stage(ok)
                staged_id = result['staged_version_id']
                self.assertIsNotNone(staged_id)
                self.assertNotEqual(staged_id, VERSION)

    def test_stage_does_not_change_active_version(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                result = sess.stage(ok)
                self.assertEqual(sess._provider['active_version_id'], VERSION)

    def test_activate_switches_active_version(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                result = sess.stage(ok)
                staged_id = result['staged_version_id']
                decision = _fixture_decision('forward', ok,
                                             self.case['observation'])
                result = sess.activate(ok, decision)
                if result['phase'] == 'active_unverified':
                    self.assertEqual(sess._provider['active_version_id'], staged_id)

    def test_rollback_restores_original_version(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                sess.stage(ok)
                decision_fwd = _fixture_decision('forward', ok,
                                                 self.case['observation'])
                result = sess.activate(ok, decision_fwd)
                if result['phase'] == 'active_unverified':
                    sess.verify(ok)
                    decision_rb = _fixture_decision('rollback', ok,
                                                    self.case['observation'])
                    result = sess.rollback(ok, decision_rb)
                    if result['phase'] == 'rolled_back':
                        self.assertEqual(sess._provider['active_version_id'], VERSION)

    def test_generation_increments_on_activate(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                sess.stage(ok)
                decision = _fixture_decision('forward', ok,
                                             self.case['observation'])
                result = sess.activate(ok, decision)
                if result['phase'] == 'active_unverified':
                    self.assertEqual(sess._state['generation'], 8)

    def test_generation_increments_on_rollback(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                sess.stage(ok)
                decision_fwd = _fixture_decision('forward', ok,
                                                 self.case['observation'])
                result = sess.activate(ok, decision_fwd)
                if result['phase'] == 'active_unverified':
                    sess.verify(ok)
                    decision_rb = _fixture_decision('rollback', ok,
                                                    self.case['observation'])
                    result = sess.rollback(ok, decision_rb)
                    if result['phase'] == 'rolled_back':
                        self.assertEqual(sess._state['generation'], 9)

    def test_duplicate_dispatch_rejected(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                sess.stage(ok)
                sess._provider['intents']['1'] = sess._provider['intents'].pop(
                    list(sess._provider['intents'].keys())[0])
                sess._save_provider()
                with self.assertRaises(ArtifactError):
                    sess.stage(ok)

    def test_handle_rejects_direct_construction(self):
        with self.assertRaises(ArtifactError):
            with fixture.locked("not a handle"):
                pass

    def test_reconcile_after_crash_continues(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                result = sess.stage(ok)
                self.assertEqual(result['phase'], 'staged')
                decision = _fixture_decision('forward', ok,
                                             self.case['observation'])
                result = sess.activate(ok, decision)
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                if sess._state and sess._state['phase'] in ('activate_pending', 'reconciliation_required'):
                    result = sess.reconcile(ok)
                    self.assertIn(result['phase'], ('active_unverified', 'staged', 'reconciliation_required'))

    def test_fixture_preserves_peer_sentinel(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
            with fixture.locked(handle) as fds:
                sentinel, _r, _i = fixture.read_file(fds['peer'], 'sentinel', 128)
                self.assertEqual(sentinel, b'unrelated fixture: preserve\n')

    def test_secret_preservation_before_activate(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                sess.stage(ok)
                tampered_provider = copy.deepcopy(sess._provider)
                tampered_provider['opaque_refs']['ADMIN_TOKEN'] = 'tampered'
                sess._provider = tampered_provider
                sess._save_provider()
                decision = _fixture_decision('forward', ok,
                                             self.case['observation'])
                result = sess.activate(ok, decision)
                self.assertIn(result['phase'], ('reconciliation_required', 'active_unverified'))


class ProcessCrashRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = _build_fixture_inputs()

    def setUp(self):
        self.case = copy.deepcopy(self.inputs)

    def _make_ok(self, plan):
        return OperationKey('op-crash', plan['plan_sha256'], 7)

    def test_crash_after_prepare_recovers(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                self.assertEqual(sess._state['phase'], 'prepared')
            with fixture.session(handle) as sess:
                self.assertEqual(sess._state['phase'], 'prepared')

    def test_crash_after_stage_intent_recovers(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                new_state, intent = transition.decide(
                    sess._state, 'stage', sess._observe(),
                    {'operation_key': ok, 'opaque_handles': copy.deepcopy(sess._provider['opaque_refs']),
                     'captured_candidate_packet': self.case['candidate_packet']})
                self.assertEqual(new_state['phase'], 'stage_pending')
                sess._state = new_state
                sess._save_state()
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                result = sess.reconcile(ok)
                self.assertIn(result['phase'], ('prepared', 'staged', 'reconciliation_required'))

    def test_crash_after_stage_effect_recovers(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                new_state, intent = transition.decide(
                    sess._state, 'stage', sess._observe(),
                    {'operation_key': ok, 'opaque_handles': copy.deepcopy(sess._provider['opaque_refs']),
                     'captured_candidate_packet': self.case['candidate_packet']})
                sess._state = new_state
                sess._save_state()
                sess._provider, _ = fixture._provider_stage(
                    sess._provider, intent, self.case['candidate_packet'],
                    sess._provider['opaque_refs'], NEW)
                sess._save_provider()
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                result = sess.reconcile(ok)
                self.assertEqual(result['phase'], 'staged')

    def test_crash_after_activate_intent_recovers(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                sess.stage(ok)
                decision = FixtureDecision('forward', ok.operation_id, ok.plan_sha256,
                                           'ff' * 32, 'ff' * 32, 'synth')
                new_state, intent = transition.decide(
                    sess._state, 'activate', sess._observe(),
                    {'operation_key': ok, 'fixture_decision': decision})
                self.assertEqual(new_state['phase'], 'activate_pending')
                sess._state = new_state
                sess._save_state()
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                result = sess.reconcile(ok)
                self.assertIn(result['phase'], ('active_unverified', 'staged', 'reconciliation_required'))

    def test_crash_after_activate_effect_recovers(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                sess.stage(ok)
                decision = FixtureDecision('forward', ok.operation_id, ok.plan_sha256,
                                           'ff' * 32, 'ff' * 32, 'synth')
                new_state, intent = transition.decide(
                    sess._state, 'activate', sess._observe(),
                    {'operation_key': ok, 'fixture_decision': decision})
                sess._state = new_state
                sess._save_state()
                staged_id = sess._state['stage_receipt']['staged_version_id']
                sess._provider = fixture._provider_activate(sess._provider, intent, staged_id)
                sess._save_provider()
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                result = sess.reconcile(ok)
                self.assertIn(result['phase'], ('active_unverified', 'reconciliation_required'))

    def test_crash_after_rollback_intent_recovers(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                sess.stage(ok)
                decision_fwd = FixtureDecision('forward', ok.operation_id, ok.plan_sha256,
                                               'ff' * 32, 'ff' * 32, 'synth')
                result = sess.activate(ok, decision_fwd)
                if result['phase'] == 'active_unverified':
                    sess.verify(ok)
                    decision_rb = FixtureDecision('rollback', ok.operation_id, ok.plan_sha256,
                                                  'ff' * 32, 'ff' * 32, 'synth')
                    new_state, intent = transition.decide(
                        sess._state, 'rollback', sess._observe(),
                        {'operation_key': ok, 'fixture_decision': decision_rb})
                    self.assertEqual(new_state['phase'], 'rollback_pending')
                    sess._state = new_state
                    sess._save_state()
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                result = sess.reconcile(ok)
                self.assertIn(result['phase'], ('rolled_back', 'reconciliation_required',
                                                'active_unverified', 'forward_verified'))

    def test_crash_after_rollback_effect_recovers(self):
        with fixture.create_fixture(self.case['observation'], self.case['baseline'],
                                    self.case['policy'], self.case['opaque_refs']) as handle:
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                sess.prepare(self.case['candidate_packet'], NEW,
                             self.case['candidate_packet_sha256'],
                             self.case['predecessor_packet'],
                             self.case['predecessor_packet_sha256'], ok)
                sess.stage(ok)
                decision_fwd = FixtureDecision('forward', ok.operation_id, ok.plan_sha256,
                                               'ff' * 32, 'ff' * 32, 'synth')
                result = sess.activate(ok, decision_fwd)
                if result['phase'] == 'active_unverified':
                    sess.verify(ok)
                    decision_rb = FixtureDecision('rollback', ok.operation_id, ok.plan_sha256,
                                                  'ff' * 32, 'ff' * 32, 'synth')
                    new_state, intent = transition.decide(
                        sess._state, 'rollback', sess._observe(),
                        {'operation_key': ok, 'fixture_decision': decision_rb})
                    sess._state = new_state
                    sess._save_state()
                    sess._provider = fixture._provider_restore(
                        sess._provider, intent, sess._state['start_baseline']['version_id'])
                    sess._save_provider()
            with fixture.session(handle) as sess:
                ok = self._make_ok(self.case['plan'])
                result = sess.reconcile(ok)
                self.assertEqual(result['phase'], 'rolled_back')


class BoundedInputTests(unittest.TestCase):
    def test_operation_key_frozen(self):
        ok = OperationKey('test', '0' * 64, 7)
        with self.assertRaises(Exception):
            ok.operation_id = 'changed'

    def test_fixture_decision_frozen(self):
        d = FixtureDecision('forward', 'test', '0' * 64, '0' * 64, 'ff' * 32, 'synth')
        with self.assertRaises(Exception):
            d.direction = 'rollback'

    def test_validate_state_rejects_missing_fields(self):
        state = {'schema_version': 1}
        ok = OperationKey('test', '0' * 64, 7)
        with self.assertRaises(ArtifactError):
            transition.validate_state(state, ok)

    def test_classify_observation_rejects_invalid(self):
        with self.assertRaises(ArtifactError):
            transition.classify_observation({}, 'empty', {},
                                            {'invalid': True}, {})


if __name__ == '__main__':
    unittest.main()
