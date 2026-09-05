#!/usr/bin/env python3
"""Offline release-artifact tests; builds use only disposable output directories."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / 'scripts/release-artifact.py'
SHA = 'a' * 40
SECRET = 'PRIVATE_MARKER_MUST_NOT_APPEAR_7392'
spec = importlib.util.spec_from_file_location('release_contract_tests', CLI)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
import site_artifact as tree


def manifest(root):
    lines = [f'{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{path.relative_to(root).as_posix()}\n'
             for path in sorted(root.rglob('*')) if path.is_file() and path.name != tree.MANIFEST]
    (root / tree.MANIFEST).write_text(''.join(lines))


class ReleaseArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builds = tempfile.TemporaryDirectory(prefix='oss-artifact-build-tests-')
        cls.addClassCleanup(cls.builds.cleanup)
        cls.first = Path(cls.builds.name) / 'first'
        cls.second = Path(cls.builds.name) / 'different-location' / 'second'
        for output, timezone in [(cls.first, 'UTC'), (cls.second, 'Pacific/Honolulu')]:
            subprocess.run(['sh', str(ROOT / 'scripts/build-site.sh'), str(output)], check=True, capture_output=True,
                           env={**os.environ, 'TZ': timezone, 'LC_ALL': 'C'}, timeout=30)
        for path in cls.second.rglob('*'):
            os.utime(path, (1_000_000_000, 1_000_000_000))
        cls.localized = None
        try:
            locales = subprocess.run(['locale', '-a'], capture_output=True, text=True, check=True, timeout=5).stdout.splitlines()
        except (OSError, subprocess.SubprocessError):
            locales = []
        available = [name for name in locales if name.lower() not in {'c', 'c.utf8', 'c.utf-8', 'posix'}]
        if available:
            cls.localized = Path(cls.builds.name) / 'different-locale'
            subprocess.run(['sh', str(ROOT / 'scripts/build-site.sh'), str(cls.localized)], check=True, capture_output=True,
                           env={**os.environ, 'TZ': 'UTC', 'LC_ALL': available[0]}, timeout=30)
        cls.baseline = api.make_descriptor(api.inspect(cls.first), SHA)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='oss-artifact-case-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.payload = self.fresh('payload')
        self.metadata = self.root / 'descriptor.json'
        self.metadata.write_text(json.dumps(self.baseline))

    def fresh(self, name):
        target = self.root / name
        shutil.copytree(self.first, target)
        return target

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, timeout=20)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        error = json.loads(result.stderr)
        self.assertEqual(set(error), {'error'})
        self.assertRegex(error['error'], r'^[a-z_]+$')
        return error['error']

    def verify(self, payload=None, metadata=None, success=True, *extra):
        return self.cli('verify', '--artifact-dir', payload or self.payload, '--descriptor', metadata or self.metadata,
                        '--expected-commit', SHA, *extra, success=success)

    def test_roundtrip_has_honest_sanitized_plan_and_schema(self):
        output = self.root / 'created.json'
        result = self.cli('create', '--artifact-dir', self.payload, '--commit', SHA, '--out', output)
        self.assertEqual(json.loads(output.read_text()), self.baseline)
        self.assertEqual(result, self.verify())
        self.assertFalse(result['deployment_authorized'])
        self.assertTrue(result['artifact_verified'])
        self.assertEqual(result['reproducibility'], 'not_checked')
        self.assertEqual(result['pending_gates'], api.PENDING_GATES)
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        schema = json.loads((ROOT / 'scripts/release-artifact.schema.json').read_text())
        self.assertEqual(set(schema['required']), set(self.baseline))
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['repository']['const'], self.baseline['repository'])
        self.assertEqual(self.baseline['file_count'], len([p for p in self.first.rglob('*') if p.is_file()]))
        self.assertEqual(self.baseline['total_bytes'], sum(p.stat().st_size for p in self.first.rglob('*') if p.is_file()))
        ordinary = subprocess.run([sys.executable, str(ROOT / 'scripts/check-site.py'), str(self.payload)], capture_output=True, text=True)
        self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
        self.assertIn('site checks passed:', ordinary.stdout)

    def test_independent_builds_different_locations_timezones_and_mtimes_match(self):
        a, b = self.root / 'a.json', self.root / 'b.json'
        self.cli('create', '--artifact-dir', self.first, '--commit', SHA, '--out', a)
        self.cli('create', '--artifact-dir', self.second, '--commit', SHA, '--out', b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        result = self.cli('verify', '--artifact-dir', self.first, '--descriptor', a, '--expected-commit', SHA, '--rebuild-dir', self.second)
        self.assertEqual(result['reproducibility'], 'matched')

    def test_non_c_locale_build_is_identical_when_locale_is_installed(self):
        if self.localized is None:
            self.skipTest('No non-C locale installed; timezone and mtime checks still run independently')
        first = tree.capture(self.first, api.site.required_files)
        localized = tree.capture(self.localized, api.site.required_files)
        self.assertEqual(first, localized)

    def test_missing_extra_changed_and_empty_extra_directory_fail(self):
        for name in ['missing', 'extra', 'changed', 'extra-directory']:
            with self.subTest(name=name):
                payload = self.fresh(name)
                if name == 'missing': (payload / 'index.html').unlink()
                if name == 'extra': (payload / (SECRET + '.txt')).write_text(SECRET)
                if name == 'changed': (payload / 'index.html').write_bytes((payload / 'index.html').read_bytes() + b'\n')
                if name == 'extra-directory': (payload / SECRET).mkdir()
                self.verify(payload, success=False)

    def test_manifest_malformed_paths_duplicates_and_coverage_fail(self):
        original = (self.payload / tree.MANIFEST).read_text()
        first = original.splitlines()[0]
        digest = first.split('  ')[0]
        replacements = ['../' + SECRET, '/' + SECRET, '././index.html', './/index.html', 'assets/../index.html',
                        'assets\\index.html', 'index.html\t' + SECRET, tree.MANIFEST]
        bad = [digest + '  ' + name + '\n' + original for name in replacements]
        bad += [original + first + '\n', '\n'.join(original.splitlines()[1:]) + '\n', original + '\n',
                original.replace('  ', ' *', 1), original.replace(digest, '0' * 64, 1), original.rstrip('\n'), original.replace('\n', '\r\n')]
        for number, value in enumerate(bad):
            with self.subTest(case=number):
                (self.payload / tree.MANIFEST).write_text(value)
                self.verify(success=False)

    def test_manifest_single_dot_prefix_optional_and_no_second_hash_list(self):
        original = (self.payload / tree.MANIFEST).read_text()
        (self.payload / tree.MANIFEST).write_text(original.replace('  ./', '  '))
        output = self.root / 'plain-paths.json'
        self.cli('create', '--artifact-dir', self.payload, '--commit', SHA, '--out', output)
        self.verify(metadata=output)
        self.assertNotIn('files', json.loads(output.read_text()))

    def test_descriptor_rejects_unknown_duplicate_nonjson_and_wrong_types(self):
        mutations = [('repository', 'fork/website'), ('kind', 'worker'), ('schema_version', True), ('schema_version', 1.0),
                     ('schema_version', 2), ('commit', 'main'), ('commit', 'A' * 40), ('commit', 'a' * 39),
                     ('manifest_sha256', 'g' * 64), ('file_count', True), ('file_count', 1.0), ('file_count', 0),
                     ('total_bytes', False), ('total_bytes', 1.5), ('total_bytes', tree.MAX_TOTAL_BYTES + 1), (SECRET, SECRET)]
        values = []
        for key, value in mutations:
            candidate = {**self.baseline, key: value}
            values.append(json.dumps(candidate))
        values += ['[]', '{}', 'null', json.dumps(self.baseline)[:-1] + ',"commit":"' + SHA + '"}',
                   json.dumps({**self.baseline, 'total_bytes': float('nan')}),
                   json.dumps({**self.baseline, 'total_bytes': float('inf')}), '{' + SECRET, '[' * 2000]
        for number, value in enumerate(values):
            with self.subTest(case=number):
                self.metadata.write_text(value)
                self.verify(success=False)

    def test_descriptor_commit_hash_counts_and_expected_commit_are_bound(self):
        for key, value in [('commit', 'b' * 40), ('manifest_sha256', '0' * 64),
                           ('file_count', self.baseline['file_count'] + 1), ('total_bytes', self.baseline['total_bytes'] + 1)]:
            with self.subTest(field=key):
                self.metadata.write_text(json.dumps({**self.baseline, key: value}))
                self.verify(success=False)
        for sha in ['main', SECRET, 'b' * 40]:
            self.cli('verify', '--artifact-dir', self.payload, '--descriptor', self.metadata, '--expected-commit', sha, success=False)

    def test_symlink_root_ancestor_file_directory_and_broken_link_are_rejected(self):
        outside = self.root / 'outside'
        outside.write_text(SECRET)
        for mode in ['external-file', 'internal-file', 'broken-file', 'directory', 'root', 'ancestor']:
            with self.subTest(mode=mode):
                payload = self.fresh(mode)
                if mode.endswith('file'):
                    target = payload / 'index.html'
                    target.unlink()
                    target.symlink_to(outside if mode == 'external-file' else '.htaccess' if mode == 'internal-file' else 'missing')
                elif mode == 'directory':
                    shutil.rmtree(payload / 'assets/styles')
                    (payload / 'assets/styles').symlink_to(self.first / 'assets/styles', target_is_directory=True)
                elif mode == 'root':
                    alias = self.root / 'root-alias'
                    alias.symlink_to(payload, target_is_directory=True)
                    payload = alias
                else:
                    alias = self.root / 'ancestor-alias'
                    alias.symlink_to(self.root, target_is_directory=True)
                    payload = alias / mode
                self.verify(payload, success=False)
        self.assertEqual(outside.read_text(), SECRET)

    def test_hardlinks_fifo_and_bounded_reads_are_rejected(self):
        for mode in ['hardlink', 'fifo', 'oversized', 'too-many']:
            with self.subTest(mode=mode):
                payload = self.fresh(mode)
                target = payload / 'index.html'
                if mode == 'hardlink': os.link(target, self.root / 'outside-hardlink')
                if mode == 'fifo': target.unlink(); os.mkfifo(target)
                if mode == 'oversized':
                    with target.open('wb') as stream: stream.truncate(tree.MAX_FILE_BYTES + 1)
                if mode == 'too-many':
                    for number in range(tree.MAX_ENTRIES + 1): (payload / f'entry-{number}').touch()
                self.verify(payload, success=False)
        self.metadata.write_text(SECRET * 1000)
        self.verify(success=False)

    def test_descriptor_destinations_refuse_payload_aliases_and_overwrite(self):
        for target in [self.payload / 'descriptor.json', self.payload / 'assets' / '..' / 'descriptor.json']:
            self.cli('create', '--artifact-dir', self.payload, '--commit', SHA, '--out', target, success=False)
            self.assertFalse((self.payload / 'descriptor.json').exists())
        alias = self.root / 'payload-alias'
        alias.symlink_to(self.payload, target_is_directory=True)
        self.cli('create', '--artifact-dir', self.payload, '--commit', SHA, '--out', alias / 'descriptor.json', success=False)
        self.cli('create', '--artifact-dir', self.payload, '--commit', SHA, '--out', self.metadata, success=False)
        self.assertEqual(json.loads(self.metadata.read_text()), self.baseline)
        descriptor_link = self.root / 'descriptor-link'
        descriptor_link.symlink_to(self.metadata)
        self.verify(metadata=descriptor_link, success=False)
        self.cli('create', '--artifact-dir', self.payload, '--commit', SHA, '--out', descriptor_link, success=False)

    def test_rehashed_product_and_machine_contract_errors_are_sanitized(self):
        for mode in ['html', 'machine']:
            with self.subTest(mode=mode):
                payload = self.fresh(mode)
                if mode == 'html':
                    target = payload / 'index.html'
                    target.write_text(target.read_text().replace('</body>', '<a href="' + SECRET + '">Broken</a></body>'))
                else:
                    target = payload / 'data/atlas.json'
                    value = json.loads(target.read_text())
                    value['entries'][0]['category'] = SECRET
                    target.write_text(json.dumps(value) + '\n')
                manifest(payload)
                output = self.root / (mode + '.json')
                error = self.cli('create', '--artifact-dir', payload, '--commit', SHA, '--out', output, success=False)
                self.assertEqual(error, 'product_checks_failed')
                self.assertFalse(output.exists())

    def test_product_references_cannot_read_outside_private_copy(self):
        target = self.payload / 'index.html'
        target.write_text(target.read_text().replace('</body>', '<a href="../../outside-secret.html#id">Outside</a></body>'))
        manifest(self.payload)
        probes = []
        original = Path.exists
        def tracked(path):
            if str(path).endswith('outside-secret.html'):
                probes.append(str(path))
            return original(path)
        with patch.object(Path, 'exists', tracked), self.assertRaises(tree.ArtifactError):
            api.inspect(self.payload)
        self.assertEqual(probes, [])

    def test_checker_output_on_both_streams_is_not_forwarded(self):
        captured_out, captured_error = io.StringIO(), io.StringIO()
        def noisy_checker(_root):
            print(SECRET)
            print(SECRET, file=sys.stderr)
            raise ValueError(SECRET)
        with patch.object(api.site, 'check_product', noisy_checker), redirect_stdout(captured_out), redirect_stderr(captured_error):
            with self.assertRaises(tree.ArtifactError) as failure:
                api.create(self.payload, SHA, self.root / 'rejected.json')
        self.assertEqual(failure.exception.code, 'product_checks_failed')
        self.assertEqual(captured_out.getvalue(), '')
        self.assertEqual(captured_error.getvalue(), '')
        self.assertFalse((self.root / 'rejected.json').exists())

    def test_no_artifact_script_execution_and_changed_rebuild_fails(self):
        target = self.payload / 'assets/scripts/theme-v1.js'
        marker = self.root / 'must-not-be-written'
        target.write_text(target.read_text() + '\nimport fs from "node:fs"; fs.writeFileSync(' + json.dumps(str(marker)) + ', "executed");\n')
        manifest(self.payload)
        output = self.root / 'changed-consistent.json'
        self.cli('create', '--artifact-dir', self.payload, '--commit', SHA, '--out', output)
        self.assertFalse(marker.exists())
        self.assertEqual(self.cli('verify', '--artifact-dir', self.payload, '--descriptor', output, '--expected-commit', SHA,
                                 '--rebuild-dir', self.first, success=False), 'rebuild_mismatch')

    def test_real_fd_catches_same_size_mutation_even_with_restored_mtime(self):
        target = self.payload / 'index.html'
        before = target.stat()
        original_open = tree.os.open
        changed = []
        def racing_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == 'index.html' and kwargs.get('dir_fd') is not None and not changed:
                content = target.read_bytes()
                target.write_bytes(bytes([content[0] ^ 1]) + content[1:])
                os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
                changed.append(True)
            return fd
        with patch.object(tree.os, 'open', racing_open), self.assertRaises(tree.ArtifactError) as error:
            tree.capture(self.payload, api.site.required_files)
        self.assertEqual(error.exception.code, 'tree_changed')
        self.assertEqual(changed, [True])

    def test_replace_with_symlink_before_actual_open_never_reads_target(self):
        target = self.payload / 'index.html'
        outside = self.root / 'outside-secret'
        outside.write_text(SECRET)
        outside_inode = outside.stat().st_ino
        original_open, original_read = tree.os.open, tree.os.read
        changed, reads = [], []
        def racing_open(path, flags, *args, **kwargs):
            if path == 'index.html' and kwargs.get('dir_fd') is not None and not changed:
                target.unlink()
                target.symlink_to(outside)
                changed.append(True)
            return original_open(path, flags, *args, **kwargs)
        def tracked_read(fd, size):
            if os.fstat(fd).st_ino == outside_inode: reads.append(True)
            return original_read(fd, size)
        with patch.object(tree.os, 'open', racing_open), patch.object(tree.os, 'read', tracked_read), self.assertRaises((OSError, tree.ArtifactError)):
            tree.capture(self.payload, api.site.required_files)
        self.assertEqual(changed, [True])
        self.assertEqual(reads, [])

    def test_cli_argument_errors_do_not_echo_rejected_values(self):
        self.cli('verify', '--unknown-' + SECRET, success=False)
        self.cli(SECRET, success=False)

    def test_inventory_stops_reading_names_at_the_entry_budget(self):
        read = []
        class Entries:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def __iter__(self):
                for number in range(tree.MAX_ENTRIES + 100):
                    read.append(number)
                    yield SimpleNamespace(name=f'entry-{number}')
        reader = tree.TreeReader(self.payload)
        try:
            with patch.object(tree.os, 'scandir', return_value=Entries()), self.assertRaises(tree.ArtifactError) as error:
                reader.inventory()
            self.assertEqual(error.exception.code, 'tree_limit')
            self.assertEqual(len(read), tree.MAX_ENTRIES + 1)
        finally:
            reader.close()

    def test_python_optimization_does_not_disable_guards(self):
        command = [sys.executable, '-O', str(CLI), 'verify', '--artifact-dir', str(self.payload), '--descriptor', str(self.metadata), '--expected-commit', SHA]
        valid = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertFalse(json.loads(valid.stdout)['deployment_authorized'])
        self.metadata.write_text(json.dumps({**self.baseline, 'schema_version': True, SECRET: SECRET}))
        rejected = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(rejected.stdout, '')
        self.assertNotIn(SECRET, rejected.stderr)
        self.assertEqual(json.loads(rejected.stderr), {'error': 'invalid_descriptor'})


if __name__ == '__main__':
    unittest.main()
