"""Private, self-created fixture and synthetic provider for exactly one Commons transition.

Reuses bounded POSIX primitives from static_posix with own anchors, lock, and limits.
The control journal and simulated provider state are in separate atomically replaced files.
No real Cloudflare API, Worker code execution, SQL, or D1 mutations.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import copy
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid as _uuid

import commons_artifact as artifact
import commons_plan as planner
import commons_transition as transition
from commons_transition import FixtureDecision, OperationKey
from site_artifact import ArtifactError, require
from static_posix import (DIR_FLAGS, identity, metadata, read_file, read_json,
                          save_json, write_new)

JSON_LIMIT = 2 * 1024 * 1024
PROVIDER_LIMIT = 4 * 1024 * 1024


@dataclass(frozen=True)
class Handle:
    container: str
    nonce: str
    anchors: dict
    lock_identity: list


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _encode(value):
    return _canonical(value).encode('utf-8')


def _digest(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def _open_directory(path):
    descriptor = os.open(str(path), DIR_FLAGS)
    try:
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def check_anchors(handle, fds):
    for name, expected in handle.anchors.items():
        path = Path(handle.container) if name == 'container' else Path(handle.container) / name
        fresh = _open_directory(path)
        try:
            info = os.fstat(fresh)
            require(identity(info) == expected and identity(os.fstat(fds[name])) == expected,
                    'fixture_identity_changed')
            require(info.st_uid == os.getuid(), 'unsafe_fixture')
            if name not in ('container', 'provider'):
                require(stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_fixture')
        finally:
            os.close(fresh)
    marker, _record, _inode = read_file(fds['control'], 'owner', 128)
    require(marker == handle.nonce.encode('ascii'), 'fixture_identity_changed')
    lock = os.stat('lock', dir_fd=fds['control'], follow_symlinks=False)
    require(stat.S_ISREG(lock.st_mode) and lock.st_nlink == 1
            and identity(lock) == handle.lock_identity
            and stat.S_IMODE(lock.st_mode) == 0o600, 'unsafe_control')
    sentinel, _record, _inode = read_file(fds['peer'], 'sentinel', 128)
    require(sentinel == b'unrelated fixture: preserve\n', 'peer_changed')


@contextmanager
def locked(handle):
    require(type(handle) is Handle, 'invalid_fixture_handle')
    fds, lock_fd = {}, None
    try:
        for name in ('container', 'control', 'provider', 'peer'):
            path = Path(handle.container) if name == 'container' else Path(handle.container) / name
            fds[name] = _open_directory(path)
        check_anchors(handle, fds)
        lock_fd = os.open('lock', os.O_RDWR | os.O_NOFOLLOW, dir_fd=fds['control'])
        info = os.fstat(lock_fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and identity(info) == handle.lock_identity
                and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_control')
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ArtifactError('fixture_busy') from None
        check_anchors(handle, fds)
        yield fds
    except OSError:
        raise ArtifactError('fixture_io_failed') from None
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        for fd in fds.values():
            os.close(fd)


def _build_initial_provider(initial_capture, baseline, target_policy, opaque_refs):
    require(type(initial_capture) is dict, 'invalid_capture')
    require(type(baseline) is dict, 'invalid_baseline')
    planner.validate_baseline(baseline)
    require(type(target_policy) is dict, 'invalid_policy')
    planner.validate_policy(target_policy)
    require(type(opaque_refs) is dict and 'ADMIN_TOKEN' in opaque_refs
            and 'IP_HMAC_SECRET' in opaque_refs, 'invalid_refs')
    version_id = baseline['version_id']
    deployment_id = baseline['deployment_id']
    version_etag = initial_capture['version']['etag']
    provider = {
        'schema_version': 1, 'target': planner.TARGET,
        'account_id': target_policy['account_id'], 'zone_id': target_policy['zone_id'],
        'script_name': target_policy['script_name'], 'database_id': target_policy['database_id'],
        'route_id': target_policy['route_id'],
        'versions': {version_id: {
            'id': version_id, 'etag': version_etag,
            'bindings': copy.deepcopy(initial_capture['version']['bindings']),
            'runtime': copy.deepcopy(initial_capture['version']['runtime']),
            'modules': copy.deepcopy(initial_capture['modules']),
        }},
        'deployments': [{'id': deployment_id, 'strategy': 'percentage',
                         'versions': [{'version_id': version_id, 'percentage': 100}]}],
        'latest_version_id': version_id, 'active_version_id': version_id,
        'active_deployment_id': deployment_id,
        'settings': copy.deepcopy(initial_capture['settings']),
        'routes': copy.deepcopy(initial_capture['routes']),
        'schedules': copy.deepcopy(initial_capture['schedules']),
        'subdomain': copy.deepcopy(initial_capture['subdomain']),
        'schema': copy.deepcopy(initial_capture['schema']),
        'opaque_refs': copy.deepcopy(opaque_refs),
        'intents': {},
    }
    return provider


def _provider_observe(provider):
    version_id = provider['active_version_id']
    version = provider['versions'][version_id]
    return {
        'schema_version': 1, 'target': planner.TARGET,
        'account_id': provider['account_id'], 'zone_id': provider['zone_id'],
        'script_name': provider['script_name'],
        'deployment': {'id': provider['active_deployment_id'], 'strategy': 'percentage',
                       'versions': [{'version_id': version_id, 'percentage': 100}]},
        'version': {'id': version_id, 'etag': version['etag'],
                    'bindings': copy.deepcopy(version['bindings']),
                    'runtime': copy.deepcopy(version['runtime'])},
        'latest_version_id': provider['latest_version_id'],
        'settings': copy.deepcopy(provider['settings']),
        'routes': copy.deepcopy(provider['routes']),
        'schedules': copy.deepcopy(provider['schedules']),
        'subdomain': copy.deepcopy(provider['subdomain']),
        'modules': copy.deepcopy(version['modules']),
        'schema': copy.deepcopy(provider['schema']),
    }


def _provider_opaque_evidence(provider):
    return {
        'latest_version_id': provider['latest_version_id'],
        'active_version_id': provider['active_version_id'],
        'active_deployment_id': provider['active_deployment_id'],
    }


def _provider_stage(provider, intent, captured_candidate_packet, opaque_handles, candidate_commit):
    require(provider.get('intents', {}).get(str(intent['dispatch_id'])) is None, 'duplicate_dispatch')
    candidate_files, candidate_descriptor = artifact.unpack(
        captured_candidate_packet, candidate_commit, planner.SCHEMA_SHA256)
    new_id = str(_uuid.uuid4())
    settings = planner.settings({'database_id': provider['database_id'],
                                  'account_id': provider['account_id'],
                                  'zone_id': provider['zone_id'],
                                  'script_name': provider['script_name'],
                                  'route_id': provider['route_id']},
                                 candidate_descriptor['commit'])
    version = {
        'id': new_id, 'etag': _digest(_encode(candidate_descriptor)),
        'bindings': copy.deepcopy(settings['bindings']),
        'runtime': copy.deepcopy({'compatibility_date': settings['compatibility_date'],
                                   'compatibility_flags': settings['compatibility_flags'],
                                   'usage_model': settings['usage_model']}),
        'modules': copy.deepcopy(candidate_descriptor['modules']),
    }
    provider['versions'][new_id] = version
    provider['latest_version_id'] = new_id
    provider['intents'][str(intent['dispatch_id'])] = {
        'effect_kind': 'stage', 'result': 'applied',
        'result_data': {'staged_version_id': new_id},
    }
    return provider, {'staged_version_id': new_id}


def _provider_activate(provider, intent, staged_version_id):
    require(provider.get('intents', {}).get(str(intent['dispatch_id'])) is None, 'duplicate_dispatch')
    require(staged_version_id in provider['versions'], 'unknown_staged_version')
    require(staged_version_id == provider['latest_version_id'], 'stale_staged_version')
    new_deployment_id = str(_uuid.uuid4())
    provider['deployments'].append({
        'id': new_deployment_id, 'strategy': 'percentage',
        'versions': [{'version_id': staged_version_id, 'percentage': 100}],
    })
    provider['active_version_id'] = staged_version_id
    provider['active_deployment_id'] = new_deployment_id
    provider['intents'][str(intent['dispatch_id'])] = {
        'effect_kind': 'activate', 'result': 'applied',
        'result_data': {'active_version_id': staged_version_id,
                        'active_deployment_id': new_deployment_id},
    }
    return provider


def _provider_restore(provider, intent, original_version_id):
    require(provider.get('intents', {}).get(str(intent['dispatch_id'])) is None, 'duplicate_dispatch')
    require(original_version_id in provider['versions'], 'unknown_original_version')
    new_deployment_id = str(_uuid.uuid4())
    provider['deployments'].append({
        'id': new_deployment_id, 'strategy': 'percentage',
        'versions': [{'version_id': original_version_id, 'percentage': 100}],
    })
    provider['active_version_id'] = original_version_id
    provider['active_deployment_id'] = new_deployment_id
    provider['intents'][str(intent['dispatch_id'])] = {
        'effect_kind': 'rollback', 'result': 'applied',
        'result_data': {'active_version_id': original_version_id,
                        'active_deployment_id': new_deployment_id},
    }
    return provider


def _build_initial_state(nonce, baseline, target_policy, candidate_packet_sha256,
                         predecessor_packet_sha256, original_plan, operation_key,
                         original_observation_sha256):
    require(type(operation_key) is OperationKey, 'invalid_operation_key')
    return {
        'schema_version': 1, 'nonce': nonce,
        'operation_key': {
            'operation_id': operation_key.operation_id,
            'plan_sha256': operation_key.plan_sha256,
            'start_generation': operation_key.start_generation,
        },
        'phase': 'empty', 'fence_epoch': 0, 'revision': 0,
        'generation': operation_key.start_generation, 'dispatch_count': 0,
        'start_baseline': copy.deepcopy(baseline),
        'target_policy': copy.deepcopy(target_policy),
        'original_plan': copy.deepcopy(original_plan),
        'candidate_packet_sha256': candidate_packet_sha256,
        'predecessor_packet_sha256': predecessor_packet_sha256,
        'original_observation_sha256': original_observation_sha256,
        'pending_intent': None,
    }


@contextmanager
def create_fixture(initial_capture, trusted_baseline, target_policy, synthetic_opaque_refs):
    require(type(initial_capture) is dict, 'invalid_capture')
    require(type(trusted_baseline) is dict, 'invalid_baseline')
    require(type(target_policy) is dict, 'invalid_policy')
    require(type(synthetic_opaque_refs) is dict, 'invalid_refs')
    planner.validate_baseline(trusted_baseline)
    planner.validate_policy(target_policy)

    provider = _build_initial_provider(initial_capture, trusted_baseline,
                                       target_policy, synthetic_opaque_refs)
    root = Path(tempfile.mkdtemp(prefix='oss-commons-fixture-'))
    anchor = identity(root.stat())
    try:
        for name in ('control', 'provider', 'peer'):
            (root / name).mkdir(mode=0o700)
        nonce = _uuid.uuid4().hex
        control_fd = _open_directory(root / 'control')
        provider_fd = _open_directory(root / 'provider')
        peer_fd = _open_directory(root / 'peer')
        try:
            write_new(control_fd, 'owner', nonce.encode())
            lock_id = write_new(control_fd, 'lock', b'')
            write_new(peer_fd, 'sentinel', b'unrelated fixture: preserve\n')
            save_json(provider_fd, 'provider.json', provider)
            save_json(control_fd, 'state.json', {
                'schema_version': 1, 'nonce': nonce,
                'start_baseline': copy.deepcopy(trusted_baseline),
                'target_policy': copy.deepcopy(target_policy),
                'phase': 'empty', 'fence_epoch': 0, 'revision': 0,
                'generation': trusted_baseline['generation'], 'dispatch_count': 0,
                'pending_intent': None,
            })
        finally:
            os.close(control_fd)
            os.close(provider_fd)
            os.close(peer_fd)
        anchors = {'container': anchor,
                   'control': identity((root / 'control').stat()),
                   'provider': identity((root / 'provider').stat()),
                   'peer': identity((root / 'peer').stat())}
        require(len({tuple(value) for value in anchors.values()}) == 4
                and len({value[0] for value in anchors.values()}) == 1, 'unsafe_fixture')
        yield Handle(str(root), nonce, anchors, lock_id)
    except OSError:
        raise ArtifactError('fixture_io_failed') from None
    finally:
        require(not root.is_symlink() and identity(root.stat()) == anchor, 'fixture_identity_changed')
        require(shutil.rmtree.avoids_symlink_attacks, 'unsupported_filesystem')
        shutil.rmtree(root)


@contextmanager
def session(handle, checkpoint=None):
    require(type(handle) is Handle, 'invalid_fixture_handle')
    with locked(handle) as fds:
        yield Session(handle, fds, checkpoint)


class Session:
    def __init__(self, handle, fds, checkpoint):
        self.handle = handle
        self.fds = fds
        self.checkpoint = checkpoint or (lambda _name: None)
        self._state = None
        self._provider = None
        self._load()

    def _load(self):
        check_anchors(self.handle, self.fds)
        self._provider = read_json(self.fds['provider'], 'provider.json')
        require(type(self._provider) is dict and self._provider.get('schema_version') == 1,
                'invalid_provider')
        try:
            self._state = read_json(self.fds['control'], 'state.json')
        except (FileNotFoundError, ArtifactError):
            self._state = None

    def _save_state(self):
        check_anchors(self.handle, self.fds)
        save_json(self.fds['control'], 'state.json', self._state)

    def _save_provider(self):
        check_anchors(self.handle, self.fds)
        raw = _canonical(self._provider)
        require(len(raw) <= PROVIDER_LIMIT, 'provider_limit')
        save_json(self.fds['provider'], 'provider.json', self._provider)

    def _operation_key(self):
        ok = self._state['operation_key']
        return OperationKey(ok['operation_id'], ok['plan_sha256'], ok['start_generation'])

    def _observe(self):
        return _provider_observe(self._provider)

    def _opaque_evidence(self):
        return _provider_opaque_evidence(self._provider)

    def _status(self):
        return self._state['phase']

    def status(self):
        return self._status()

    def prepare(self, candidate_packet, expected_candidate_commit,
                expected_candidate_packet_sha256,
                predecessor_packet, expected_predecessor_packet_sha256, operation_key):
        require(type(candidate_packet) is bytes, 'invalid_packet')
        require(type(predecessor_packet) is bytes, 'invalid_packet')
        require(type(operation_key) is OperationKey, 'invalid_operation_key')
        require(artifact.digest(candidate_packet) == expected_candidate_packet_sha256, 'packet_mismatch')
        require(artifact.digest(predecessor_packet) == expected_predecessor_packet_sha256, 'packet_mismatch')
        observation = self._observe()
        require(self._state.get('phase') == 'empty', 'already_prepared')
        baseline = self._state['start_baseline']
        target_policy = self._state['target_policy']
        plan = planner.build_plan(candidate_packet=candidate_packet,
                                  expected_candidate_commit=expected_candidate_commit,
                                  expected_candidate_packet_sha256=expected_candidate_packet_sha256,
                                  predecessor_packet=predecessor_packet,
                                  baseline=baseline,
                                  observation=observation,
                                  target_policy=target_policy)
        require(plan['plan_sha256'] == operation_key.plan_sha256, 'plan_mismatch')
        initial_state = _build_initial_state(
            self.handle.nonce, baseline, target_policy,
            expected_candidate_packet_sha256, expected_predecessor_packet_sha256,
            plan, operation_key,
            artifact.digest(artifact.encode(observation)))
        self._state = initial_state
        self._candidate_packet = candidate_packet
        self._candidate_commit = expected_candidate_commit
        self._save_state()
        new_state, intent = transition.decide(
            self._state, 'prepare', observation,
            {'operation_key': operation_key})
        require(new_state is not None and new_state['phase'] == 'preparing', 'prepare_failed')
        self._state = new_state
        self._save_state()
        self.checkpoint('prepared')
        new_state, intent = transition.decide(
            self._state, 'complete_prepare', observation,
            {'original_plan': plan})
        require(new_state is not None and new_state['phase'] == 'prepared', 'complete_prepare_failed')
        self._state = new_state
        self._save_state()
        return {'phase': 'prepared', 'plan_sha256': plan['plan_sha256']}

    def stage(self, operation_key):
        require(self._state['phase'] == 'prepared', 'not_prepared')
        require(type(operation_key) is OperationKey, 'invalid_operation_key')
        require(operation_key == self._operation_key(), 'wrong_operation_key')
        opaque_handles = copy.deepcopy(self._provider['opaque_refs'])
        new_state, intent = transition.decide(
            self._state, 'stage', self._observe(),
            {'operation_key': operation_key, 'opaque_handles': opaque_handles,
             'captured_candidate_packet': self._candidate_packet})
        require(new_state is not None and new_state['phase'] == 'stage_pending', 'stage_failed')
        require(intent is not None, 'stage_no_intent')
        self._state = new_state
        self._save_state()
        self.checkpoint('stage_intent')
        dispatch_id = intent['dispatch_id']
        require(dispatch_id not in self._provider.get('intents', {}), 'duplicate_dispatch')
        self._provider, _stage_result = _provider_stage(self._provider, intent,
                                          self._candidate_packet, opaque_handles,
                                          self._candidate_commit)
        self._save_provider()
        self.checkpoint('stage_effect')
        observation = self._observe()
        evidence = self._opaque_evidence()
        new_state, intent = transition.decide(
            self._state, 'complete_stage', observation,
            {'operation_key': operation_key, 'opaque_evidence': evidence})
        require(new_state is not None and new_state['phase'] == 'staged', 'complete_stage_failed')
        self._state = new_state
        self._save_state()
        return {'phase': 'staged', 'staged_version_id': self._state.get('stage_receipt', {}).get('staged_version_id')}

    def activate(self, operation_key, fixture_decision):
        require(self._state['phase'] == 'staged', 'not_staged')
        require(type(operation_key) is OperationKey, 'invalid_operation_key')
        require(operation_key == self._operation_key(), 'wrong_operation_key')
        require(type(fixture_decision) is FixtureDecision, 'invalid_decision')
        new_state, intent = transition.decide(
            self._state, 'activate', self._observe(),
            {'operation_key': operation_key, 'fixture_decision': fixture_decision})
        require(new_state is not None and new_state['phase'] == 'activate_pending', 'activate_failed')
        require(intent is not None, 'activate_no_intent')
        self._state = new_state
        self._save_state()
        self.checkpoint('activate_intent')
        staged_id = self._state['stage_receipt']['staged_version_id']
        self._provider = _provider_activate(self._provider, intent, staged_id)
        self._save_provider()
        self.checkpoint('activate_effect')
        observation = self._observe()
        evidence = self._opaque_evidence()
        new_state, intent = transition.decide(
            self._state, 'complete_activate', observation,
            {'operation_key': operation_key, 'opaque_evidence': evidence})
        require(new_state is not None and new_state['phase'] in ('active_unverified', 'reconciliation_required'),
                'complete_activate_failed')
        self._state = new_state
        self._save_state()
        return {'phase': self._state['phase']}

    def verify(self, operation_key):
        require(self._state['phase'] in ('active_unverified', 'forward_verified'), 'not_verifiable')
        require(type(operation_key) is OperationKey, 'invalid_operation_key')
        require(operation_key == self._operation_key(), 'wrong_operation_key')
        observation = self._observe()
        new_state, intent = transition.decide(
            self._state, 'verify', observation,
            {'operation_key': operation_key})
        require(new_state is not None and new_state['phase'] == 'forward_verified', 'verify_failed')
        self._state = new_state
        self._save_state()
        return {'phase': 'forward_verified'}

    def rollback(self, operation_key, fixture_decision):
        require(self._state['phase'] in ('active_unverified', 'forward_verified'), 'not_rollbackable')
        require(type(operation_key) is OperationKey, 'invalid_operation_key')
        require(operation_key == self._operation_key(), 'wrong_operation_key')
        require(type(fixture_decision) is FixtureDecision, 'invalid_decision')
        new_state, intent = transition.decide(
            self._state, 'rollback', self._observe(),
            {'operation_key': operation_key, 'fixture_decision': fixture_decision})
        require(new_state is not None and new_state['phase'] == 'rollback_pending', 'rollback_failed')
        require(intent is not None, 'rollback_no_intent')
        self._state = new_state
        self._save_state()
        self.checkpoint('rollback_intent')
        original_id = self._state['start_baseline']['version_id']
        self._provider = _provider_restore(self._provider, intent, original_id)
        self._save_provider()
        self.checkpoint('rollback_effect')
        observation = self._observe()
        evidence = self._opaque_evidence()
        new_state, intent = transition.decide(
            self._state, 'complete_rollback', observation,
            {'operation_key': operation_key, 'opaque_evidence': evidence})
        require(new_state is not None and new_state['phase'] in ('rolled_back', 'reconciliation_required'),
                'complete_rollback_failed')
        self._state = new_state
        self._save_state()
        return {'phase': self._state['phase']}

    def reconcile(self, operation_key):
        require(type(operation_key) is OperationKey, 'invalid_operation_key')
        require(operation_key == self._operation_key(), 'wrong_operation_key')
        observation = self._observe()
        evidence = self._opaque_evidence()
        new_state, intent = transition.decide(
            self._state, 'reconcile', observation,
            {'operation_key': operation_key, 'opaque_evidence': evidence})
        require(new_state is not None, 'reconcile_failed')
        self._state = new_state
        self._save_state()
        return {'phase': self._state['phase']}
