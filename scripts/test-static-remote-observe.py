#!/usr/bin/env python3
"""Synthetic fixed-command endpoint tests. No SSH, provider or credentials."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).with_name('static-remote-observe.py').resolve()
spec = importlib.util.spec_from_file_location('observer', SOURCE)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oss-observer-test-')
        self.base = Path(self.temp.name)
        self.root = self.base / 'public'
        self.control = self.base / 'private'
        self.peer = self.base / 'unrelated'
        for directory in (self.root, self.control, self.peer):
            directory.mkdir(mode=0o700)
        self.root.chmod(0o755)
        (self.root / 'assets').mkdir(mode=0o755)
        (self.root / 'assets/style.css').write_bytes(b'body { color: blue; }\n')
        (self.root / 'index.html').write_bytes(b'<!doctype html><title>Fixture</title>\n')
        (self.root / '.htaccess').write_bytes(b'# fixture config\n')
        (self.peer / 'sentinel').write_bytes(b'private peer must never be read or written\n')
        (self.root / 'unmanaged.txt').write_bytes(b'unmanaged content\n')
        self.names = ['.htaccess', 'assets/style.css', 'index.html']
        self.manifest = self.root / observer.MANIFEST
        self.rebuild_manifest()
        self.lock = self.control / 'lock'
        self.lock.touch(mode=0o600)
        self.config = {'schema': 1, 'target': 'oss-static', 'root': str(self.root), 'control': str(self.control),
                       'root_identity': observer.identity(self.root.stat()),
                       'control_identity': observer.identity(self.control.stat()),
                       'lock_identity': observer.identity(self.lock.stat())}
        self.config_path = self.control / 'config.json'
        self.save_config()

    def tearDown(self):
        self.temp.cleanup()

    def save_config(self):
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        self.config_path.chmod(0o600)

    def rebuild_manifest(self):
        self.manifest.write_text(''.join(hashlib.sha256((self.root / name).read_bytes()).hexdigest()
                                         + '  ' + name + '\n' for name in self.names), encoding='ascii')

    def run_cli(self, command=observer.COMMAND, args=None, stdin=b'', isolated=True, extra_env=None):
        env = dict(os.environ, SSH_ORIGINAL_COMMAND=command)
        env.update(extra_env or {})
        flags = ['-I', '-S'] if isolated else []
        flags += ['-O'] if sys.flags.optimize else []
        result = subprocess.run([sys.executable, *flags, str(SOURCE),
                                 *(args if args is not None else ['--config', str(self.config_path)])],
                                env=env, input=stdin, capture_output=True, timeout=5)
        self.assertEqual(result.stderr, b'')
        self.assertLess(len(result.stdout), 4096)
        self.assertNotIn(str(self.base).encode(), result.stdout)
        report = json.loads(result.stdout)
        self.assertIs(report['deployment_authorized'], False)
        return result.returncode, report

    def rejects(self, code=None, **kwargs):
        status, report = self.run_cli(**kwargs)
        self.assertEqual(status, 1)
        self.assertEqual(set(report), {'schema', 'target', 'error', 'deployment_authorized'})
        if code:
            self.assertEqual(report['error'], code)

    def test_observes_actual_bytes_with_no_target_or_private_writes(self):
        def snapshot():
            return {str(path.relative_to(self.base)): (path.read_bytes(), path.stat().st_mode)
                    for path in self.base.rglob('*') if path.is_file()}
        before = snapshot()
        status, report = self.run_cli()
        self.assertEqual(status, 0)
        self.assertEqual(report['files'], 3)
        self.assertEqual(report['manifest_sha256'], hashlib.sha256(self.manifest.read_bytes()).hexdigest())
        self.assertEqual(report['observer_sha256'], hashlib.sha256(SOURCE.read_bytes()).hexdigest())
        self.assertEqual(report['other_content_differences'], 0)
        self.assertIs(report['htaccess_differs'], False)
        self.assertIs(report['read_only'], True)
        self.assertEqual(snapshot(), before)
        self.assertNotIn('sentinel', json.dumps(report))
        self.assertNotIn('public', json.dumps(report))

    def test_distinguishes_overlay_from_other_content_drift(self):
        (self.root / '.htaccess').write_bytes(b'# provider prefix\n# fixture config\n# provider suffix\n')
        status, before = self.run_cli()
        self.assertEqual(status, 0)
        self.assertTrue(before['htaccess_differs'])
        self.assertEqual(before['other_content_differences'], 0)
        (self.root / 'index.html').write_bytes(b'altered\n')
        status, after = self.run_cli()
        self.assertEqual(status, 0)
        self.assertEqual(after['other_content_differences'], 1)
        self.assertNotEqual(before['observed_inventory_sha256'], after['observed_inventory_sha256'])

    def test_rejects_commands_before_opening_any_configuration(self):
        for command in ['', 'sh', 'sftp', 'internal-sftp', 'scp -t /tmp/x', observer.COMMAND + ' ',
                        observer.COMMAND + '; id', observer.COMMAND + '\n', '$(id)', '../../other']:
            with self.subTest(command=command):
                self.rejects('command_rejected', command=command, args=['--config', '/missing/private'])

    def test_no_body_or_environment_execution_interface(self):
        marker = self.base / 'EXECUTED'
        poison = self.base / 'sitecustomize.py'
        poison.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n", encoding='utf-8')
        status, _ = self.run_cli(stdin=b'{"command":"rm","target":"other"}\n',
                                 extra_env={'PYTHONPATH': str(self.base), 'PYTHONSTARTUP': str(poison)})
        self.assertEqual(status, 0)
        self.assertFalse(marker.exists())

    def test_requires_isolated_interpreter(self):
        self.rejects('unsupported_runtime', isolated=False)

    def test_rejects_extra_cli_arguments(self):
        self.rejects('invalid_arguments', args=['--config', str(self.config_path), '--target', str(self.peer)])

    def test_private_config_permissions(self):
        self.config_path.chmod(0o644)
        self.rejects('unsafe_control')

    def test_config_parent_must_be_private(self):
        self.control.chmod(0o755)
        self.rejects('unsafe_control')

    def test_config_symlinks_and_hardlinks(self):
        self.config_path.rename(self.control / 'saved.json')
        self.config_path.symlink_to(self.control / 'saved.json')
        self.rejects()
        self.config_path.unlink()
        os.link(self.control / 'saved.json', self.config_path)
        self.rejects('unsafe_file')

    def test_invalid_config_shapes_and_aliases(self):
        original = dict(self.config)
        for changes in [{'schema': True}, {'target': 'another-target'}, {'extra': 1},
                        {'root_identity': [True, 1]}, {'root': str(self.root) + '/.'},
                        {'control': str(self.root)}, {'control': str(self.root / 'private')}]:
            with self.subTest(changes=changes):
                self.config = dict(original, **changes)
                self.save_config()
                self.rejects('invalid_config')

    def test_duplicate_and_excessive_config(self):
        self.config_path.write_text('{"schema":1,"schema":1}', encoding='utf-8')
        self.rejects('invalid_config')
        self.config_path.write_bytes(b' ' * (observer.MAX_CONFIG + 1))
        self.rejects('size_limit')

    def test_root_identity_replacement(self):
        old = self.base / 'old'
        self.root.rename(old)
        self.root.mkdir()
        self.rejects('identity_changed')

    def test_target_symlink_and_ancestor_alias(self):
        actual = self.base / 'actual'
        self.root.rename(actual)
        self.root.symlink_to(actual, target_is_directory=True)
        self.rejects()
        self.root.unlink()
        actual.rename(self.root)
        alias = self.base / 'alias'
        alias.symlink_to(self.base, target_is_directory=True)
        self.config['root'] = str(alias / 'public')
        self.save_config()
        self.rejects()

    def test_lock_replacement_and_permissions(self):
        self.lock.rename(self.control / 'old-lock')
        self.lock.touch(mode=0o600)
        self.rejects('identity_changed')
        self.config['lock_identity'] = observer.identity(self.lock.stat())
        self.save_config()
        self.lock.chmod(0o644)
        self.rejects('unsafe_control')

    def test_exclusive_lock_blocks_observer_and_releases_without_repair(self):
        with self.lock.open('rb') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.rejects('target_busy')
        self.assertEqual(self.run_cli()[0], 0)

    def test_shared_observers_can_coexist(self):
        with self.lock.open('rb') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertEqual(self.run_cli()[0], 0)

    def test_rejects_manifest_path_attacks_and_duplicates(self):
        valid = self.manifest.read_bytes()
        for path in ['../unrelated/sentinel', '/etc/passwd', 'assets/../index.html',
                     'assets//style.css', '.', observer.MANIFEST, 'x\\y', 'a/' * 8 + 'file']:
            with self.subTest(path=path):
                self.manifest.write_bytes(b'0' * 64 + b'  ' + path.encode() + b'\n')
                self.rejects('invalid_manifest')
        for raw in [valid + valid, valid.replace(b'\n', b'\r\n'), valid[:-1], b'', b'\n']:
            with self.subTest(raw=raw[:30]):
                self.manifest.write_bytes(raw)
                self.rejects('invalid_manifest')

    def test_symlinked_file_and_parent_are_rejected(self):
        path = self.root / 'index.html'
        path.unlink()
        path.symlink_to(self.peer / 'sentinel')
        self.rejects()
        path.unlink()
        path.write_bytes(b'fixture')
        (self.root / 'assets').rename(self.base / 'assets')
        (self.root / 'assets').symlink_to(self.base / 'assets', target_is_directory=True)
        self.rejects()

    def test_hardlink_and_fifo_are_rejected_without_blocking(self):
        path = self.root / 'index.html'
        path.unlink()
        os.link(self.peer / 'sentinel', path)
        self.rejects('unsafe_file')
        path.unlink()
        os.mkfifo(path)
        self.rejects('unsafe_file')

    def test_manifest_link_and_missing_file_fail_without_data_leak(self):
        self.manifest.rename(self.root / 'saved-manifest')
        self.manifest.symlink_to(self.root / 'saved-manifest')
        self.rejects()
        self.manifest.unlink()
        (self.root / 'saved-manifest').rename(self.manifest)
        (self.root / 'index.html').unlink()
        self.rejects('observation_failed')

    def test_bounded_file_manifest_and_total_reads(self):
        with (self.root / 'index.html').open('wb') as stream:
            stream.truncate(observer.MAX_FILE + 1)
        self.rejects('size_limit')
        (self.root / 'index.html').write_bytes(b'x')
        with patch.object(observer, 'MAX_TOTAL', 1):
            with self.assertRaisesRegex(observer.Rejected, '^size_limit$'):
                observer.observe(self.config)
        self.manifest.write_bytes(b'x' * (observer.MAX_MANIFEST + 1))
        self.rejects('size_limit')

    def test_file_count_bound(self):
        self.manifest.write_bytes(b'0' * 64 + b'  .htaccess\n' + b''.join(
            b'0' * 64 + ('  file%d\n' % i).encode() for i in range(256)))
        self.rejects('invalid_manifest')

    def test_changes_after_capture_are_detected(self):
        original = observer.capture_file
        def racing_capture(parent, name, limit, private=False):
            value = original(parent, name, limit, private)
            if name == 'index.html':
                (self.root / 'assets/style.css').write_bytes(b'changed after read')
            return value
        with patch.object(observer, 'capture_file', side_effect=racing_capture):
            with self.assertRaisesRegex(observer.Rejected, '^state_changed$'):
                observer.observe(self.config)

    def test_unlisted_files_are_never_opened(self):
        original = observer.capture_file
        seen = []
        def recording_capture(parent, name, limit, private=False):
            seen.append(name)
            return original(parent, name, limit, private)
        with patch.object(observer, 'capture_file', side_effect=recording_capture):
            observer.observe(self.config)
        self.assertEqual(set(seen), {observer.MANIFEST, '.htaccess', 'style.css', 'index.html'})


if __name__ == '__main__':
    unittest.main()
