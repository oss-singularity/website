"""Installed-command tests with private targets, real processes and crash recovery."""
from contextlib import contextmanager
import base64
import copy
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid

ROOT = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


observer = module('static_remote_observer', ROOT / 'static-remote-observe.py')
launcher = module('remote_test_launcher', ROOT / 'static-remote-release.py')
import static_plan as plan
import static_policy as policy
import static_posix as fs
import static_remote as remote
from site_artifact import ArtifactError, MANIFEST, open_directory

OLD, NEW = '1' * 40, '2' * 40
ACCESS = b'Options -Indexes\nDirectoryIndex index.html\n'
PREFIX, SUFFIX = b'# preserved provider prefix\n', b'# preserved provider suffix\n'


def encode(raw):
    return base64.b64encode(raw).decode('ascii')


def payload(values):
    return {**values, MANIFEST: b''.join((plan.digest(raw) + '  ./' + name + '\n').encode()
                                       for name, raw in sorted(values.items()))}


def descriptor(files, commit):
    return plan.canonical({'schema_version': 1, 'repository': plan.REPOSITORY,
                           'kind': 'static-site', 'commit': commit, **plan.summary(files)})


def put(path, raw, mode=0o600):
    with path.open('xb') as stream:
        stream.write(raw)
    path.chmod(mode)


def file_contents(path):
    fd = open_directory(path)
    try:
        return fs.snapshot(fd)[2]
    finally:
        os.close(fd)


@contextmanager
def installation(candidate=None):
    access = candidate['.htaccess'] if candidate is not None else ACCESS
    old = payload({'.htaccess': access, 'index.html': b'old page',
                   'assets/scripts/retired.js': b'retained old asset',
                   'assets/styles/same.css': b'unchanged'})
    new = candidate if candidate is not None else payload({'.htaccess': access,
        'index.html': b'new page', 'assets/scripts/new.js': b'new asset',
        'assets/styles/same.css': b'unchanged', 'data/new.json': b'{}'})
    installed_access = PREFIX + access + SUFFIX + policy.STATIC_GUARD
    original = {**old, '.htaccess': installed_access, '.well-known/provider-marker': b'preserve'}
    with tempfile.TemporaryDirectory(prefix='oss-remote-test-') as name:
        base = Path(name)
        target, control, config_dir, runtime, peer = [base / p for p in ('target', 'control', 'config', 'runtime', 'peer')]
        for folder in (target, control, config_dir, runtime, peer):
            folder.mkdir(mode=0o700)
        target.chmod(0o755)
        put(peer / 'sentinel', b'unrelated data\n')
        for relative, raw in original.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            for parent in path.parents:
                if parent == target:
                    break
                parent.chmod(0o755)
            put(path, raw, 0o644)
        for filename in ['static-remote-release.py'] + [name for _key, name in launcher.MODULES]:
            put(runtime / filename, (ROOT / filename).read_bytes(), 0o400)
        nonce = uuid.uuid4().hex
        put(control / 'owner', nonce.encode())
        put(control / 'lock', b'')
        root_fd = open_directory(target)
        try:
            root_metadata, entries, _contents, _identities = fs.snapshot(root_fd)
        finally:
            os.close(root_fd)
        owned = {}
        for path in old:
            owned[path] = entries[path]
            for parent in plan.parents(path):
                owned[parent] = entries[parent]
        previous = descriptor(old, OLD)
        baseline = {'schema_version': 1, 'target': plan.TARGET, 'generation': 7,
            'commit': OLD, 'descriptor_sha256': plan.digest(previous),
            'manifest_sha256': plan.digest(old[MANIFEST]),
            'owned_inventory_sha256': plan.digest(plan.canonical({'root': root_metadata, 'entries': owned})),
            'overlay': {'prefix_bytes': len(PREFIX), 'prefix_sha256': plan.digest(PREFIX),
                        'suffix_bytes': len(SUFFIX + policy.STATIC_GUARD),
                        'suffix_sha256': plan.digest(SUFFIX + policy.STATIC_GUARD)}}
        control_fd = open_directory(control)
        try:
            fs.save_json(control_fd, 'state.json', {'schema_version': 1, 'nonce': nonce,
                'generation': 7, 'baseline': baseline, 'attempt': None, 'attempt_count': 0,
                'creation_metadata': {
                    'file': {'mode': 0o644, 'uid': os.getuid(), 'gid': os.getgid()},
                    'directory': {'mode': 0o755, 'uid': os.getuid(), 'gid': os.getgid()}}})
        finally:
            os.close(control_fd)
        put(control / ('descriptor-' + plan.digest(previous)), previous)
        config = {'schema': 1, 'target': 'oss-static', 'root': str(target), 'control': str(control),
                  'root_identity': fs.identity(target.stat()), 'control_identity': fs.identity(control.stat()),
                  'lock_identity': fs.identity((control / 'lock').stat())}
        config_path = config_dir / 'binding.json'
        put(config_path, plan.canonical(config))
        installed_policy = {'schema': 1, 'htaccess': encode(access),
            'installed_htaccess_sha256': plan.digest(installed_access),
            'ancestor_htaccess': {str(parent): None for parent in target.parents}}
        put(config_dir / 'policy.json', plan.canonical(installed_policy))
        request = {'schema': 1, 'operation': 'prepare', 'identity': uuid.uuid4().hex,
            'expected_generation': 7, 'expected_predecessor_commit': OLD,
            'expected_manifest_sha256': plan.digest(old[MANIFEST]), 'candidate_commit': NEW,
            'candidate_descriptor': encode(descriptor(new, NEW)),
            'files': {path: encode(raw) for path, raw in new.items()}}
        yield SimpleNamespace(base=base, target=target, control=control, runtime=runtime,
            peer=peer, config=config_path, request=request, original=original, candidate=new)


