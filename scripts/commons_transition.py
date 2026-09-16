"""Pure decision engine for exactly one version-bound Commons code transition.

No I/O, no network, no clock, no UUID, no SQL, no worker code, no provider
operations. Every decision returns a new control state and at most one typed
intent. Lost replies are classified without mutating or repeating effects.
"""
from dataclasses import dataclass
import json

from commons_artifact import digest, encode, hash_value
from commons_plan import (CRON, MODULES, OBSERVATION_FIELDS, ROUTE, SCRIPT,
                          TARGET, fields, profile, same, settings, uuid, validate_policy)
from site_artifact import ArtifactError, require

PHASES = {
    'empty', 'preparing', 'prepared', 'stage_pending', 'staged',
    'activate_pending', 'active_unverified', 'forward_verified',
    'rollback_pending', 'rolled_back', 'aborted', 'reconciliation_required',
}
TERMINAL = {'forward_verified', 'rolled_back', 'aborted'}
PENDING_PHASES = {'preparing', 'stage_pending', 'activate_pending', 'rollback_pending'}
MAX_DISPATCHES = 8


@dataclass(frozen=True)
class OperationKey:
    """Externally held identity; never deserialized from a candidate."""
    operation_id: str
    plan_sha256: str
    start_generation: int


@dataclass(frozen=True)
class FixtureDecision:
    """Trusted harness decision on compatibility; not a candidate-supplied flag."""
    direction: str  # 'forward' | 'rollback'
    operation_id: str
    plan_sha256: str
    observation_sha256: str
    schema_fingerprint: str
    data_revision: str


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _decode(value):
    return json.loads(value)


def _state_check(state, operation_key):
    require(type(state) is dict, 'invalid_state')
    if operation_key is not None:
        require(type(operation_key) is OperationKey, 'invalid_operation_key')
        require(state.get('operation_key') == {
            'operation_id': operation_key.operation_id,
            'plan_sha256': operation_key.plan_sha256,
            'start_generation': operation_key.start_generation,
        }, 'wrong_operation_key')
        require(state['generation'] == operation_key.start_generation + (1 if state['phase'] in {
            'active_unverified', 'forward_verified', 'rollback_pending', 'rolled_back'} else
            2 if state['phase'] == 'rolled_back' and state.get('rollback_receipt') else 0), 'invalid_state')
    require(type(state.get('schema_version')) is int and state['schema_version'] == 1, 'invalid_state')
    require(type(state.get('phase')) is str and state['phase'] in PHASES, 'invalid_state')
    require(type(state.get('fence_epoch')) is int and state['fence_epoch'] >= 0, 'invalid_state')
    require(type(state.get('revision')) is int and state['revision'] >= 0, 'invalid_state')
    require(type(state.get('generation')) is int and 1 <= state['generation'] < 2**63 - 1, 'invalid_state')
    require(type(state.get('dispatch_count')) is int and 0 <= state['dispatch_count'] <= MAX_DISPATCHES,
            'invalid_state')


def _require_no_pending(state):
    require(state.get('pending_intent') is None, 'pending_intent_exists')


def _require_terminal(state):
    require(state['phase'] not in TERMINAL or state.get('pending_intent') is None, 'pending_intent_exists')
    if state['phase'] in TERMINAL:
        require(False, 'already_terminal')


def validate_state(state, operation_key):
    _state_check(state, operation_key)
    require(type(state.get('start_baseline')) is dict, 'invalid_state')
    require(type(state.get('original_plan')) is dict and state['original_plan'].get('plan_sha256')
            == operation_key.plan_sha256, 'invalid_state')
    require(type(state.get('target_policy')) is dict, 'invalid_state')
    validate_policy(state['target_policy'])
    for name in ('candidate_packet_sha256', 'predecessor_packet_sha256'):
        hash_value(state.get(name, ''))
    require(state.get('original_observation_sha256'), 'invalid_state')
    hash_value(state['original_observation_sha256'])


