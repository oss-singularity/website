"""Journaled filesystem transitions for trusted, separately anchored backends.

This internal engine never opens a target from a request. Entry points supply
bound descriptors, an anchor check, captured bytes and independently validated
preconditions. The fixture and remote entry points establish different trust
boundaries; neither may borrow the other's handles.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import os
import uuid

import static_posix as fs
import static_plan as plan
from site_artifact import ArtifactError, require

TERMINAL = {'verified', 'rolled_back', 'aborted'}
PHASES = TERMINAL | {'observed', 'staged', 'prepared', 'applying', 'applied',
                    'rollback_prepared', 'rollback_applying', 'reconciliation_required'}


@dataclass(frozen=True)
class Attempt:
    """Trusted caller precondition; retain this across process recovery."""
    identity: str
    plan_sha256: str
    generation: int


class Transition:
    def __init__(self, handle, descriptors, checkpoint, check_anchors):
        self.handle, self.fds = handle, descriptors
        self.check_anchors = check_anchors
        self.checkpoint = checkpoint or (lambda _name: None)
        self.state = None
        self._load()

    def _load(self):
        self.check_anchors(self.handle, self.fds)
        value = fs.read_json(self.fds['control'], 'state.json')
        require(type(value) is dict and set(value) == {
            'schema_version', 'nonce', 'generation', 'baseline', 'attempt',
            'attempt_count', 'creation_metadata'}, 'invalid_journal')
        require(type(value['schema_version']) is int and value['schema_version'] == 1
                and value['nonce'] == self.handle.nonce, 'invalid_journal')
        plan.integer(value['generation'], 1, 2**63 - 1, 'invalid_journal')
        plan.integer(value['attempt_count'], 0, 8, 'invalid_journal')
        plan.validate_baseline(value['baseline'])
        attempt = value['attempt']
        if attempt is not None:
            require(type(attempt) is dict and set(attempt) == {
                'id', 'material_identity', 'plan', 'start_baseline', 'phase', 'resume_phase',
                'steps', 'identities', 'forward_done', 'rollback_done', 'rollback_generation',
                'pending', 'retained_directories', 'last_reconciliation'}, 'invalid_journal')
            require(type(attempt['phase']) is str and attempt['phase'] in PHASES, 'invalid_journal')
            plan.hex_value(attempt['id'], 32, 'invalid_journal')
            require(type(attempt['plan']) is dict and 'plan_sha256' in attempt['plan']
                    and type(attempt['steps']) is list and len(attempt['steps']) <= plan.MAX_ENTRIES,
                    'invalid_journal')
            content = {key: item for key, item in attempt['plan'].items() if key != 'plan_sha256'}
            require(plan.digest(plan.canonical(content)) == attempt['plan']['plan_sha256'], 'invalid_journal')
            plan.integer(attempt['forward_done'], 0, len(attempt['steps']), 'invalid_journal')
            plan.integer(attempt['rollback_done'], 0, attempt['forward_done'], 'invalid_journal')
        self.state = value

    @property
    def attempt(self):
        require(self.state['attempt'] is not None, 'no_attempt')
        return self.state['attempt']

    def ticket(self):
        return Attempt(self.attempt['id'], self.attempt['plan']['plan_sha256'],
                       self.attempt['plan']['expected_generation'])

    def _require_ticket(self, expected):
        require(type(expected) is Attempt and expected == self.ticket(), 'stale_attempt')

    def _save(self):
        self.check_anchors(self.handle, self.fds)
        fs.save_json(self.fds['control'], 'state.json', self.state)

    def _event(self, name):
        self.checkpoint(name)

    @contextmanager
    def _material(self):
        descriptor = os.open(self.attempt['id'], fs.DIR_FLAGS, dir_fd=self.fds['control'])
        try:
            require(fs.identity(os.fstat(descriptor)) == self.attempt['material_identity'],
                    'material_changed')
            require(fs.metadata(os.fstat(descriptor)) == {
                'mode': 0o700, 'uid': os.getuid(), 'gid': os.getgid()}, 'unsafe_control')
            yield descriptor
        finally:
            os.close(descriptor)

    def _prepare_captured(self, *, candidate, candidate_descriptor, expected_candidate_commit,
                          predecessor, predecessor_descriptor, attempt_identity=None):
        """Captured inputs from an independently validating, trusted entry point."""
        self._load()
        require(self.state['attempt'] is None or self.attempt['phase'] in TERMINAL, 'unfinished_attempt')
        require(self.state['attempt_count'] < 8, 'attempt_limit')
        require(self.state['generation'] <= 2**63 - 3, 'generation_limit')
        root, entries, contents, identities = fs.snapshot(self.fds['target'])
        baseline = self.state['baseline']
        result = plan.build_plan(
            candidate=candidate, candidate_descriptor=candidate_descriptor,
            expected_candidate_commit=expected_candidate_commit,
            predecessor=predecessor, predecessor_descriptor=predecessor_descriptor,
            baseline=baseline, inventory={'target': plan.TARGET,
                'generation': self.state['generation'], 'root': root, 'entries': entries},
            installed_htaccess=contents.get('.htaccess'),
            creation_metadata=self.state['creation_metadata'])
        steps = [dict(item) for item in result['required_directories'] if item['operation'] == 'create']
        steps.extend(dict(item) for item in result['operations'] if item['operation'] != 'keep')
        attempt_id = uuid.uuid4().hex if attempt_identity is None else attempt_identity
        plan.hex_value(attempt_id, 32, 'invalid_attempt')
        os.mkdir(attempt_id, mode=0o700, dir_fd=self.fds['control'])
        os.fsync(self.fds['control'])
        material_id = fs.identity(os.stat(attempt_id, dir_fd=self.fds['control'], follow_symlinks=False))
        self.state['attempt_count'] += 1
        self.state['attempt'] = {
            'id': attempt_id, 'material_identity': material_id, 'plan': result,
            'start_baseline': baseline, 'phase': 'observed', 'resume_phase': None,
            'steps': steps, 'identities': identities, 'forward_done': 0,
            'rollback_done': 0, 'rollback_generation': None, 'pending': None,
            'retained_directories': [], 'last_reconciliation': None}
        self._save()
        self._event('observed')
        offset = baseline['overlay']['prefix_bytes']
        access = contents['.htaccess']
        composite = access[:offset] + candidate['.htaccess'] + access[offset + len(predecessor['.htaccess']):]
        with self._material() as material:
            for index, step in enumerate(steps):
                if step['after']['kind'] == 'directory':
                    os.mkdir(f'publish-{index}', mode=0o700, dir_fd=material)
                    child = os.open(f'publish-{index}', fs.DIR_FLAGS, dir_fd=material)
                    try:
                        os.fchmod(child, step['after']['mode'])
                        os.fsync(child)
                        step['publish_identity'] = fs.identity(os.fstat(child))
                    finally:
                        os.close(child)
                    os.fsync(material)
                else:
                    data = composite if step['path'] == '.htaccess' else candidate[step['path']]
                    require(plan.digest(data) == step['after']['sha256'], 'stage_mismatch')
                    fs.write_new(material, f'after-{index}', data)
                    step['publish_identity'] = fs.write_new(
                        material, f'publish-{index}', data, step['after']['mode'])
                self._event(f'stage-{index}')
            self.attempt['phase'] = 'staged'
            self._save()
            self._event('staged')
            for index, step in enumerate(steps):
                if step['before'] is not None:
                    fs.write_new(material, f'before-{index}', contents[step['path']])
                    self._event(f'backup-{index}')
        self._verify_material()
        self._validate_target()
        self.attempt['phase'] = 'prepared'
        self._save()
        self._event('prepared')
        return self.report()

    def _generation(self):
        attempt = self.attempt
        original = attempt['start_baseline']
        expected = attempt['rollback_generation']
        if expected is None:
            expected = original['generation'] + (attempt['phase'] == 'verified')
        require(self.state['generation'] == expected, 'generation_conflict')
        baseline = self._next_baseline() if expected == original['generation'] + 1 else original
        require(self.state['baseline'] == baseline, 'generation_conflict')

    def _next_baseline(self):
        attempt = self.attempt
        output = attempt['plan']
        entries = {item['path']: item['after'] for item in
                   output['operations'] + output['required_directories']}
        return {**attempt['start_baseline'], 'generation': output['expected_generation'] + 1,
                'commit': output['candidate']['commit'],
                'descriptor_sha256': output['candidate']['descriptor_sha256'],
                'manifest_sha256': output['candidate']['manifest_sha256'],
                'owned_inventory_sha256': plan.digest(plan.canonical({
                    'root': output['root_metadata'], 'entries': entries}))}

    def _expected(self, forward=None, backward=None):
        attempt = self.attempt
        expected = {}
        for item in attempt['plan']['operations'] + attempt['plan']['required_directories']:
            expected[item['path']] = (item['before'], attempt['identities'].get(item['path']))
        forward = attempt['forward_done'] if forward is None else forward
        backward = attempt['rollback_done'] if backward is None else backward
        for item in attempt['steps'][:forward]:
            expected[item['path']] = (item['after'], item['publish_identity'])
        for item in list(reversed(attempt['steps'][:forward]))[:backward]:
            if item['path'] not in attempt['retained_directories']:
                expected[item['path']] = (item['before'], item.get('restore_identity'))
        return expected

    def _validate_target(self, forward=None, backward=None):
        self.check_anchors(self.handle, self.fds)
        root, entries, _contents, identities = fs.snapshot(self.fds['target'])
        require(root == self.attempt['plan']['root_metadata'], 'root_metadata_changed')
        for name, (record, inode) in self._expected(forward, backward).items():
            require(entries.get(name) == record, 'target_conflict')
            if record is not None:
                require(identities[name] == inode, 'target_identity_changed')
        return entries, identities

    def _verify_material(self):
        private = {'mode': 0o600, 'uid': os.getuid(), 'gid': os.getgid()}
        with self._material() as material:
            for index, step in enumerate(self.attempt['steps']):
                for side, key in (('after', 'after'), ('before', 'before')):
                    record = step[key]
                    if record is not None and record['kind'] == 'file':
                        _data, actual, _inode = fs.read_file(material, f'{side}-{index}')
                        require(actual == {**record, **private}, 'material_changed')

    @contextmanager
    def _parent(self, name):
        expected = self._expected()
        parent = os.dup(self.fds['target'])
        try:
            prefix = ''
            for part in name.split('/')[:-1]:
                prefix += part
                child = os.open(part, fs.DIR_FLAGS, dir_fd=parent)
                os.close(parent)
                parent = child
                record, inode = expected[prefix]
                require(record is not None and fs.identity(os.fstat(parent)) == inode
                        and fs.metadata(os.fstat(parent)) == {key: record[key] for key in plan.META_FIELDS},
                        'parent_changed')
                prefix += '/'
            yield parent, name.split('/')[-1]
        finally:
            os.close(parent)

    def _publish(self, index, rollback):
        step = self.attempt['steps'][index]
        record = step['before'] if rollback else step['after']
        source = f'restore-{index}' if rollback else f'publish-{index}'
        self._validate_target()
        with self._parent(step['path']) as (parent, name), self._material() as material:
            if record is not None:
                if record['kind'] == 'file':
                    _data, actual, inode = fs.read_file(material, source)
                    require(actual == record, 'material_changed')
                else:
                    child = os.open(source, fs.DIR_FLAGS, dir_fd=material)
                    try:
                        require(not os.listdir(child), 'material_changed')
                        actual = {'kind': 'directory', **fs.metadata(os.fstat(child))}
                        inode = fs.identity(os.fstat(child))
                        require(actual == record, 'material_changed')
                    finally:
                        os.close(child)
                require(inode == step['restore_identity' if rollback else 'publish_identity'], 'material_changed')
                # Validate again after opening the source and parent. Cooperative
                # writers hold the same lock; this is not a hostile-writer CAS.
                self._validate_target()
                require(fs.identity(os.stat(source, dir_fd=material, follow_symlinks=False)) == inode,
                        'material_changed')
                os.replace(source, name, src_dir_fd=material, dst_dir_fd=parent)
                os.fsync(material)
            elif step['after']['kind'] == 'directory':
                os.rmdir(name, dir_fd=parent)
            else:
                os.unlink(name, dir_fd=parent)
            os.fsync(parent)

    def _conflict(self, error):
        attempt = self.attempt
        if attempt['phase'] != 'reconciliation_required':
            attempt['resume_phase'] = attempt['phase']
            attempt['phase'] = 'reconciliation_required'
            self._save()
        if isinstance(error, ArtifactError):
            raise error
        raise ArtifactError('transition_io_failed') from None

    def apply(self, expected):
        self._load()
        self._require_ticket(expected)
        require(self.attempt['phase'] in {'prepared', 'applying', 'applied'}, 'reconciliation_required')
        require(self.attempt['pending'] is None, 'reconciliation_required')
        try:
            self._generation()
            self._verify_material()
            self._validate_target()
            self.attempt['phase'] = 'applying'
            self._save()
            while self.attempt['forward_done'] < len(self.attempt['steps']):
                index = self.attempt['forward_done']
                self._validate_target()
                self.attempt['pending'] = {'direction': 'forward', 'index': index}
                self._save()
                self._event(f'forward-intent-{index}')
                self._publish(index, rollback=False)
                self._event(f'forward-written-{index}')
                self._validate_target(forward=index + 1)
                self.attempt['forward_done'] += 1
                self.attempt['pending'] = None
                self._save()
                self._event(f'forward-recorded-{index}')
            self.attempt['phase'] = 'applied'
            self._save()
            self._event('applied')
            self._validate_target()
            self._verify_material()
            self._generation()
            self._event('verified-before-commit')
            self._validate_target()
            self._verify_material()
            self._generation()
            self.state['baseline'] = self._next_baseline()
            self.state['generation'] += 1
            self.attempt['phase'] = 'verified'
            self._save()
            self._event('committed')
        except (ArtifactError, OSError) as error:
            self._conflict(error)
        return self.report()

    def reconcile(self, expected):
        """Observe pending outcomes. This method never mutates target files."""
        self._load()
        self._require_ticket(expected)
        attempt = self.attempt
        require(attempt['phase'] not in {'rolled_back', 'aborted'}, 'already_closed')
        try:
            if attempt['phase'] == 'reconciliation_required':
                attempt['phase'] = attempt['resume_phase']
                attempt['resume_phase'] = None
            self._generation()
            if attempt['phase'] in {'observed', 'staged'}:
                self._validate_target()
                attempt['phase'] = 'aborted'
                attempt['last_reconciliation'] = 'no_target_writes'
                self._save()
                return self.report()
            self._verify_material()
            pending = attempt['pending']
            if pending is not None:
                forward = pending['direction'] == 'forward'
                require(pending['index'] == (attempt['forward_done'] if forward else
                    attempt['forward_done'] - attempt['rollback_done'] - 1), 'invalid_journal')
                try:
                    self._validate_target()
                    outcome = 'not_applied'
                except ArtifactError:
                    if forward:
                        self._validate_target(forward=attempt['forward_done'] + 1)
                        attempt['forward_done'] += 1
                    else:
                        self._validate_target(backward=attempt['rollback_done'] + 1)
                        attempt['rollback_done'] += 1
                    outcome = 'applied'
                attempt['last_reconciliation'] = outcome
                attempt['pending'] = None
            else:
                self._validate_target()
                attempt['last_reconciliation'] = 'consistent'
            self._save()
        except (ArtifactError, OSError) as error:
            self._conflict(error)
        return self.report()

    def rollback(self, expected):
        self._load()
        self._require_ticket(expected)
        attempt = self.attempt
        require(attempt['phase'] in {'prepared', 'applying', 'applied', 'verified',
                                    'rollback_prepared', 'rollback_applying'}
                and attempt['pending'] is None, 'reconciliation_required')
        try:
            self._generation()
            self._verify_material()
            self._validate_target()
            if attempt['rollback_generation'] is None:
                # Restoration transfers are prepared before any rollback target
                # write. Incomplete preparation is safe to re-materialize below.
                attempt['rollback_generation'] = self.state['generation']
                attempt['phase'] = 'rollback_prepared'
                self._save()
                self._event('rollback-preparing')
            if attempt['phase'] == 'rollback_prepared':
                with self._material() as material:
                    for index, step in enumerate(attempt['steps'][:attempt['forward_done']]):
                        if step['before'] is not None:
                            source = f'restore-{index}'
                            try:
                                # No rollback target writes have begun in this
                                # phase. An incomplete private transfer can be
                                # rebuilt from the separately verified backup.
                                fs.read_file(material, source)
                                os.unlink(source, dir_fd=material)
                            except FileNotFoundError:
                                pass
                            data, _record, _inode = fs.read_file(material, f'before-{index}')
                            step['restore_identity'] = fs.write_new(material, source, data, step['before']['mode'])
                            self._event(f'restore-stage-{index}')
                attempt['phase'] = 'rollback_applying'
                self._save()
                self._event('rollback-prepared')
            while attempt['rollback_done'] < attempt['forward_done']:
                index = attempt['forward_done'] - attempt['rollback_done'] - 1
                step = attempt['steps'][index]
                entries, _identities = self._validate_target()
                if step['after']['kind'] == 'directory' and any(
                        name.startswith(step['path'] + '/') for name in entries):
                    attempt['retained_directories'].append(step['path'])
                    attempt['rollback_done'] += 1
                    self._save()
                    self._event(f'rollback-retained-{index}')
                    continue
                attempt['pending'] = {'direction': 'rollback', 'index': index}
                self._save()
                self._event(f'rollback-intent-{index}')
                self._publish(index, rollback=True)
                self._event(f'rollback-written-{index}')
                self._validate_target(backward=attempt['rollback_done'] + 1)
                attempt['rollback_done'] += 1
                attempt['pending'] = None
                self._save()
                self._event(f'rollback-recorded-{index}')
            self._validate_target()
            self._verify_material()
            self._generation()
            self._event('rollback-verified-before-commit')
            self._validate_target()
            self._verify_material()
            self._generation()
            self.state['generation'] += 1
            self.state['baseline'] = {**attempt['start_baseline'], 'generation': self.state['generation']}
            attempt['phase'] = 'rolled_back'
            self._save()
            self._event('rollback-committed')
        except (ArtifactError, OSError) as error:
            self._conflict(error)
        return self.report()

    def report(self):
        attempt = self.state['attempt']
        return {'schema_version': 1, 'kind': 'static-transition',
                'target': plan.TARGET, 'deployment_authorized': False,
                'generation': self.state['generation'],
                'phase': attempt['phase'] if attempt else 'empty',
                'candidate_commit': attempt['plan']['candidate']['commit'] if attempt else None,
                'plan_sha256': attempt['plan']['plan_sha256'] if attempt else None,
                'forward_operations': attempt['forward_done'] if attempt else 0,
                'rollback_operations': attempt['rollback_done'] if attempt else 0,
                'retained_directories': len(attempt['retained_directories']) if attempt else 0,
                'last_reconciliation': attempt['last_reconciliation'] if attempt else None}