def crash_child(config, runtime, request, stop):
    def checkpoint(event):
        if event == stop:
            os._exit(73)
    try:
        binding = remote.load_binding(config, runtime)
        remote.dispatch(binding, request, checkpoint)
    except Exception:
        os._exit(74)


class RemoteTests(unittest.TestCase):
    def cli(self, fixture, request, *, command=remote.COMMAND, flags=('-I', '-S'), args=None):
        env = os.environ.copy()
        env['SSH_ORIGINAL_COMMAND'] = command
        body = request if type(request) is bytes else plan.canonical(request)
        optimized = ['-O'] if sys.flags.optimize else []
        result = subprocess.run([sys.executable, *optimized, *flags, str(fixture.runtime / 'static-remote-release.py'),
                                 *(args if args is not None else ['--config', str(fixture.config)])],
                                input=body, env=env, capture_output=True, timeout=20)
        self.assertEqual(result.stderr, b'')
        response = json.loads(result.stdout)
        for private in (str(fixture.base), str(fixture.target), str(fixture.control)):
            self.assertNotIn(private, result.stdout.decode())
        return result.returncode, response

    def success(self, fixture, request):
        code, response = self.cli(fixture, request)
        self.assertEqual(code, 0, response)
        self.assertFalse(response['deployment_authorized'])
        self.assertFalse(response['publication_verified'])
        self.assertTrue(response['filesystem_only'])
        return response

    def rejected(self, fixture, request, error=None, **kwargs):
        before = file_contents(fixture.target)
        code, response = self.cli(fixture, request, **kwargs)
        self.assertEqual(code, 1, response)
        self.assertRegex(response['error'], r'^[a-z_]+$')
        if error is not None:
            self.assertEqual(response['error'], error)
        self.assertEqual(file_contents(fixture.target), before)
        self.assertEqual((fixture.peer / 'sentinel').read_bytes(), b'unrelated data\n')

    def prepare(self, fixture):
        return self.success(fixture, fixture.request)['ticket']

    def action(self, operation, ticket):
        return {'schema': 1, 'operation': operation, 'ticket': ticket}

    def stop(self, fixture, request, event):
        context = multiprocessing.get_context('spawn')
        process = context.Process(target=crash_child,
            args=(str(fixture.config), str(fixture.runtime), request, event))
        process.start()
        process.join(20)
        if process.is_alive():
            process.kill()
            process.join(5)
            self.fail('remote fixture exceeded its time bound')
        self.assertEqual(process.exitcode, 73, event)

    def test_installed_cli_prepares_applies_and_rolls_back_exact_bytes(self):
        with installation() as fixture:
            root_before = fixture.target.stat()
            report = self.success(fixture, {'schema': 1, 'operation': 'status', 'identity': None})
            self.assertEqual(report['phase'], 'empty')
            self.assertNotIn('ticket', report)
            expected = self.prepare(fixture)
            self.assertEqual(expected['identity'], fixture.request['identity'])
            self.assertEqual(file_contents(fixture.target), fixture.original)
            report = self.success(fixture, self.action('apply', expected))
            self.assertEqual((report['phase'], report['generation'], report['baseline_commit']), ('verified', 8, NEW))
            self.assertEqual(file_contents(fixture.target), {**fixture.original, **fixture.candidate,
                '.htaccess': fixture.original['.htaccess']})
            report = self.success(fixture, self.action('rollback', expected))
            self.assertEqual((report['phase'], report['generation']), ('rolled_back', 9))
            self.assertEqual(file_contents(fixture.target), fixture.original)
            after = fixture.target.stat()
            self.assertEqual((root_before.st_dev, root_before.st_ino, root_before.st_mode, root_before.st_uid, root_before.st_gid),
                             (after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid))
            self.assertEqual((fixture.peer / 'sentinel').read_bytes(), b'unrelated data\n')

    def test_lost_prepare_response_is_queried_and_replayed_by_original_identity(self):
        with installation() as fixture:
            first = self.prepare(fixture)
            state = self.success(fixture, {'schema': 1, 'operation': 'status', 'identity': fixture.request['identity']})
            self.assertEqual(state['ticket'], first)
            self.assertEqual(self.prepare(fixture), first)
            self.success(fixture, self.action('apply', first))
            replay = self.success(fixture, fixture.request)
            self.assertEqual((replay['phase'], replay['generation'], replay['ticket']), ('verified', 8, first))
            changed = copy.deepcopy(fixture.request)
            new = payload({**{name: raw for name, raw in fixture.candidate.items() if name != MANIFEST}, 'index.html': b'different'})
            changed['files'] = {path: encode(raw) for path, raw in new.items()}
            changed['candidate_descriptor'] = encode(descriptor(new, NEW))
            self.rejected(fixture, changed, 'stale_attempt')

    def test_incomplete_descriptor_record_is_completed_by_the_same_preparation(self):
        with installation() as fixture:
            self.stop(fixture, fixture.request, 'prepared')
            state = self.success(fixture, {'schema': 1, 'operation': 'status', 'identity': fixture.request['identity']})
            self.rejected(fixture, self.action('apply', state['ticket']))
            expected = self.prepare(fixture)
            self.assertEqual(expected, state['ticket'])
            self.success(fixture, self.action('apply', expected))
            self.assertEqual((fixture.target / 'index.html').read_bytes(), b'new page')

    def test_initial_baseline_preconditions_are_not_taken_from_submitted_state(self):
        with installation() as fixture:
            original_control = file_contents(fixture.control)
            for key, value, error in (
                ('expected_generation', 6, 'generation_conflict'),
                ('expected_predecessor_commit', 'a' * 40, 'baseline_mismatch'),
                ('expected_manifest_sha256', 'a' * 64, 'baseline_mismatch'),
            ):
                self.rejected(fixture, {**fixture.request, key: value}, error)
            self.assertEqual(file_contents(fixture.control), original_control)

    def test_unfinished_attempt_and_attempt_limit_do_not_store_more_descriptors(self):
        with installation() as fixture:
            self.prepare(fixture)
            original_control = file_contents(fixture.control)
            self.rejected(fixture, {**fixture.request, 'identity': uuid.uuid4().hex}, 'unfinished_attempt')
            self.assertEqual(file_contents(fixture.control), original_control)
        with installation() as fixture:
            control = open_directory(fixture.control)
            try:
                state = fs.read_json(control, 'state.json')
                state['attempt_count'] = 8
                fs.save_json(control, 'state.json', state)
            finally:
                os.close(control)
            original_control = file_contents(fixture.control)
            self.rejected(fixture, fixture.request, 'attempt_limit')
            self.assertEqual(file_contents(fixture.control), original_control)

    def test_executable_creation_modes_and_changed_file_bytes_block_application(self):
        with installation() as fixture:
            control = open_directory(fixture.control)
            try:
                state = fs.read_json(control, 'state.json')
                state['creation_metadata']['file']['mode'] = 0o755
                fs.save_json(control, 'state.json', state)
            finally:
                os.close(control)
            self.rejected(fixture, fixture.request, 'unsafe_control')
        with installation() as fixture:
            expected = self.prepare(fixture)
            (fixture.target / 'index.html').write_bytes(b'newer independently written content')
            self.rejected(fixture, self.action('apply', expected), 'target_conflict')

    def test_stale_ticket_cannot_apply_or_recover_another_attempt(self):
        with installation() as fixture:
            expected = self.prepare(fixture)
            for field, value in [('identity', '0' * 32), ('plan_sha256', '0' * 64), ('generation', 6)]:
                stale = {**expected, field: value}
                for action in ('apply', 'reconcile', 'rollback'):
                    with self.subTest(field=field, action=action):
                        self.rejected(fixture, self.action(action, stale), 'stale_attempt')
            self.rejected(fixture, {'schema': 1, 'operation': 'status', 'identity': '0' * 32}, 'stale_attempt')

    def test_commands_and_arguments_are_rejected_before_configuration(self):
        with installation() as fixture:
            for command in ('', 'id', 'sftp', 'scp -t /tmp/unused', remote.COMMAND + '; id', remote.COMMAND + ' '):
                with self.subTest(command=command):
                    self.rejected(fixture, b'not json', 'command_rejected', command=command,
                                  args=['--config', '/not/a/real/config'])
            self.rejected(fixture, b'', 'invalid_arguments', args=['--config', str(fixture.config), 'extra'])
            self.rejected(fixture, b'', 'unsupported_runtime', flags=())

    def test_poisoned_python_startup_is_ignored(self):
        with installation() as fixture:
            poison = fixture.base / 'poison'
            poison.mkdir()
            marker = fixture.base / 'executed'
            (poison / 'sitecustomize.py').write_text(f'open({str(marker)!r}, "w").write("bad")')
            old = os.environ.get('PYTHONPATH')
            try:
                os.environ['PYTHONPATH'] = str(poison)
                self.prepare(fixture)
            finally:
                if old is None:
                    os.environ.pop('PYTHONPATH', None)
                else:
                    os.environ['PYTHONPATH'] = old
            self.assertFalse(marker.exists())

    def test_runtime_sources_must_be_private_regular_read_only_files(self):
        with installation() as fixture:
            source = fixture.runtime / 'static_engine.py'
            source.chmod(0o600)
            self.rejected(fixture, fixture.request, 'release_failed')
            source.chmod(0o400)
            source.unlink()
            source.symlink_to(ROOT / 'static_engine.py')
            self.rejected(fixture, fixture.request, 'release_failed')

    def test_protocol_rejects_duplicates_unknown_fields_and_malformed_encoding(self):
        with installation() as fixture:
            for raw in (b'{"schema":1,"schema":1}', b'{"schema":NaN}', b'[' * 30 + b']' * 30):
                self.rejected(fixture, raw, 'invalid_request')
            self.rejected(fixture, {**fixture.request, 'root': '/tmp/another-root'}, 'invalid_request')
            self.rejected(fixture, {**fixture.request, 'operation': '__init__'}, 'invalid_request')
            broken = copy.deepcopy(fixture.request)
            broken['files']['index.html'] = 'not base64'
            self.rejected(fixture, broken, 'invalid_encoding')
            self.rejected(fixture, b' ' * (remote.MAX_REQUEST + 1), 'invalid_request')

    def test_server_configuration_and_non_static_paths_never_reach_staging(self):
        with installation() as fixture:
            original_control = file_contents(fixture.control)
            cases = ['shell.php', 'assets/scripts/shell.php.js', 'assets/scripts/.user.ini',
                     'assets/.htaccess', '../escape.html', '/absolute.html',
                     '.well-known/ssl-manager/override.json', 'cgi-bin/index.html', 'api/index.html']
            for name in cases:
                request = copy.deepcopy(fixture.request)
                request['files'][name] = encode(b'not executable')
                with self.subTest(name=name):
                    self.rejected(fixture, request)
            changed = copy.deepcopy(fixture.request)
            files = payload({**{name: raw for name, raw in fixture.candidate.items() if name != MANIFEST},
                             '.htaccess': b'AddHandler application/x-httpd-php .html\n'})
            changed['files'] = {name: encode(raw) for name, raw in files.items()}
            changed['candidate_descriptor'] = encode(descriptor(files, NEW))
            self.rejected(fixture, changed, 'server_configuration_changed')
            self.assertEqual(file_contents(fixture.control), original_control)

    def test_changed_root_or_ancestor_configuration_stops_existing_attempt(self):
        with installation() as fixture:
            expected = self.prepare(fixture)
            access = fixture.target / '.htaccess'
            access.write_bytes(access.read_bytes() + b'# changed\n')
            self.rejected(fixture, self.action('apply', expected), 'server_configuration_changed')
        with installation() as fixture:
            expected = self.prepare(fixture)
            put(fixture.base / '.htaccess', b'AddHandler unsafe .html\n', 0o644)
            self.rejected(fixture, self.action('apply', expected), 'server_configuration_changed')

    def test_nested_configuration_is_checked_before_prepare_and_each_write(self):
        for prepared in (False, True):
            with self.subTest(prepared=prepared), installation() as fixture:
                expected = self.prepare(fixture) if prepared else None
                put(fixture.target / 'assets/.htaccess', b'AddHandler unsafe .js\n', 0o644)
                request = self.action('apply', expected) if prepared else fixture.request
                self.rejected(fixture, request, 'nested_server_configuration')

    def test_configuration_identity_lock_and_private_permissions_are_enforced(self):
        with installation() as fixture:
            lock = fixture.control / 'lock'
            lock.rename(fixture.control / 'original-lock')
            put(lock, b'')
            self.rejected(fixture, fixture.request)
        with installation() as fixture:
            fixture.config.chmod(0o644)
            self.rejected(fixture, fixture.request)
        with installation() as fixture:
            config = json.loads(fixture.config.read_bytes())
            config['root'] = str(fixture.peer)
            fixture.config.write_bytes(plan.canonical(config))
            self.rejected(fixture, fixture.request)

    def test_observer_and_writer_share_one_lock(self):
        with installation() as fixture:
            binding = remote.load_binding(str(fixture.config), str(fixture.runtime))
            with remote.locked(binding):
                self.rejected(fixture, fixture.request, 'target_busy')
                env = {**os.environ, 'SSH_ORIGINAL_COMMAND': observer.COMMAND}
                result = subprocess.run([sys.executable, '-I', '-S', str(fixture.runtime / 'static-remote-observe.py'),
                                         '--config', str(fixture.config)], env=env, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout)['error'], 'target_busy')

    def test_interrupted_prepare_is_closed_without_target_writes(self):
        for event in ('observed', 'staged', 'prepared'):
            with self.subTest(event=event), installation() as fixture:
                self.stop(fixture, fixture.request, event)
                status = self.success(fixture, {'schema': 1, 'operation': 'status', 'identity': fixture.request['identity']})
                result = self.success(fixture, self.action('reconcile', status['ticket']))
                self.assertEqual(result['phase'], 'prepared' if event == 'prepared' else 'aborted')
                self.assertEqual(file_contents(fixture.target), fixture.original)

    def test_fresh_process_recovery_observes_both_sides_of_a_write(self):
        for event in ('forward-intent-0', 'forward-written-0', 'applied', 'committed'):
            with self.subTest(event=event), installation() as fixture:
                expected = self.prepare(fixture)
                self.stop(fixture, self.action('apply', expected), event)
                result = self.success(fixture, self.action('reconcile', expected))
                if result['phase'] != 'verified':
                    self.success(fixture, self.action('apply', expected))
                self.assertEqual((fixture.target / 'index.html').read_bytes(), b'new page')
                self.success(fixture, self.action('rollback', expected))
                self.assertEqual(file_contents(fixture.target), fixture.original)

    def test_rollback_recovery_preserves_new_unmanaged_files(self):
        with installation() as fixture:
            expected = self.prepare(fixture)
            self.success(fixture, self.action('apply', expected))
            put(fixture.target / 'data/unmanaged.json', b'new unrelated data', 0o644)
            self.stop(fixture, self.action('rollback', expected), 'rollback-written-4')
            self.success(fixture, self.action('reconcile', expected))
            result = self.success(fixture, self.action('rollback', expected))
            self.assertEqual(result['phase'], 'rolled_back')
            self.assertEqual((fixture.target / 'data/unmanaged.json').read_bytes(), b'new unrelated data')
            self.assertEqual((fixture.target / 'index.html').read_bytes(), b'old page')

    def test_current_build_uses_the_installed_protocol(self):
        with tempfile.TemporaryDirectory(prefix='oss-remote-build-') as temporary:
            built = Path(temporary) / 'dist'
            result = subprocess.run(['bash', str(ROOT / 'build-site.sh'), str(built)], capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            candidate = file_contents(built)
            with installation(candidate) as fixture:
                expected = self.prepare(fixture)
                self.success(fixture, self.action('apply', expected))
                actual = file_contents(fixture.target)
                for name, raw in candidate.items():
                    self.assertEqual(actual[name], fixture.original['.htaccess'] if name == '.htaccess' else raw, name)
                self.success(fixture, self.action('rollback', expected))
                self.assertEqual(file_contents(fixture.target), fixture.original)


if __name__ == '__main__':
    unittest.main()