def _classify_pending(state, fresh_observation, opaque_evidence):
    pending = state.get('pending_intent')
    if pending is None:
        return None
    require(type(pending) is dict, 'invalid_state')
    require(type(pending.get('effect_kind')) is str
            and pending['effect_kind'] in ('stage', 'activate', 'rollback'), 'invalid_state')
    effect = pending['effect_kind']
    fresh = _canonical(fresh_observation)

    if effect == 'stage':
        staged_id = opaque_evidence.get('latest_version_id')
        expected_active = pending.get('expected_active_id')
        if staged_id is not None and staged_id != expected_active:
            if opaque_evidence.get('active_version_id') == expected_active:
                return 'applied'
            else:
                return 'conflict'
        elif staged_id == expected_active:
            if opaque_evidence.get('active_version_id') == expected_active:
                return 'not_applied_final'
            else:
                return 'conflict'
        elif staged_id is None:
            return 'unknown'
        else:
            return 'conflict'
        return 'unknown'

    elif effect == 'activate':
        active_id = opaque_evidence.get('active_version_id')
        if active_id == pending.get('expected_new_active_id'):
            if opaque_evidence.get('latest_version_id') == pending.get('expected_new_active_id'):
                return 'applied'
        elif active_id == pending.get('expected_predecessor_id'):
            return 'not_applied_final'
        elif active_id is None:
            return 'unknown'
        else:
            return 'conflict'
        return 'unknown'

    elif effect == 'rollback':
        active_id = opaque_evidence.get('active_version_id')
        if active_id == pending.get('expected_rollback_target_id'):
            return 'applied'
        elif active_id == pending.get('expected_new_active_id'):
            return 'not_applied_final'
        elif active_id is None:
            return 'unknown'
        else:
            return 'conflict'
        return 'unknown'

    return 'unknown'


def classify_observation(plan, phase, receipts, observation, opaque_evidence):
    require(type(plan) is dict and plan.get('plan_sha256'), 'invalid_plan')
    require(type(phase) is str and phase in PHASES, 'invalid_phase')
    require(type(receipts) is dict, 'invalid_receipts')
    bounded_observation(observation)
    require(type(opaque_evidence) is dict, 'invalid_evidence')
    expected = plan.get('desired_version', {})
    obs_version = observation.get('version', {})
    obs_deployment = observation.get('deployment', {})
    if expected:
        obs_modules = observation.get('modules', {})
        expected_modules = expected.get('modules', {})
        if set(obs_modules.keys()) == MODULES and set(expected_modules.keys()) == MODULES:
            for name in sorted(MODULES):
                if obs_modules[name] != expected_modules[name]:
                    return 'candidate_state_changed'
        obs_bindings = obs_version.get('bindings', {})
        expected_bindings = expected.get('bindings', {})
        if _canonical(obs_bindings) != _canonical(expected_bindings):
            return 'candidate_state_changed'
    if phase in ('staged', 'activate_pending', 'active_unverified', 'forward_verified',
                 'rollback_pending', 'rolled_back'):
        staged_id = receipts.get('staged_version_id')
        if staged_id and obs_version.get('id') == staged_id:
            pass
        elif staged_id and obs_version.get('id') != staged_id:
            return 'candidate_state_changed'
    return 'consistent'


def bounded_observation(value):
    require(type(value) is dict and set(value) == OBSERVATION_FIELDS, 'invalid_observation')
    profile(value, 'invalid_observation')


def _decide_prepare(state, observation, operation_key):
    _require_terminal(state)
    require(state['phase'] == 'empty', 'already_prepared')
    require(type(observation) is dict, 'invalid_observation')
    new_state = {**state, 'phase': 'preparing',
                 'original_observation_sha256': digest(encode(observation)),
                 'revision': state['revision'] + 1}
    return new_state, None


def _decide_complete_prepare(state, fresh_observation, original_plan):
    require(state['phase'] == 'preparing', 'not_preparing')
    require(type(original_plan) is dict and original_plan.get('plan_sha256')
            == state['original_plan']['plan_sha256'], 'plan_mismatch')
    plan_without_sha = {k: v for k, v in original_plan.items() if k != 'plan_sha256'}
    require(digest(encode(plan_without_sha)) == state['original_plan']['plan_sha256'], 'plan_mismatch')
    new_state = {**state, 'phase': 'prepared', 'revision': state['revision'] + 1}
    return new_state, None


