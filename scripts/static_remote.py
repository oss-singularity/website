"""Fixed-target remote filesystem operations behind a restricted SSH command.

Target bindings, the initial baseline, descriptor and static policy are installed
by a separate operator. No request can initialize, rebind or reset them.
This module never imports or executes payload code or accepts a filesystem path
from an SSH request. Publication provenance and HTTP verification remain client
release gates; this endpoint reports filesystem outcomes only.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import sys

import static_engine as engine
import static_plan as plan
import static_policy as policy
import static_posix as fs
import static_retention as retention
import static_remote_observer as observer
from site_artifact import ArtifactError, MANIFEST, open_directory, require

COMMAND = 'oss-static-release-v1'
MAX_REQUEST = 12 * 1024 * 1024
PUBLIC_ERRORS = {'command_rejected', 'invalid_arguments', 'unsupported_runtime',
                 'invalid_request', 'invalid_encoding', 'candidate_limit', 'non_static_path',
                 'server_configuration_changed', 'nested_server_configuration',
                 'stale_attempt', 'generation_conflict', 'baseline_mismatch',
                 'unfinished_attempt', 'attempt_limit', 'reconciliation_required',
                 'target_busy', 'request_timeout', 'invalid_policy', 'unsafe_control',
                 'identity_changed', 'target_conflict', 'release_failed',
                 'maintenance_required', 'maintenance_conflict'}


def json_value(raw, code):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, code)
            result[key] = value
        return result

    def invalid(_value):
        raise ArtifactError(code)

    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError):
        raise ArtifactError(code) from None
    count = 0

    def bounded(item, depth):
        nonlocal count
        count += 1
        require(count <= 1024 and depth <= 12, code)
        if type(item) is dict:
            for child in item.values():
                bounded(child, depth + 1)
        elif type(item) is list:
            for child in item:
                bounded(child, depth + 1)
    bounded(value, 0)
    return value


def private_file(path, limit):
    path = Path(observer.absolute(str(path)))
    parent = observer.open_directory(str(path.parent))
    try:
        info = os.fstat(parent)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
                'unsafe_control')
        return observer.capture_file(parent, path.name, limit, private=True)[0]
    finally:
        os.close(parent)


@dataclass(frozen=True)
class Binding:
    """Trusted installed configuration, never deserialized from a request."""
    config_path: str
    config_bytes: bytes
    policy_path: str
    policy_bytes: bytes
    config: dict
    policy: dict
    nonce: str
    runtime: str


def load_binding(config_path, runtime):
    config_path = observer.absolute(config_path)
    runtime = observer.absolute(runtime)
    raw = private_file(config_path, observer.MAX_CONFIG)
    config = observer.load_config(config_path)
    # These two private files are selected only by the installed command.
    policy_path = str(Path(config_path).with_name('policy.json'))
    raw_policy = private_file(policy_path, 32768)
    installed_policy = json_value(raw_policy, 'invalid_policy')
    policy.validate_policy(installed_policy)
    expected_ancestors = {str(parent) for parent in Path(config['root']).parents}
    require(set(installed_policy['ancestor_htaccess']) == expected_ancestors, 'invalid_policy')
    for source in (config_path, policy_path, runtime):
        for target in (config['root'], config['control']):
            require(os.path.commonpath([source, target]) not in {source, target}, 'unsafe_control')
    require(os.stat(config['root']).st_dev == os.stat(config['control']).st_dev,
            'unsafe_control')
    control = open_directory(config['control'])
    try:
        owner, record, _ = fs.read_file(control, 'owner', 128)
        require(record['mode'] == 0o600 and record['uid'] == os.getuid(), 'unsafe_control')
        nonce = owner.decode('ascii')
        plan.hex_value(nonce, 32, 'unsafe_control')
    finally:
        os.close(control)
    return Binding(config_path, raw, policy_path, raw_policy, config,
                   installed_policy, nonce, runtime)


def check_anchors(binding, fds):
    require(type(binding) is Binding, 'unsafe_control')
    observer.verify_anchors(binding.config, fds['target'], fds['control'], fds['lock'])
    require(private_file(binding.config_path, observer.MAX_CONFIG) == binding.config_bytes
            and private_file(binding.policy_path, 32768) == binding.policy_bytes,
            'server_configuration_changed')
    owner, record, _ = fs.read_file(fds['control'], 'owner', 128)
    require(owner == binding.nonce.encode('ascii') and record['mode'] == 0o600
            and record['uid'] == os.getuid(), 'unsafe_control')
    access, _record, _ = fs.read_file(fds['target'], '.htaccess')
    policy.validate_installed_access(access, binding.policy)
    for directory, expected in binding.policy['ancestor_htaccess'].items():
        parent = open_directory(directory)
        try:
            try:
                raw, _record, _ = fs.read_file(parent, '.htaccess')
                observed = plan.digest(raw)
            except FileNotFoundError:
                observed = None
            require(observed == expected, 'server_configuration_changed')
        finally:
            os.close(parent)


@contextmanager
def locked(binding):
    fds = {}
    try:
        fds['target'] = open_directory(binding.config['root'])
        fds['control'] = open_directory(binding.config['control'])
        fds['lock'] = os.open('lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                              dir_fd=fds['control'])
        check_anchors(binding, fds)
        try:
            fcntl.flock(fds['lock'], fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ArtifactError('target_busy') from None
        check_anchors(binding, fds)
        yield fds
    finally:
        for fd in fds.values():
            os.close(fd)


def ticket(value):
    plan.object_fields(value, {'identity', 'plan_sha256', 'generation'}, 'invalid_request')
    plan.hex_value(value['identity'], 32, 'invalid_request')
    plan.hex_value(value['plan_sha256'], 64, 'invalid_request')
    plan.integer(value['generation'], 1, 2**63 - 1, 'invalid_request')
    return engine.Attempt(**value)


def payload(value):
    require(type(value) is dict and 0 < len(value) <= plan.MAX_FILES, 'invalid_request')
    result, total = {}, 0
    for name, encoded in value.items():
        require(policy.allowed_path(name), 'non_static_path')
        raw = policy.decode(encoded, policy.MAX_STATIC_FILE_BYTES)
        total += len(raw)
        require(total <= policy.MAX_CANDIDATE_BYTES, 'candidate_limit')
        result[name] = raw
    return result


class RemoteTransition(engine.Transition):
    def __init__(self, binding, fds, checkpoint=None):
        super().__init__(binding, fds, checkpoint, check_anchors)

    def _load(self):
        super()._load()
        require(self.state['creation_metadata'] == {
            'file': {'mode': 0o644, 'uid': os.getuid(), 'gid': os.getgid()},
            'directory': {'mode': 0o755, 'uid': os.getuid(), 'gid': os.getgid()}},
            'unsafe_control')

    def _allocate_material(self, attempt_id):
        retention.allocation(self, attempt_id)
        identity = super()._allocate_material(attempt_id)
        self._event('allocation-created')
        return identity

    def maintain(self, expected):
        return retention.maintain(self, expected)

    def _validate_target(self, forward=None, backward=None):
        entries, identities = super()._validate_target(forward, backward)
        policy.validate_parents((item['path'] for item in self.attempt['plan']['operations']), entries)
        return entries, identities

    def _descriptor(self, digest):
        plan.hex_value(digest, 64, 'invalid_journal')
        raw, record, _ = fs.read_file(self.fds['control'], 'descriptor-' + digest, 4096)
        require(record['mode'] == 0o600 and record['uid'] == os.getuid()
                and plan.digest(raw) == digest, 'unsafe_control')
        return raw

    def _remember_descriptor(self, raw):
        name = 'descriptor-' + plan.digest(raw)
        try:
            existing = self._descriptor(plan.digest(raw))
            require(existing == raw, 'unsafe_control')
        except FileNotFoundError:
            fs.write_new(self.fds['control'], name, raw)

    def predecessor(self):
        raw, _record, _ = fs.read_file(self.fds['target'], MANIFEST, plan.MAX_MANIFEST_BYTES)
        names = observer.parse_manifest(raw)
        files = {MANIFEST: raw}
        for name in names:
            parent = observer.relative_parent(self.fds['target'], name, {})
            try:
                files[name] = fs.read_file(parent, name.split('/')[-1])[0]
            finally:
                os.close(parent)
        installed, _record, _ = fs.read_file(self.fds['target'], '.htaccess')
        files['.htaccess'] = policy.validate_installed_access(installed, self.handle.policy)
        policy.validate_candidate(files, self.handle.policy)
        return files, self._descriptor(self.state['baseline']['descriptor_sha256'])

    def prepare_request(self, request):
        fields = {'schema', 'operation', 'identity', 'expected_generation',
                  'expected_predecessor_commit', 'expected_manifest_sha256',
                  'candidate_commit', 'candidate_descriptor', 'files'}
        plan.object_fields(request, fields, 'invalid_request')
        plan.hex_value(request['identity'], 32, 'invalid_request')
        plan.integer(request['expected_generation'], 1, 2**63 - 3, 'invalid_request')
        plan.hex_value(request['expected_predecessor_commit'], 40, 'invalid_request')
        plan.hex_value(request['expected_manifest_sha256'], 64, 'invalid_request')
        plan.hex_value(request['candidate_commit'], 40, 'invalid_request')
        candidate = payload(request['files'])
        policy.validate_candidate(candidate, self.handle.policy)
        description = policy.decode(request['candidate_descriptor'], 4096)
        parsed = plan.descriptor(description, candidate)
        require(parsed['commit'] == request['candidate_commit'], 'invalid_request')
        self._load()
        if self.state['attempt'] is not None and self.attempt['id'] == request['identity']:
            expected = self.attempt['start_baseline']
            require(self.attempt['plan']['candidate']['descriptor_sha256'] == plan.digest(description),
                    'stale_attempt')
        else:
            expected = self.state['baseline']
        require(request['expected_generation'] == expected['generation'], 'generation_conflict')
        require(request['expected_predecessor_commit'] == expected['commit']
                and request['expected_manifest_sha256'] == expected['manifest_sha256'], 'baseline_mismatch')
        if self.state['attempt'] is None or self.attempt['id'] != request['identity']:
            require(self.state['attempt'] is None or self.attempt['phase'] in engine.TERMINAL,
                    'unfinished_attempt')
            require(self.state['attempt_count'] < 8, 'attempt_limit')
            predecessor, previous_description = self.predecessor()
            _root, entries, contents, _identities = fs.snapshot(self.fds['target'])
            policy.validate_replacement(contents['.htaccess'], candidate['.htaccess'], self.handle.policy)
            policy.validate_parents(candidate, entries)
            for name in candidate:
                if name in entries:
                    record = entries[name]
                    require(record['kind'] == 'file' and record['mode'] == 0o644
                            and record['uid'] == os.getuid() and record['gid'] == os.getgid(),
                            'unsafe_control')
                for parent in plan.parents(name):
                    if parent in entries:
                        record = entries[parent]
                        require(record['kind'] == 'directory' and record['mode'] == 0o755
                                and record['uid'] == os.getuid(), 'unsafe_control')
            self._prepare_captured(candidate=candidate, candidate_descriptor=description,
                expected_candidate_commit=request['candidate_commit'], predecessor=predecessor,
                predecessor_descriptor=previous_description, attempt_identity=request['identity'])
        # Descriptor objects are installed only after the complete plan and
        # predecessor checks succeed; registered retired objects can be pruned.
        # A lost prepare response can be queried by the caller's original ID.
        self._remember_descriptor(description)
        retention.recover_allocation(self)
        return self.report(include_ticket=True)

    def apply(self, expected):
        self._load()
        self._require_ticket(expected)
        self._descriptor(self.attempt['plan']['candidate']['descriptor_sha256'])
        return super().apply(expected)

    def report(self, include_ticket=False):
        result = {**super().report(), 'kind': 'static-remote-transition',
                  'filesystem_only': True, 'publication_verified': False,
                  'baseline_commit': self.state['baseline']['commit'],
                  'baseline_manifest_sha256': self.state['baseline']['manifest_sha256'],
                  'maintenance_pending': retention.optional(self.fds['control'], retention.RETENTION) is not None}
        if include_ticket:
            expected = self.ticket()
            result['ticket'] = {'identity': expected.identity, 'generation': expected.generation,
                                'plan_sha256': expected.plan_sha256}
            result['candidate_descriptor_sha256'] = self.attempt['plan']['candidate']['descriptor_sha256']
        return result


def dispatch(binding, request, checkpoint=None):
    require(type(request) is dict and type(request.get('schema')) is int
            and request['schema'] == 1, 'invalid_request')
    operation = request.get('operation')
    require(type(operation) is str
            and operation in {'status', 'prepare', 'apply', 'reconcile', 'rollback', 'maintain'}, 'invalid_request')
    with locked(binding) as fds:
        transition = RemoteTransition(binding, fds, checkpoint)
        retention.recover_allocation(transition)
        require(operation in {'status', 'maintain'} or retention.optional(fds['control'], retention.RETENTION) is None,
                'maintenance_required')
        if operation == 'prepare':
            return transition.prepare_request(request)
        if operation == 'status':
            plan.object_fields(request, {'schema', 'operation', 'identity'}, 'invalid_request')
            identity = request['identity']
            if identity is not None:
                plan.hex_value(identity, 32, 'invalid_request')
                require(transition.state['attempt'] is not None
                        and transition.attempt['id'] == identity, 'stale_attempt')
            return transition.report(include_ticket=identity is not None)
        plan.object_fields(request, {'schema', 'operation', 'ticket'}, 'invalid_request')
        expected = ticket(request['ticket'])
        return getattr(transition, operation)(expected)


def main(config_path, runtime):
    require(os.environ.get('SSH_ORIGINAL_COMMAND') == COMMAND, 'command_rejected')
    require(sys.version_info >= (3, 11) and sys.flags.isolated and sys.flags.no_site,
            'unsupported_runtime')

    def timeout(_signum, _frame):
        raise ArtifactError('request_timeout')
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(60)
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        require(0 < len(raw) <= MAX_REQUEST, 'invalid_request')
        request = json_value(raw, 'invalid_request')
        binding = load_binding(config_path, runtime)
        return dispatch(binding, request)
    finally:
        signal.alarm(0)
