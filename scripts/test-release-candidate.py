#!/usr/bin/env python3
"""Offline candidate-consumer tests; only trusted source builds in temporary directories."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
import warnings
import zipfile
import zlib

import site_artifact as tree
ROOT = Path(tree.__file__).resolve().parent.parent
CLI = Path(__file__).with_name('release-candidate.py')
spec = importlib.util.spec_from_file_location('candidate_tests', CLI)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
SHA = 'a' * 40
RUN, ATTEMPT, CANDIDATE, RECEIPT = 248, 2, 97, 98
MARKER = 'REJECTED_CANDIDATE_FIXTURE_7201'
ENV = {'GH_TOKEN': 'public-fixture-' + MARKER}


def zipped(files):
    output = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        warnings.simplefilter('ignore', UserWarning)
        for name, content in files:
            archive.writestr(name, content)
    return output.getvalue()


def run():
    repo = {'id': api.REPOSITORY_ID, 'full_name': api.REPOSITORY, 'fork': False}
    return {'id': RUN, 'run_attempt': ATTEMPT, 'workflow_id': api.WORKFLOW_ID, 'path': api.WORKFLOW_PATH,
            'status': 'completed', 'conclusion': 'success', 'event': 'push', 'head_branch': 'main', 'head_sha': SHA,
            'repository': deepcopy(repo), 'head_repository': deepcopy(repo), 'unrelated': MARKER}


def uploaded(number, role, raw):
    return {'id': number, 'name': f'static-{role}-{SHA}-{RUN}-{ATTEMPT}',
            'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'expired': False,
            'expires_at': (datetime.now(timezone.utc) + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'workflow_run': {'id': RUN, 'repository_id': api.REPOSITORY_ID, 'head_repository_id': api.REPOSITORY_ID,
                             'head_branch': 'main', 'head_sha': SHA}, 'unrelated': MARKER}


class Response:
    def __init__(self, body, url=None, status=200):
        self.body, self.url, self.status = body, url or api.API_URL + api.BRANCH_ROUTE, status
        self.reads = []
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def geturl(self): return self.url
    def read(self, limit):
        self.reads.append(limit)
        return self.body[:limit]


class CandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builds = tempfile.TemporaryDirectory(prefix='oss-candidate-build-tests-')
        cls.addClassCleanup(cls.builds.cleanup)
        root = Path(cls.builds.name)
        first, second = root / 'first', root / 'second'
        for target in [first, second]:
            subprocess.run(['sh', str(ROOT / 'scripts/build-site.sh'), str(target)], capture_output=True, check=True,
                           timeout=30, env={**os.environ, 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'})
        metadata, report, receipt = [root / name for name in ['release.json', 'roundtrip.json', 'dry-run-plan.json']]
        api.artifact.create(first, SHA, metadata)
        report.write_text(json.dumps(api.artifact.verify(second, metadata, SHA, first)))
        cls.payload = {'payload/' + name: raw for name, raw in api.artifact.inspect(first).items()}
        cls.payload['release.json'] = metadata.read_bytes()
        cls.candidate_bytes = zipped(cls.payload.items())
        env = {'GITHUB_SERVER_URL': 'https://github.com', 'GITHUB_API_URL': api.API_URL,
               'GITHUB_REPOSITORY': api.REPOSITORY, 'GITHUB_REPOSITORY_ID': str(api.REPOSITORY_ID),
               'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main', 'GITHUB_REF_PROTECTED': 'true',
               'GITHUB_SHA': SHA, 'GITHUB_WORKFLOW_SHA': SHA, 'GITHUB_WORKFLOW_REF': api.rehearsal.WORKFLOW_REF,
               'GITHUB_RUN_ID': str(RUN), 'GITHUB_RUN_ATTEMPT': str(ATTEMPT), **ENV}
        def producer_fetch(route, _env):
            if route == api.BRANCH_ROUTE: return {'name': 'main', 'protected': True, 'commit': {'sha': SHA}}
            return uploaded(CANDIDATE, 'candidate', cls.candidate_bytes)
        api.rehearsal.receipt(SHA, str(CANDIDATE), hashlib.sha256(cls.candidate_bytes).hexdigest(),
                              metadata, report, receipt, env, producer_fetch)
        cls.receipt_value = json.loads(receipt.read_bytes())
        cls.receipt_bytes = zipped([('dry-run-plan.json', receipt.read_bytes())])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='oss-candidate-case-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.candidate, self.receipt, self.output = [self.root / name for name in ['candidate.zip', 'receipt.zip', 'verified.json']]
        self.candidate.write_bytes(self.candidate_bytes)
        self.receipt.write_bytes(self.receipt_bytes)
        self.values = self.fixtures()
        self.calls = []

    def fixtures(self):
        return {api.BRANCH_ROUTE: {'name': 'main', 'protected': True, 'commit': {'sha': SHA}},
                api.WORKFLOW_ROUTE: {'id': api.WORKFLOW_ID, 'path': api.WORKFLOW_PATH, 'state': 'active'},
                api.run_route(RUN): run(), api.run_route(RUN, ATTEMPT): run(),
                api.artifact_route(CANDIDATE): uploaded(CANDIDATE, 'candidate', self.candidate_bytes),
                api.artifact_route(RECEIPT): uploaded(RECEIPT, 'rehearsal-receipt', self.receipt_bytes)}

    def fetch(self, route, environ):
        self.calls.append(route)
        self.assertEqual(environ, ENV)
        self.assertIn(route, self.values)
        return deepcopy(self.values[route])

    def args(self):
        return ['verify', '--expected-commit', SHA, '--run-id', str(RUN), '--run-attempt', str(ATTEMPT),
                '--candidate-id', str(CANDIDATE), '--candidate-archive', str(self.candidate),
                '--receipt-id', str(RECEIPT), '--receipt-archive', str(self.receipt), '--out', str(self.output)]

    def invoke(self, args=None, success=False, fetch=None):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = api.cli(args or self.args(), ENV, fetch or self.fetch)
        self.assertNotIn(MARKER, output.getvalue() + errors.getvalue())
        if success:
            self.assertEqual(code, 0, errors.getvalue())
            self.assertEqual(errors.getvalue(), '')
            return json.loads(output.getvalue())
        self.assertEqual(code, 1, output.getvalue())
        self.assertEqual(output.getvalue(), '')
        value = json.loads(errors.getvalue())
        self.assertEqual(set(value), {'error'})
        self.assertRegex(value['error'], '^[a-z_]+$')
        return value['error']

    def replace_archive(self, role, raw):
        path, number, prefix = (self.candidate, CANDIDATE, 'candidate') if role == 'candidate' else (self.receipt, RECEIPT, 'rehearsal-receipt')
        path.write_bytes(raw)
        self.values[api.artifact_route(number)] = uploaded(number, prefix, raw)

    def test_real_build_producer_receipt_consumer_roundtrip(self):
        value = self.invoke(success=True)
        self.assertEqual(value, json.loads(self.output.read_bytes()))
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertIs(value['deployment_authorized'], False)
        self.assertIs(value['rehearsal_candidate_verified'], True)
        self.assertEqual(value['descriptor'], json.loads(self.payload['release.json']))
        self.assertEqual(value['artifacts']['candidate']['digest_sha256'], hashlib.sha256(self.candidate_bytes).hexdigest())
        self.assertEqual(value['artifacts']['receipt']['digest_sha256'], hashlib.sha256(self.receipt_bytes).hexdigest())
        self.assertEqual(value['checks']['producer_reproducibility'], 'matched')
        self.assertEqual(value['checks']['consumer_rebuild'], 'not_performed')
        self.assertEqual(value['pending_gates'], api.PENDING_GATES)
        self.assertIn('required-github-checks', value['pending_gates'])
        self.assertNotIn(str(self.root), json.dumps(value))
        expected = list(self.fixtures())
        self.assertEqual(self.calls, expected + expected)
        content = api.archive_files(self.candidate_bytes, 'candidate')
        self.assertIn('payload/.htaccess', content)
        self.assertIn('payload/.well-known/security.txt', content)

    def test_invalid_cli_identities_fail_before_network(self):
        for flag in ['--run-id', '--run-attempt', '--candidate-id', '--receipt-id']:
            for bad in ['0', '01', '-1', '2.0', '1\n', '9' * 20, MARKER]:
                args = self.args()
                args[args.index(flag) + 1] = bad
                self.invoke(args)
        for bad in ['main', 'A' * 40, SHA + '\n', 'a' * 39, MARKER]:
            args = self.args()
            args[args.index('--expected-commit') + 1] = bad
            self.invoke(args)
        args = self.args()
        args[args.index('--receipt-id') + 1] = str(CANDIDATE)
        self.invoke(args)
        self.invoke(self.args() + ['--' + MARKER])
        self.assertEqual(self.calls, [])

    def test_main_and_workflow_are_fixed_and_strictly_typed(self):
        cases = [(api.BRANCH_ROUTE, 'protected', x) for x in [False, 1, 'true', None]]
        cases += [(api.BRANCH_ROUTE, 'name', 'other'), (api.BRANCH_ROUTE, 'commit', {'sha': 'b' * 40}),
                  (api.WORKFLOW_ROUTE, 'id', True), (api.WORKFLOW_ROUTE, 'id', float(api.WORKFLOW_ID)),
                  (api.WORKFLOW_ROUTE, 'id', api.WORKFLOW_ID + 1), (api.WORKFLOW_ROUTE, 'state', 'disabled_manually'),
                  (api.WORKFLOW_ROUTE, 'path', '../' + api.WORKFLOW_PATH)]
        for route, field, value in cases:
            with self.subTest(route=route, field=field, value=value):
                self.values = self.fixtures()
                self.values[route][field] = value
                self.invoke()
        self.assertFalse(self.output.exists())

    def test_successful_receipt_cannot_heal_bad_or_incomplete_run(self):
        cases = [('id', RUN + 1), ('id', float(RUN)), ('id', True), ('run_attempt', ATTEMPT + 1),
                 ('run_attempt', float(ATTEMPT)), ('workflow_id', api.WORKFLOW_ID + 1), ('workflow_id', True),
                 ('head_sha', 'b' * 40), ('head_branch', 'other'), ('path', api.WORKFLOW_PATH + '@other'),
                 ('event', 'pull_request'), ('event', 'workflow_dispatch'), ('event', 'workflow_run'),
                 ('status', 'in_progress'), ('conclusion', None), ('conclusion', 'failure'),
                 ('conclusion', 'cancelled'), ('conclusion', 'neutral'), ('conclusion', 'skipped')]
        for route in [api.run_route(RUN), api.run_route(RUN, ATTEMPT)]:
            for key, value in cases:
                self.values = self.fixtures()
                self.values[route][key] = value
                self.invoke()
            for role in ['repository', 'head_repository']:
                for key, value in [('id', api.REPOSITORY_ID + 1), ('id', str(api.REPOSITORY_ID)),
                                   ('id', True), ('fork', True), ('fork', 0), ('full_name', 'fork/website')]:
                    self.values = self.fixtures()
                    self.values[route][role][key] = value
                    self.invoke()
        self.assertFalse(self.output.exists())

    def test_each_artifact_binds_id_name_attempt_source_and_expiry(self):
        for number in [CANDIDATE, RECEIPT]:
            route = api.artifact_route(number)
            cases = [('id', number + 2), ('id', float(number)), ('id', True), ('name', MARKER),
                     ('name', self.fixtures()[route]['name'].replace('-248-2', '-248-1')),
                     ('expired', True), ('expired', 0), ('expired', 'false'), ('digest', 'sha256:' + 'f' * 64),
                     ('expires_at', None), ('expires_at', '2000-01-01T00:00:00Z'),
                     ('expires_at', '2999-01-01T00:00:00'), ('expires_at', '2999-13-01T00:00:00Z')]
            for field, value in cases:
                self.values = self.fixtures()
                self.values[route][field] = value
                self.invoke()
            for key, value in [('id', RUN + 1), ('id', True), ('repository_id', True),
                               ('repository_id', api.REPOSITORY_ID + 1), ('head_repository_id', api.REPOSITORY_ID + 1),
                               ('head_branch', 'other'), ('head_sha', 'c' * 40)]:
                self.values = self.fixtures()
                self.values[route]['workflow_run'][key] = value
                self.invoke()
        self.assertFalse(self.output.exists())

    def test_remote_changes_during_verification_never_write_success(self):
        cases = [(api.BRANCH_ROUTE, 'commit', {'sha': 'b' * 40}),
                 (api.BRANCH_ROUTE, 'protected', False), (api.WORKFLOW_ROUTE, 'state', 'disabled_manually'),
                 (api.run_route(RUN), 'run_attempt', ATTEMPT + 1),
                 (api.run_route(RUN, ATTEMPT), 'conclusion', 'failure'),
                 (api.artifact_route(CANDIDATE), 'expired', True),
                 (api.artifact_route(RECEIPT), 'digest', 'sha256:' + 'c' * 64)]
        for route, field, changed in cases:
            count = {}
            def fetch(path, env):
                value = self.fetch(path, env)
                count[path] = count.get(path, 0) + 1
                if path == route and count[path] == 2: value[field] = changed
                return value
            self.invoke(fetch=fetch)
            self.assertFalse(self.output.exists())

    def test_local_archive_bytes_are_bound_before_parsing(self):
        self.candidate.write_bytes(b'not a zip ' + MARKER.encode())
        with patch.object(api, 'archive_files', side_effect=AssertionError('must not parse')) as parse:
            self.assertEqual(self.invoke(), 'archive_digest_mismatch')
        parse.assert_not_called()
        self.candidate.write_bytes(self.receipt_bytes)
        self.receipt.write_bytes(self.candidate_bytes)
        self.assertIn(self.invoke(), {'archive_digest_mismatch', 'size_limit'})

    def test_receipt_semantics_are_exact_even_with_matching_remote_archive_digest(self):
        cases = [('deployment_authorized', True), ('deployment_authorized', 0), ('schema_version', True),
                 ('run_id', float(RUN)), ('run_attempt', ATTEMPT - 1), ('commit', 'b' * 40),
                 ('workflow_ref', MARKER), ('observed_main_protected', 1), ('descriptor_sha256', 'c' * 64),
                 ('pending_gates', []), ('checks', {'artifact_verified': 1, 'reproducibility': 'matched', 'transport_roundtrip': True}),
                 ('artifact', {'id': CANDIDATE, 'digest_sha256': 'd' * 64, 'metadata_verified': True}),
                 ('descriptor', {**self.receipt_value['descriptor'], 'file_count': float(self.receipt_value['descriptor']['file_count'])}),
                 (MARKER, MARKER)]
        for key, value in cases:
            receipt = {**self.receipt_value, key: value}
            self.replace_archive('receipt', zipped([('dry-run-plan.json', json.dumps(receipt))]))
            self.assertEqual(self.invoke(), 'invalid_receipt')
        self.assertFalse(self.output.exists())

    def test_receipt_duplicate_nonfinite_overlong_and_deep_json_fail(self):
        valid = json.dumps(self.receipt_value)
        cases = [valid[:-1] + ',"run_id":248}', '{"x":NaN}', '{"x":Infinity}', '[]', '\ud800',
                 ' ' * (api.MAX_RECEIPT + 1), '{"x":' + '[' * 2000 + '0' + ']' * 2000 + '}']
        for content in cases:
            raw = content.encode('utf-8', errors='surrogatepass')
            self.replace_archive('receipt', zipped([('dry-run-plan.json', raw)]))
            self.invoke()
        self.assertFalse(self.output.exists())

    def test_payload_checker_rejects_tampered_missing_extra_and_descriptor_changes(self):
        for case in ['changed', 'missing', 'extra', 'descriptor']:
            files = self.payload.copy()
            if case == 'changed': files['payload/index.html'] += b'\n' + MARKER.encode()
            if case == 'missing': del files['payload/index.html']
            if case == 'extra': files['payload/' + MARKER] = b'unwanted'
            if case == 'descriptor': files['release.json'] = json.dumps({**json.loads(files['release.json']), 'commit': 'b' * 40}).encode()
            self.replace_archive('candidate', zipped(files.items()))
            self.assertEqual(self.invoke(), 'payload_verification_failed')
        self.assertFalse(self.output.exists())

    def test_zip_paths_duplicates_empty_directories_and_foreign_layout_rejected(self):
        cases = [[('../escape', b'x')], [('/absolute', b'x')], [('payload/../outside', b'x')],
                 [('payload\\outside', b'x')], [('payload//file', b'x')], [('payload/./file', b'x')],
                 [('payload/file\x00ignored', b'x')], [('release.json', b'{}'), ('release.json', b'{}')],
                 [('payload/x', b'x'), ('payload/x/', b'')], [('unwanted.py', b'print(1)')],
                 [('release.json', b'{}'), ('payload/empty/', b'')]]
        for files in cases:
            raw = zipped(files)
            # ZipInfo construction truncates NUL itself; inject it into both headers instead.
            if files[0][0] == 'payload/file\x00ignored':
                raw = zipped([('payload/fileXignored', b'x')]).replace(b'fileXignored', b'file\x00ignored')
            with self.subTest(files=files):
                with self.assertRaises(ArtifactError): api.archive_files(raw, 'candidate')
        for files in [[('other.json', b'{}')], [('dry-run-plan.json', b'{}'), ('payload/x', b'x')], [('dry-run-plan.json/', b'')]]:
            with self.assertRaises(ArtifactError): api.archive_files(zipped(files), 'receipt')

    def test_zip_symbolic_special_and_encrypted_members_rejected(self):
        for mode in [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFBLK]:
            info = zipfile.ZipInfo('payload/test')
            info.create_system = 3
            info.external_attr = (mode | 0o600) << 16
            with self.assertRaises(ArtifactError): api.archive_files(zipped([(info, b'../../outside')]), 'candidate')
        raw = bytearray(zipped([('release.json', b'{}')]))
        central = raw.index(b'PK\x01\x02')
        struct.pack_into('<H', raw, 6, 1)
        struct.pack_into('<H', raw, central + 8, 1)
        with self.assertRaises(ArtifactError): api.archive_files(bytes(raw), 'candidate')

    def test_zip_budgets_precede_infolist_and_decompression(self):
        raw = bytearray(self.candidate_bytes)
        struct.pack_into('<HH', raw, len(raw) - 22 + 8, tree.MAX_ENTRIES + 1, tree.MAX_ENTRIES + 1)
        with patch.object(api.zipfile, 'ZipFile', side_effect=AssertionError('must not allocate')) as opened:
            with self.assertRaises(ArtifactError): api.archive_files(bytes(raw), 'candidate')
        opened.assert_not_called()
        # A physically large directory cannot lie about its count to evade the preflight.
        many = bytearray(zipped([(f'payload/x{i}', b'') for i in range(tree.MAX_ENTRIES + 1)]))
        struct.pack_into('<HH', many, len(many) - 22 + 8, 1, 1)
        with self.assertRaises(ArtifactError): api.archive_files(bytes(many), 'candidate')
        bomb = zipped([('payload/bomb', b'0' * (tree.MAX_FILE_BYTES + 1))])
        with patch.object(zipfile.ZipFile, 'open', side_effect=AssertionError('must not inflate')) as inflated:
            with self.assertRaises(ArtifactError): api.archive_files(bomb, 'candidate')
        inflated.assert_not_called()
        with patch.object(api, 'MAX_CENTRAL', 1):
            with self.assertRaises(ArtifactError): api.archive_files(self.candidate_bytes, 'candidate')

    def test_decompression_enforces_actual_bytes_and_total_budget(self):
        raw = bytearray(zipped([('dry-run-plan.json', b'{}IGNORED_DECOMPRESSED_BYTES')]))
        central = raw.index(b'PK\x01\x02')
        for position in [14, central + 16]: struct.pack_into('<L', raw, position, zlib.crc32(b'{}'))
        for position in [22, central + 24]: struct.pack_into('<L', raw, position, 2)
        with self.assertRaises(ArtifactError): api.archive_files(bytes(raw), 'receipt')
        hidden_directory = bytearray(zipped([('payload/', b'hidden directory body'), ('payload/file', b'x'), ('release.json', b'{}')]))
        central = hidden_directory.index(b'PK\x01\x02')
        for position in [14, 22, central + 16, central + 24]:
            struct.pack_into('<L', hidden_directory, position, 0)
        with self.assertRaises(ArtifactError): api.archive_files(bytes(hidden_directory), 'candidate')
        with patch.object(tree, 'MAX_TOTAL_BYTES', 0):
            with self.assertRaises(ArtifactError):
                api.archive_files(zipped([('release.json', b'{}'), ('payload/file', b'x' * 4097)]), 'candidate')

    def test_zip_crc_truncation_contradictory_directory_and_zip64(self):
        stored = io.BytesIO()
        with zipfile.ZipFile(stored, 'w', compression=zipfile.ZIP_STORED) as z:
            z.writestr('release.json', b'{}')
        damaged = stored.getvalue().replace(b'{}', b'{!')
        with self.assertRaises(ArtifactError): api.archive_files(damaged, 'candidate')
        for raw in [self.candidate_bytes[:-1], b'prefix' + self.candidate_bytes, self.candidate_bytes + b'tail']:
            with self.assertRaises(ArtifactError): api.archive_files(raw, 'candidate')
        # Archiver-compatible ZIP64 footer, with unchanged small ordinary entries.
        raw = self.candidate_bytes
        end = len(raw) - 22
        footer = list(struct.unpack_from('<4s4H2LH', raw, end))
        record = struct.pack('<4sQ2H2L4Q', b'PK\x06\x06', 44, 45, 45, 0, 0, footer[4], footer[4], footer[5], footer[6])
        locator = struct.pack('<4sLQL', b'PK\x06\x07', 0, end, 1)
        footer[3:7] = [65535, 65535, 4294967295, 4294967295]
        zip64 = raw[:end] + record + locator + struct.pack('<4s4H2LH', *footer)
        self.assertEqual(api.archive_files(zip64, 'candidate'), api.archive_files(raw, 'candidate'))
        corrupt = bytearray(zip64)
        struct.pack_into('<Q', corrupt, end + 32, tree.MAX_ENTRIES + 1)
        with self.assertRaises(ArtifactError): api.archive_files(bytes(corrupt), 'candidate')

    def test_local_links_hardlinks_fifos_and_oversize_fail_before_network(self):
        original = self.candidate.read_bytes()
        for kind in ['symlink', 'hardlink', 'fifo', 'oversize']:
            self.candidate.unlink()
            if kind == 'symlink': self.candidate.symlink_to(self.receipt)
            if kind == 'hardlink': os.link(self.receipt, self.candidate)
            if kind == 'fifo': os.mkfifo(self.candidate)
            if kind == 'oversize':
                with self.candidate.open('wb') as f: f.truncate(api.MAX_ARCHIVE + 1)
            self.invoke()
            self.candidate.unlink()
            self.candidate.write_bytes(original)
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        args = self.args()
        args[args.index('--candidate-archive') + 1] = str(alias / 'candidate.zip')
        self.invoke(args)
        self.assertEqual(self.calls, [])

    def test_exclusive_output_and_output_symlinks_preserve_existing_bytes(self):
        self.output.write_text(MARKER)
        self.invoke()
        self.assertEqual(self.output.read_text(), MARKER)
        self.output.unlink()
        self.output.symlink_to(self.receipt)
        self.invoke()
        self.assertEqual(self.receipt.read_bytes(), self.receipt_bytes)
        self.output.unlink()
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        args = self.args()
        args[args.index('--out') + 1] = str(alias / 'new.json')
        self.invoke(args)
        self.assertFalse((self.root / 'new.json').exists())

    def test_fixed_get_transport_is_bounded_and_never_redirects_authentication(self):
        response = Response(b'{"name":"main"}')
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with patch.object(api.urllib.request, 'build_opener', return_value=opener) as builder:
            self.assertEqual(api.github_get(api.BRANCH_ROUTE, ENV), {'name': 'main'})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, api.API_URL + api.BRANCH_ROUTE)
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(opener.open.call_args.kwargs, {'timeout': 10})
        self.assertEqual(response.reads, [api.rehearsal.MAX_JSON + 1])
        self.assertIsNone(builder.call_args.args[0].redirect_request(request, None, 302, MARKER, {}, 'https://example.invalid'))
        for route in ['https://example.invalid/' + MARKER, api.BASE_ROUTE + '/actions/artifacts/1/zip',
                      api.run_route(RUN) + '?x=' + MARKER, api.BASE_ROUTE + '/actions/runs/01',
                      api.BASE_ROUTE + '/actions/workflows/1', '/repos/fork/website/branches/main']:
            with patch.object(api.urllib.request, 'build_opener') as opened:
                with self.assertRaises(ArtifactError): api.github_get(route, ENV)
                opened.assert_not_called()

    def test_transport_failures_bad_json_and_diagnostics_do_not_leak(self):
        bodies = [b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{' + MARKER.encode(),
                  b' ' * (api.rehearsal.MAX_JSON + 1), b'{"x":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}']
        for raw in bodies:
            opener = unittest.mock.Mock()
            opener.open.return_value = Response(raw)
            with patch.object(api.urllib.request, 'build_opener', return_value=opener):
                self.invoke(fetch=api.github_get)
            self.assertEqual(opener.open.call_count, 1)
        for error in [TimeoutError(MARKER), urllib.error.HTTPError(api.API_URL, 302, MARKER, {}, None)]:
            opener = unittest.mock.Mock()
            opener.open.side_effect = error
            with patch.object(api.urllib.request, 'build_opener', return_value=opener):
                self.invoke(fetch=api.github_get)
            self.assertEqual(opener.open.call_count, 1)
        def noisy(_route, _env):
            print(MARKER)
            print(MARKER, file=sys.stderr)
            raise ArtifactError(MARKER)
        self.assertEqual(self.invoke(fetch=noisy), 'candidate_verification_failed')
        for env in [{}, {'GH_TOKEN': MARKER + '\n'}]:
            with patch.object(api.urllib.request, 'build_opener') as opened:
                with self.assertRaises(ArtifactError): api.github_get(api.BRANCH_ROUTE, env)
                opened.assert_not_called()

    def test_product_checker_stdout_stderr_and_unexpected_exception_are_sanitized(self):
        def checker(*_args):
            print(MARKER)
            print(MARKER, file=sys.stderr)
            raise RuntimeError(MARKER)
        with patch.object(api.artifact, 'verify', side_effect=checker):
            self.assertEqual(self.invoke(), 'payload_verification_failed')
        self.assertFalse(self.output.exists())

    def test_only_two_canonical_run_path_forms_are_accepted(self):
        for path in [api.WORKFLOW_PATH, api.WORKFLOW_PATH + '@main']:
            value = run()
            value['path'] = path
            api.check_run(value, SHA, RUN, ATTEMPT)
        for path in [api.WORKFLOW_PATH + '@refs/heads/main', api.WORKFLOW_PATH + '@main/extra',
                     'other/' + api.WORKFLOW_PATH, api.WORKFLOW_PATH + '@' + SHA]:
            value = run()
            value['path'] = path
            with self.assertRaises(ArtifactError): api.check_run(value, SHA, RUN, ATTEMPT)

    def test_unindexed_local_records_and_contradictory_headers_are_rejected(self):
        ordinary = zipped([('dry-run-plan.json', b'{}')])
        foreign = zipped([('unlisted.py', b'print("never execute")')])
        offset = struct.unpack_from('<L', ordinary, len(ordinary) - 6)[0]
        foreign_offset = struct.unpack_from('<L', foreign, len(foreign) - 6)[0]
        added = foreign[:foreign_offset]
        changed = bytearray(ordinary[:offset] + added + ordinary[offset:])
        struct.pack_into('<L', changed, len(changed) - 6, offset + len(added))
        with self.assertRaises(ArtifactError): api.archive_files(bytes(changed), 'receipt')
        for offset, form, value in [(8, '<H', zipfile.ZIP_STORED), (14, '<L', 42),
                                     (18, '<L', 1), (22, '<L', 1), (4, '<H', 45), (6, '<H', 8)]:
            changed = bytearray(ordinary)
            struct.pack_into(form, changed, offset, value)
            with self.assertRaises(ArtifactError): api.archive_files(bytes(changed), 'receipt')

    def test_local_zip64_and_streaming_descriptor_forms_are_supported(self):
        class Streaming(io.BytesIO):
            def seek(self, *_args): raise io.UnsupportedOperation('streaming fixture')
        for streaming in [False, True]:
            for force_zip64 in [False, True]:
                output = Streaming() if streaming else io.BytesIO()
                with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    with archive.open('dry-run-plan.json', 'w', force_zip64=force_zip64) as entry:
                        entry.write(b'{}')
                raw = output.getvalue()
                self.assertEqual(api.archive_files(raw, 'receipt'), {'dry-run-plan.json': b'{}'})
                if streaming:
                    # Remove only the optional data-descriptor signature and adjust the central offset.
                    descriptor = raw.index(b'PK\x07\x08')
                    unsigned = bytearray(raw[:descriptor] + raw[descriptor + 4:])
                    offset = struct.unpack_from('<L', unsigned, len(unsigned) - 6)[0]
                    struct.pack_into('<L', unsigned, len(unsigned) - 6, offset - 4)
                    self.assertEqual(api.archive_files(bytes(unsigned), 'receipt'), {'dry-run-plan.json': b'{}'})
                    corrupt = bytearray(raw)
                    corrupt[descriptor + 4] ^= 1
                    with self.assertRaises(ArtifactError): api.archive_files(bytes(corrupt), 'receipt')

    def test_full_deflate_eof_and_unused_compressed_tail_are_required(self):
        raw = zipped([('dry-run-plan.json', b'{}')])
        def with_data(new_data):
            value = bytearray(raw)
            central = value.index(b'PK\x01\x02')
            name_len, extra_len = struct.unpack_from('<2H', value, 26)
            start = 30 + name_len + extra_len
            value = bytearray(value[:start] + new_data + value[central:])
            new_central = start + len(new_data)
            struct.pack_into('<L', value, 18, len(new_data))
            struct.pack_into('<L', value, new_central + 20, len(new_data))
            struct.pack_into('<L', value, len(value) - 6, new_central)
            return bytes(value)
        central = raw.index(b'PK\x01\x02')
        name_len, extra_len = struct.unpack_from('<2H', raw, 26)
        body = raw[30 + name_len + extra_len:central]
        for data in [body[:-1], body + b'ignored compressed tail', body + body]:
            with self.assertRaises(ArtifactError): api.archive_files(with_data(data), 'receipt')
        # A valid near-budget highly compressed member exercises repeated bounded draining.
        data = b'x' * (tree.MAX_FILE_BYTES - 1)
        expanded = api.archive_files(zipped([('release.json', b'{}'), ('payload/large', data)]), 'candidate')
        self.assertEqual(expanded['payload/large'], data)

    def test_actual_fd_rejects_replacement_and_same_size_mutation(self):
        original_open = tree.os.open
        replaced = False
        def replace_before_open(name, flags, *args, **kwargs):
            nonlocal replaced
            if name == 'candidate.zip' and not replaced:
                replaced = True
                self.candidate.unlink()
                os.mkfifo(self.candidate)
            return original_open(name, flags, *args, **kwargs)
        with patch.object(tree.os, 'open', side_effect=replace_before_open):
            self.invoke()
        self.assertTrue(replaced)
        self.assertEqual(self.calls, [])
        self.candidate.unlink()
        self.candidate.write_bytes(self.candidate_bytes)
        before = self.candidate.stat()
        original_read = tree.os.read
        changed = False
        def mutate_during_read(fd, count):
            nonlocal changed
            data = original_read(fd, count)
            if not changed:
                changed = True
                with self.candidate.open('r+b') as f:
                    f.seek(-1, os.SEEK_END)
                    f.write(b'X')
                os.utime(self.candidate, ns=(before.st_atime_ns, before.st_mtime_ns))
            return data
        with patch.object(tree.os, 'read', side_effect=mutate_during_read):
            self.assertEqual(self.invoke(), 'tree_changed')
        self.assertTrue(changed)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.output.exists())

    def test_consumer_uses_no_subprocess_or_generic_archive_extraction(self):
        with patch.object(subprocess, 'Popen', side_effect=AssertionError('must not execute')) as execute, \
             patch.object(zipfile.ZipFile, 'extractall', side_effect=AssertionError('must not extractall')) as extract:
            self.invoke(success=True)
        execute.assert_not_called()
        extract.assert_not_called()

    def test_python_optimized_mode_still_validates_and_writes_exclusively(self):
        fixtures = self.root / 'fixtures.json'
        fixtures.write_text(json.dumps(self.values))
        code = '''import importlib.util,json,sys
s=importlib.util.spec_from_file_location('candidate',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
f=json.load(open(sys.argv[2]));raise SystemExit(m.cli(sys.argv[3:],{},lambda route,env:f[route]))'''
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'scripts'), 'PYTHONDONTWRITEBYTECODE': '1'}
        result = subprocess.run([sys.executable, '-O', '-c', code, str(CLI), str(fixtures), *self.args()],
                                capture_output=True, text=True, timeout=20, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(json.loads(result.stdout)['deployment_authorized'], False)
        self.output.unlink()
        self.values[api.run_route(RUN)]['conclusion'] = 'failure'
        fixtures.write_text(json.dumps(self.values))
        result = subprocess.run([sys.executable, '-O', '-c', code, str(CLI), str(fixtures), *self.args()],
                                capture_output=True, text=True, timeout=20, env=env)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertFalse(self.output.exists())
        self.assertNotIn(MARKER, result.stderr)


# Use the same exception class as the trusted helper and consumer.
ArtifactError = tree.ArtifactError
if __name__ == '__main__':
    unittest.main()