def _decide_stage(state, captured_candidate_packet, opaque_handles, operation_key):
    require(state['phase'] == 'prepared', 'not_prepared')
    _require_no_pending(state)
    require(state['dispatch_count'] < MAX_DISPATCHES, 'dispatch_limit')
    require(type(captured_candidate_packet) is bytes, 'invalid_packet')
    require(digest(captured_candidate_packet) == state['candidate_packet_sha256'], 'packet_mismatch')
    require(type(opaque_handles) is dict and 'ADMIN_TOKEN' in opaque_handles
            and 'IP_HMAC_SECRET' in opaque_handles and 'GITHUB_READ_TOKEN' in opaque_handles, 'invalid_handles')
    intent = {'effect_kind': 'stage', 'operation_id': operation_key.operation_id,
              'plan_sha256': operation_key.plan_sha256, 'fence_epoch': state['fence_epoch'],
              'expected_revision': state['revision'] + 1, 'dispatch_id': state['dispatch_count'] + 1,
              'expected_active_id': state['start_baseline']['version_id']}
    new_state = {**state, 'phase': 'stage_pending',
                 'pending_intent': intent,
                 'stage_handles': opaque_handles,
                 'revision': state['revision'] + 1,
                 'dispatch_count': state['dispatch_count'] + 1}
    return new_state, intent


def _decide_complete_stage(state, fresh_observation, opaque_evidence, operation_key):
    require(state['phase'] == 'stage_pending', 'not_stage_pending')
    pending = state.get('pending_intent')
    require(pending is not None and pending['effect_kind'] == 'stage', 'no_stage_pending')
    classification = _classify_pending(state, fresh_observation, opaque_evidence)
    if classification == 'applied':
        staged_id = opaque_evidence.get('latest_version_id')
        require(staged_id is not None and staged_id != state['start_baseline']['version_id'],
                'invalid_staged_id')
        receipt = {'staged_version_id': staged_id,
                   'dispatch_id': pending['dispatch_id'],
                   'observation_sha256': digest(encode(fresh_observation))}
        new_state = {**state, 'phase': 'staged',
                     'pending_intent': None, 'stage_receipt': receipt,
                     'revision': state['revision'] + 1}
        return new_state, None
    elif classification == 'not_applied_final':
        new_state = {**state, 'phase': 'prepared', 'pending_intent': None,
                     'revision': state['revision'] + 1}
        return new_state, None
    else:
        new_state = {**state, 'phase': 'reconciliation_required',
                     'revision': state['revision'] + 1}
        return new_state, None


def _decide_activate(state, fixture_decision, operation_key):
    require(state['phase'] == 'staged', 'not_staged')
    _require_no_pending(state)
    require(type(fixture_decision) is FixtureDecision, 'invalid_decision')
    require(fixture_decision.direction == 'forward', 'wrong_decision_direction')
    require(fixture_decision.operation_id == operation_key.operation_id, 'decision_mismatch')
    require(fixture_decision.plan_sha256 == operation_key.plan_sha256, 'decision_mismatch')
    staged_id = state['stage_receipt']['staged_version_id']
    intent = {'effect_kind': 'activate', 'operation_id': operation_key.operation_id,
              'plan_sha256': operation_key.plan_sha256, 'fence_epoch': state['fence_epoch'],
              'expected_revision': state['revision'] + 1, 'dispatch_id': state['dispatch_count'] + 1,
              'expected_predecessor_id': state['start_baseline']['version_id'],
              'expected_new_active_id': staged_id}
    new_state = {**state, 'phase': 'activate_pending',
                 'pending_intent': intent,
                 'activate_decision': {
                     'observation_sha256': fixture_decision.observation_sha256,
                     'schema_fingerprint': fixture_decision.schema_fingerprint,
                     'data_revision': fixture_decision.data_revision},
                 'revision': state['revision'] + 1,
                 'dispatch_count': state['dispatch_count'] + 1}
    return new_state, intent


def _decide_complete_activate(state, fresh_observation, opaque_evidence, operation_key):
    require(state['phase'] == 'activate_pending', 'not_activate_pending')
    pending = state.get('pending_intent')
    require(pending is not None and pending['effect_kind'] == 'activate', 'no_activate_pending')
    classification = _classify_pending(state, fresh_observation, opaque_evidence)
    if classification == 'applied':
        active_id = opaque_evidence.get('active_version_id')
        staged_id = state['stage_receipt']['staged_version_id']
        require(active_id == staged_id, 'activate_id_mismatch')
        receipt = {'forward_version_id': active_id,
                   'forward_deployment_id': opaque_evidence.get('active_deployment_id', ''),
                   'dispatch_id': pending['dispatch_id'],
                   'observation_sha256': digest(encode(fresh_observation))}
        new_state = {**state, 'phase': 'active_unverified',
                     'pending_intent': None, 'forward_receipt': receipt,
                     'generation': operation_key.start_generation + 1,
                     'revision': state['revision'] + 1}
        return new_state, None
    elif classification == 'not_applied_final':
        new_state = {**state, 'phase': 'staged', 'pending_intent': None,
                     'revision': state['revision'] + 1}
        return new_state, None
    else:
        new_state = {**state, 'phase': 'reconciliation_required',
                     'revision': state['revision'] + 1}
        return new_state, None


