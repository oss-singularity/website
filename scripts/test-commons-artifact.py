#!/usr/bin/env python3
"""Real source capture, transport rejection and literal-preserving schema fixtures."""
import base64
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import commons_artifact as a
from site_artifact import ArtifactError

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'services' / 'commons'
SHA = 'a' * 40


class CommonsArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files, cls.schema = a.source_files(SOURCE)
        cls.raw = a.packet(cls.files, SHA, cls.schema)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='oss-commons-artifact-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def source(self):
        path = self.root / 'source'
        shutil.copytree(SOURCE, path)
        return path

    def rejection(self, value, code=None):
        with self.assertRaises(ArtifactError) as caught:
            a.unpack(a.encode(value) if type(value) is dict else value, SHA, self.schema)
        if code:
            self.assertEqual(caught.exception.code, code)

    def test_real_source_roundtrip_and_cli_rebuild(self):
        path = self.root / 'candidate.json'
        result = a.create(SOURCE, SHA, path)
        self.assertEqual(path.read_bytes(), self.raw)
        self.assertFalse(result['deployment_authorized'])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        command = [sys.executable, *(['-O'] if sys.flags.optimize else []),
                   str(ROOT / 'scripts/commons-artifact.py'), 'verify', '--candidate', str(path),
                   '--expected-commit', SHA, '--expected-schema-sha256', self.schema,
                   '--rebuild-source', str(SOURCE)]
        process = subprocess.run(command, capture_output=True, timeout=20, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertTrue(result['artifact_verified'] and result['rebuild_matched'])
        self.assertFalse(result['deployment_authorized'])
        self.assertEqual(set(result['modules']), a.MODULES)
        self.assertIn('fresh-installed-schema', result['pending_gates'])

    def test_no_local_helpers_tests_or_config_in_payload(self):
        value = json.loads(self.raw)
        self.assertEqual(set(value['files']), a.MODULES)
        for excluded in ['local-d1.mjs', 'dev-server.mjs', 'wrangler.example.toml', 'ADMIN_TOKEN', 'database_id']:
            self.assertNotIn(excluded, value['files'])
        self.assertEqual(set(value['descriptor']), {'schema_version', 'kind', 'repository', 'commit', 'runtime', 'schema', 'modules'})

    def test_source_changes_are_detected_by_independent_rebuild(self):
        source = self.source()
        path = self.root / 'candidate.json'
        a.create(source, SHA, path)
        with (source / 'worker.mjs').open('ab') as stream:
            stream.write(b'\n// another source revision\n')
        with self.assertRaisesRegex(ArtifactError, 'rebuild_mismatch'):
            a.verify(path, SHA, self.schema, source)

    def test_worker_code_is_captured_without_execution(self):
        source = self.source()
        (source / 'worker.mjs').write_text("throw new Error('do not execute candidate code');\n")
        files, schema = a.source_files(source)
        self.assertEqual(files['worker.mjs'], (source / 'worker.mjs').read_bytes())
        self.assertEqual(schema, self.schema)

    def test_unknown_root_module_is_not_silently_dropped(self):
        source = self.source()
        (source / 'unexpected.mjs').write_text('export default {};\n')
        with self.assertRaisesRegex(ArtifactError, 'source_module_allowlist_mismatch'):
            a.source_files(source)

    def test_missing_production_module_rejected(self):
        source = self.source()
        (source / 'security.mjs').unlink()
        with self.assertRaisesRegex(ArtifactError, 'source_module_allowlist_mismatch'):
            a.source_files(source)

    def test_changed_or_extra_migrations_are_rejected_before_sql_evaluation(self):
        source = self.source()
        first = source / 'migrations/0001_commons.sql'
        first.write_text("ATTACH DATABASE '/not-an-allowed-target' AS stolen;")
        with patch.object(a.sqlite3, 'connect', side_effect=AssertionError('SQL must not run')):
            with self.assertRaisesRegex(ArtifactError, 'schema_profile_changed'):
                a.source_files(source)
        first.write_bytes((SOURCE / 'migrations/0001_commons.sql').read_bytes())
        (source / 'migrations/0004_future.sql').write_text('CREATE TABLE future (id TEXT);')
        with self.assertRaisesRegex(ArtifactError, 'schema_profile_changed'):
            a.source_files(source)

    def test_modified_initialization_cannot_be_rebound_to_a_packet(self):
        value = json.loads(self.raw)
        value['descriptor']['schema']['migrations']['0001_commons.sql'] = 'b' * 64
        self.rejection(value, 'descriptor_mismatch')
        value = json.loads(self.raw)
        value['descriptor']['schema']['sha256'] = 'b' * 64
        self.rejection(value, 'descriptor_mismatch')

    def test_schema_tokens_preserve_literal_and_identifier_boundaries(self):
        prefix = 'CREATE TABLE example (text TEXT DEFAULT '
        self.assertEqual(a.sql_tokens(prefix + "'a  b' )"), a.sql_tokens(prefix + "'a  b'\n)"))
        self.assertNotEqual(a.sql_tokens(prefix + "'a  b')"), a.sql_tokens(prefix + "'a b')"))
        self.assertNotEqual(a.sql_tokens('SELECT a b'), a.sql_tokens('SELECT ab'))
        self.assertNotEqual(a.sql_tokens('SELECT "a b"'), a.sql_tokens('SELECT "ab"'))
        self.assertEqual(a.sql_tokens("SELECT 'it''s /* literal */' -- comment\n"),
                         ['SELECT', "'it''s /* literal */'"])
        self.assertEqual(a.sql_tokens('SELECT /* formatting */ [a b]'), ['SELECT', '[a b]'])
        for invalid in ["SELECT 'broken", '/* broken', 'SELECT "broken', 'SELECT [broken', '\0', '-- only comment']:
            with self.subTest(invalid=invalid), self.assertRaises(ArtifactError):
                a.sql_tokens(invalid)

    def test_schema_hash_uses_structure_and_preserves_literal_values(self):
        def schema(sql):
            with sqlite3.connect(':memory:') as database:
                database.row_factory = sqlite3.Row
                database.execute(sql)
                return [dict(row) for row in database.execute(a.SCHEMA_QUERY)]
        first = schema("CREATE TABLE example (text TEXT DEFAULT 'a  b')")
        formatted = schema("CREATE TABLE example\n( text TEXT DEFAULT 'a  b' )")
        changed = schema("CREATE TABLE example (text TEXT DEFAULT 'a b')")
        self.assertEqual(a.schema_hash(first), a.schema_hash(formatted))
        self.assertNotEqual(a.schema_hash(first), a.schema_hash(changed))
        for bad in [[], first + first, [{**first[0], 'type': []}], [{**first[0], 'sql': '-- absent'}],
                    [{**first[0], 'data_rows': ['never accepted']}]]:
            with self.subTest(bad=bad), self.assertRaises(ArtifactError):
                a.schema_hash(bad)

    def test_internal_name_filter_does_not_hide_application_objects(self):
        with sqlite3.connect(':memory:') as database:
            database.row_factory = sqlite3.Row
            database.execute('CREATE TABLE sqliteXapplication (id TEXT)')
            database.execute('CREATE TABLE _cf_KV (key TEXT)')
            rows = [dict(row) for row in database.execute(a.SCHEMA_QUERY)]
        self.assertEqual([row['name'] for row in rows], ['sqliteXapplication'])

    def test_tampered_bytes_and_self_consistent_wrong_commit_rejected(self):
        value = json.loads(self.raw)
        value['files']['worker.mjs'] = base64.b64encode(b'export default {};').decode()
        self.rejection(value, 'descriptor_mismatch')
        self.rejection(a.packet(self.files, 'b' * 40, self.schema), 'descriptor_mismatch')

    def test_foreign_runtime_and_extra_authority_fields_rejected(self):
        variants = []
        for key, replacement in [('repository', 'other/project'), ('kind', 'static-site'), ('schema_version', True)]:
            value = json.loads(self.raw)
            value['descriptor'][key] = replacement
            variants.append(value)
        for key, replacement in [('entrypoint', '../worker.mjs'), ('compatibility_date', '2020-01-01'),
                                 ('compatibility_flags', ['nodejs_compat'])]:
            value = json.loads(self.raw)
            value['descriptor']['runtime'][key] = replacement
            variants.append(value)
        value = json.loads(self.raw)
        value['descriptor']['provider_token'] = 'not-an-accepted-field'
        variants.append(value)
        value = json.loads(self.raw)
        value['descriptor']['schema']['profile'] = True
        variants.append(value)
        value = json.loads(self.raw)
        value['descriptor']['modules']['worker.mjs']['size'] = float(len(self.files['worker.mjs']))
        variants.append(value)
        for value in variants:
            with self.subTest(descriptor=value['descriptor']):
                self.rejection(value)

    def test_extra_missing_and_unsafe_payload_members_rejected(self):
        for name in ['dev-server.mjs', '../worker.mjs', '/worker.mjs', 'worker.mjs/extra']:
            value = json.loads(self.raw)
            value['files'][name] = 'YQ=='
            self.rejection(value, 'module_allowlist_mismatch')
        value = json.loads(self.raw)
        del value['files']['security.mjs']
        self.rejection(value, 'module_allowlist_mismatch')

    def test_invalid_json_and_base64_are_bounded(self):
        for raw in [b'', b'[]', b'{}', b'\xff', self.raw + b'{}', b'{"files":{},"files":{}}',
                    b'{"files": NaN}', b'[' * 1500 + b']' * 1500, b' ' * (a.MAX_PACKET + 1)]:
            with self.subTest(size=len(raw)):
                self.rejection(raw)
        for encoded in ['!', 'YQ', 'YR==', 'YQ==\n', '', base64.b64encode(b'\xff').decode(),
                        base64.b64encode(b'\0').decode(), 'A' * (4 * ((a.MAX_MODULE + 2) // 3) + 1)]:
            value = json.loads(self.raw)
            value['files']['worker.mjs'] = encoded
            self.rejection(value)

    def test_no_follow_for_source_payload_output_and_parent_aliases(self):
        source = self.source()
        target = source / 'worker.mjs'
        target.unlink()
        target.symlink_to(SOURCE / 'worker.mjs')
        with self.assertRaises((ArtifactError, OSError)):
            a.source_files(source)
        path = self.root / 'candidate.json'
        path.write_bytes(self.raw)
        alias = self.root / 'candidate-alias.json'
        alias.symlink_to(path)
        with self.assertRaises((ArtifactError, OSError)):
            a.verify(alias, SHA, self.schema)
        with self.assertRaises((ArtifactError, OSError)):
            a.create(SOURCE, SHA, alias)
        directory_alias = self.root / 'parent-alias'
        directory_alias.symlink_to(SOURCE, target_is_directory=True)
        with self.assertRaises((ArtifactError, OSError)):
            a.source_files(directory_alias)
        self.assertEqual(path.read_bytes(), self.raw)

    def test_hardlinks_existing_outputs_and_outputs_in_source_rejected(self):
        source = self.source()
        os.link(source / 'worker.mjs', self.root / 'linked.mjs')
        with self.assertRaisesRegex(ArtifactError, 'unsafe_file'):
            a.source_files(source)
        path = self.root / 'existing.json'
        path.write_text('preserve this file')
        with self.assertRaises(FileExistsError):
            a.create(SOURCE, SHA, path)
        self.assertEqual(path.read_text(), 'preserve this file')
        with self.assertRaisesRegex(ArtifactError, 'output_inside_source'):
            a.create(SOURCE, SHA, SOURCE / 'never-created.json')

    def test_source_size_limits_are_checked_before_reading_code(self):
        source = self.source()
        (source / 'worker.mjs').write_bytes(b'x' * (a.MAX_MODULE + 1))
        with patch.object(a.TreeReader, 'read', side_effect=AssertionError('oversized input must not be read')):
            with self.assertRaisesRegex(ArtifactError, 'invalid_module'):
                a.source_files(source)
        for name in a.MODULES:
            (source / name).write_bytes(b'x' * 360000)
        with self.assertRaisesRegex(ArtifactError, 'code_size_limit'):
            a.source_files(source)
        with self.assertRaisesRegex(ArtifactError, 'code_size_limit'):
            a.packet({name: b'x' * 360000 for name in a.MODULES}, SHA, self.schema)

    def test_replacing_source_directory_during_capture_is_rejected(self):
        source = self.source()
        original = a.TreeReader.read
        replaced = False

        def replace(reader, name):
            nonlocal replaced
            raw = original(reader, name)
            if name == 'worker.mjs' and not replaced:
                replaced = True
                moved = self.root / 'moved-source'
                source.rename(moved)
                shutil.copytree(moved, source)
            return raw

        with patch.object(a.TreeReader, 'read', new=replace):
            with self.assertRaisesRegex(ArtifactError, 'tree_changed'):
                a.source_files(source)

    def test_cli_errors_do_not_echo_payload_or_path(self):
        path = self.root / 'private-name.json'
        path.write_text('{"private-value": "never log me"}')
        command = [sys.executable, *(['-O'] if sys.flags.optimize else []), str(ROOT / 'scripts/commons-artifact.py'),
                   'verify', '--candidate', str(path), '--expected-commit', SHA, '--expected-schema-sha256', self.schema]
        result = subprocess.run(command, capture_output=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b'')
        self.assertEqual(json.loads(result.stderr), {'error': 'invalid_packet'})
        self.assertNotIn(b'private', result.stderr)

    def test_descriptor_cannot_mutate_shared_profile(self):
        value = a.metadata(self.files, SHA, self.schema)
        value['schema']['migrations'].clear()
        value['runtime']['compatibility_flags'].append('unexpected')
        self.assertEqual(len(a.MIGRATIONS), 3)
        self.assertEqual(a.RUNTIME['compatibility_flags'], [])


if __name__ == '__main__':
    unittest.main()