def _decide_verify(state, fresh_observation, operation_key):
    require(state['phase'] in ('active_unverified', 'forward_verified'), 'not_verifiable')
    _require_no_pending(state)
    forward = state.get('forward_receipt')
    require(forward is not None, 'no_forward_receipt')
    obs_version = fresh_observation.get('version', {})
    require(obs_version.get('id') == forward['forward_version_id'], 'version_changed')
    new_state = {**state, 'phase': 'forward_verified',
                 'revision': state['revision'] + 1}
    return new_state, None


def _decide_rollback(state, fixture_decision, operation_key):
    require(state['phase'] in ('active_unverified', 'forward_verified'), 'not_rollbackable')
    _require_no_pending(state)
    require(type(fixture_decision) is FixtureDecision, 'invalid_decision')
    require(fixture_decision.direction == 'rollback', 'wrong_decision_direction')
    require(fixture_decision.operation_id == operation_key.operation_id, 'decision_mismatch')
    require(fixture_decision.plan_sha256 == operation_key.plan_sha256, 'decision_mismatch')
    forward = state.get('forward_receipt')
    require(forward is not None, 'no_forward_receipt')
    original_version = state['start_baseline']['version_id']
    intent = {'effect_kind': 'rollback', 'operation_id': operation_key.operation_id,
              'plan_sha256': operation_key.plan_sha256, 'fence_epoch': state['fence_epoch'],
              'expected_revision': state['revision'] + 1, 'dispatch_id': state['dispatch_count'] + 1,
              'expected_rollback_target_id': original_version,
              'expected_new_active_id': forward['forward_version_id']}
    new_state = {**state, 'phase': 'rollback_pending',
                 'pending_intent': intent,
                 'revision': state['revision'] + 1,
                 'dispatch_count': state['dispatch_count'] + 1}
    return new_state, intent


def _decide_complete_rollback(state, fresh_observation, opaque_evidence, operation_key):
    require(state['phase'] == 'rollback_pending', 'not_rollback_pending')
    pending = state.get('pending_intent')
    require(pending is not None and pending['effect_kind'] == 'rollback', 'no_rollback_pending')
    classification = _classify_pending(state, fresh_observation, opaque_evidence)
    if classification == 'applied':
        active_id = opaque_evidence.get('active_version_id')
        original_id = state['start_baseline']['version_id']
        require(active_id == original_id, 'rollback_target_mismatch')
        receipt = {'rollback_version_id': active_id,
                   'rollback_deployment_id': opaque_evidence.get('active_deployment_id', ''),
                   'dispatch_id': pending['dispatch_id'],
                   'forward_receipt_sha256': digest(encode(state['forward_receipt'])),
                   'observation_sha256': digest(encode(fresh_observation))}
        new_state = {**state, 'phase': 'rolled_back',
                     'pending_intent': None, 'rollback_receipt': receipt,
                     'generation': operation_key.start_generation + 2,
                     'revision': state['revision'] + 1}
        return new_state, None
    elif classification == 'not_applied_final':
        new_state = {**state, 'phase': state['phase'].replace('_pending', ''),
                     'pending_intent': None, 'revision': state['revision'] + 1}
        return new_state, None
    else:
        new_state = {**state, 'phase': 'reconciliation_required',
                     'revision': state['revision'] + 1}
        return new_state, None


def _decide_reconcile(state, fresh_observation, opaque_evidence, operation_key):
    if state['phase'] in PENDING_PHASES:
        classification = _classify_pending(state, fresh_observation, opaque_evidence)
        if classification == 'applied':
            pending = state['pending_intent']
            if pending['effect_kind'] == 'stage':
                staged_id = opaque_evidence.get('latest_version_id')
                receipt = {'staged_version_id': staged_id,
                           'dispatch_id': pending['dispatch_id'],
                           'observation_sha256': digest(encode(fresh_observation))}
                new_state = {**state, 'phase': 'staged', 'pending_intent': None,
                             'stage_receipt': receipt, 'revision': state['revision'] + 1}
                return new_state, None
            elif pending['effect_kind'] == 'activate':
                active_id = opaque_evidence.get('active_version_id')
                receipt = {'forward_version_id': active_id,
                           'forward_deployment_id': opaque_evidence.get('active_deployment_id', ''),
                           'dispatch_id': pending['dispatch_id'],
                           'observation_sha256': digest(encode(fresh_observation))}
                new_state = {**state, 'phase': 'active_unverified', 'pending_intent': None,
                             'forward_receipt': receipt,
                             'generation': operation_key.start_generation + 1,
                             'revision': state['revision'] + 1}
                return new_state, None
            elif pending['effect_kind'] == 'rollback':
                active_id = opaque_evidence.get('active_version_id')
                receipt = {'rollback_version_id': active_id,
                           'rollback_deployment_id': opaque_evidence.get('active_deployment_id', ''),
                           'dispatch_id': pending['dispatch_id'],
                           'forward_receipt_sha256': digest(encode(state['forward_receipt'])),
                           'observation_sha256': digest(encode(fresh_observation))}
                new_state = {**state, 'phase': 'rolled_back', 'pending_intent': None,
                             'rollback_receipt': receipt,
                             'generation': operation_key.start_generation + 2,
                             'revision': state['revision'] + 1}
                return new_state, None
        elif classification == 'not_applied_final':
            prior = {'stage_pending': 'prepared', 'activate_pending': 'staged',
                     'rollback_pending': 'active_unverified'}.get(state['phase'], state['phase'])
            new_state = {**state, 'phase': prior, 'pending_intent': None,
                         'revision': state['revision'] + 1}
            return new_state, None
        else:
            new_state = {**state, 'phase': 'reconciliation_required',
                         'revision': state['revision'] + 1}
            return new_state, None
    elif state['phase'] == 'reconciliation_required':
        classification = _classify_pending(state, fresh_observation, opaque_evidence)
        if classification == 'applied':
            return _decide_reconcile(state, fresh_observation, opaque_evidence, operation_key)
        elif classification == 'not_applied_final':
            prior = state.get('resume_phase', 'empty')
            new_state = {**state, 'phase': prior, 'pending_intent': None,
                         'revision': state['revision'] + 1}
            return new_state, None
        else:
            return state, None
    else:
        return state, None


def decide(state, command, observation, operation_evidence):
    require(type(state) is dict, 'invalid_state')
    require(type(command) is str, 'invalid_command')
    _state_check(state, operation_evidence.get('operation_key'))

    if command == 'prepare':
        return _decide_prepare(state, observation,
                               operation_evidence.get('operation_key'))
    elif command == 'complete_prepare':
        return _decide_complete_prepare(state, observation,
                                        operation_evidence.get('original_plan'))
    elif command == 'stage':
        return _decide_stage(state, operation_evidence.get('captured_candidate_packet'),
                             operation_evidence.get('opaque_handles'),
                             operation_evidence.get('operation_key'))
    elif command == 'complete_stage':
        return _decide_complete_stage(state, fresh_observation=observation,
                                      opaque_evidence=operation_evidence.get('opaque_evidence', {}),
                                      operation_key=operation_evidence.get('operation_key'))
    elif command == 'activate':
        return _decide_activate(state, operation_evidence.get('fixture_decision'),
                                operation_evidence.get('operation_key'))
    elif command == 'complete_activate':
        return _decide_complete_activate(state, fresh_observation=observation,
                                         opaque_evidence=operation_evidence.get('opaque_evidence', {}),
                                         operation_key=operation_evidence.get('operation_key'))
    elif command == 'verify':
        return _decide_verify(state, observation, operation_evidence.get('operation_key'))
    elif command == 'rollback':
        return _decide_rollback(state, operation_evidence.get('fixture_decision'),
                                operation_evidence.get('operation_key'))
    elif command == 'complete_rollback':
        return _decide_complete_rollback(state, fresh_observation=observation,
                                         opaque_evidence=operation_evidence.get('opaque_evidence', {}),
                                         operation_key=operation_evidence.get('operation_key'))
    elif command == 'reconcile':
        return _decide_reconcile(state, fresh_observation=observation,
                                 opaque_evidence=operation_evidence.get('opaque_evidence', {}),
                                 operation_key=operation_evidence.get('operation_key'))
    else:
        require(False, 'unknown_command')
