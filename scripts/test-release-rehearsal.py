#!/usr/bin/env python3
"""Offline canonical-main rehearsal tests; no GitHub or provider access."""
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
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / 'scripts/release-rehearsal.py'
SHA = 'a' * 40
DIGEST = 'b' * 64
MARKER = 'REJECTED_REHEARSAL_FIXTURE_5803'
spec = importlib.util.spec_from_file_location('rehearsal_tests', CLI)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def environment():
    return {'GITHUB_SERVER_URL': 'https://github.com', 'GITHUB_API_URL': 'https://api.github.com',
            'GITHUB_REPOSITORY': 'oss-singularity/website', 'GITHUB_REPOSITORY_ID': '1351274990',
            'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main', 'GITHUB_REF_PROTECTED': 'true',
            'GITHUB_SHA': SHA, 'GITHUB_WORKFLOW_SHA': SHA,
            'GITHUB_WORKFLOW_REF': 'oss-singularity/website/.github/workflows/static-release-rehearsal.yml@refs/heads/main',
            'GITHUB_RUN_ID': '248', 'GITHUB_RUN_ATTEMPT': '2', 'GH_TOKEN': 'public-fixture-' + MARKER}


def branch():
    return {'name': 'main', 'protected': True, 'commit': {'sha': SHA}, 'irrelevant': MARKER}


def uploaded():
    return {'id': 97, 'name': 'static-candidate-' + SHA + '-248-2', 'digest': 'sha256:' + DIGEST,
            'expired': False, 'expires_at': (datetime.now(timezone.utc) + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'workflow_run': {'id': 248, 'repository_id': 1351274990, 'head_repository_id': 1351274990,
                             'head_branch': 'main', 'head_sha': SHA}, 'irrelevant': MARKER}


class Response:
    def __init__(self, body, status=200, url=None):
        self.body = body
        self.status = status
        self.url = url or api.API_URL + api.BRANCH_ROUTE
        self.reads = []

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def geturl(self): return self.url

    def read(self, size):
        self.reads.append(size)
        return self.body[:size]


class RehearsalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builds = tempfile.TemporaryDirectory(prefix='oss-rehearsal-build-tests-')
        cls.addClassCleanup(cls.builds.cleanup)
        first, second = [Path(cls.builds.name) / name for name in ['first', 'second']]
        for target in [first, second]:
            subprocess.run(['sh', str(ROOT / 'scripts/build-site.sh'), str(target)], check=True,
                           capture_output=True, timeout=30, env={**os.environ, 'LC_ALL': 'C'})
        cls.descriptor_source = Path(cls.builds.name) / 'release.json'
        api.artifact.create(first, SHA, cls.descriptor_source)
        cls.report_value = api.artifact.verify(second, cls.descriptor_source, SHA, first)
        cls.descriptor_bytes = cls.descriptor_source.read_bytes()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='oss-rehearsal-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.metadata = self.root / 'release.json'
        self.report = self.root / 'report.json'
        self.output = self.root / 'receipt.json'
        self.metadata.write_bytes(self.descriptor_bytes)
        self.report.write_text(json.dumps(self.report_value))
        self.env, self.main, self.upload = environment(), branch(), uploaded()
        self.calls = []

    def fetch(self, route, environ):
        self.calls.append(route)
        self.assertEqual(environ, self.env)
        if route == api.BRANCH_ROUTE: return deepcopy(self.main)
        self.assertEqual(route, api.ARTIFACT_ROUTE + '97')
        return deepcopy(self.upload)

    def receipt_args(self):
        return ['receipt', '--checkout-commit', SHA, '--artifact-id', '97', '--artifact-digest', DIGEST,
                '--descriptor', str(self.metadata), '--roundtrip-report', str(self.report), '--out', str(self.output)]

    def invoke(self, args, success=True, fetch=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = api.cli(args, self.env, fetch or self.fetch)
        self.assertNotIn(MARKER, stdout.getvalue() + stderr.getvalue())
        if success:
            self.assertEqual(status, 0, stderr.getvalue())
            self.assertEqual(stderr.getvalue(), '')
            return json.loads(stdout.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), '')
        value = json.loads(stderr.getvalue())
        self.assertEqual(set(value), {'error'})
        self.assertRegex(value['error'], r'^[a-z_]+$')
        return value['error']

    def test_real_descriptor_roundtrip_gate_and_receipt_have_limited_claims(self):
        gated = self.invoke(['gate', '--checkout-commit', SHA])
        self.assertEqual(gated['run_id'], 248)
        self.assertEqual(gated['run_attempt'], 2)
        self.assertIs(gated['observed_main_protected'], True)
        self.assertIs(gated['deployment_authorized'], False)
        result = self.invoke(self.receipt_args())
        self.assertEqual(json.loads(self.output.read_bytes()), result)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result['descriptor'], json.loads(self.descriptor_bytes))
        self.assertEqual(result['descriptor_sha256'], hashlib.sha256(self.descriptor_bytes).hexdigest())
        self.assertEqual(result['artifact'], {'id': 97, 'digest_sha256': DIGEST, 'metadata_verified': True})
        self.assertEqual(result['checks'], {'artifact_verified': True, 'reproducibility': 'matched', 'transport_roundtrip': True})
        self.assertIn('successful-github-run', result['pending_gates'])
        self.assertIn('required-github-checks', result['pending_gates'])
        self.assertIn('trusted-provenance-consumption', result['pending_gates'])
        self.assertIs(result['deployment_authorized'], False)
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(self.calls, [api.BRANCH_ROUTE, api.BRANCH_ROUTE, api.ARTIFACT_ROUTE + '97'])

    def test_wrong_or_missing_native_context_fails_before_network(self):
        bad = [('GITHUB_SERVER_URL', 'https://example.invalid'), ('GITHUB_API_URL', 'https://example.invalid'),
               ('GITHUB_REPOSITORY', 'fork/website'), ('GITHUB_REPOSITORY_ID', '1351274991'),
               ('GITHUB_EVENT_NAME', 'pull_request'), ('GITHUB_EVENT_NAME', 'workflow_dispatch'),
               ('GITHUB_EVENT_NAME', 'workflow_run'), ('GITHUB_REF', 'refs/tags/main'),
               ('GITHUB_REF', 'refs/heads/other'), ('GITHUB_REF_PROTECTED', 'false'),
               ('GITHUB_REF_PROTECTED', True), ('GITHUB_REF_PROTECTED', 'True'),
               ('GITHUB_SHA', 'b' * 40), ('GITHUB_WORKFLOW_SHA', 'b' * 40),
               ('GITHUB_WORKFLOW_REF', api.WORKFLOW_REF.replace('main', MARKER)),
               ('GITHUB_RUN_ID', True), ('GITHUB_RUN_ID', 248), ('GITHUB_RUN_ID', '01'),
               ('GITHUB_RUN_ID', '0'), ('GITHUB_RUN_ATTEMPT', '1.0'), ('GITHUB_RUN_ATTEMPT', '2\n' + MARKER),
               ('GITHUB_RUN_ATTEMPT', '9' * 10000)]
        bad += [(key, None) for key in environment() if key != 'GH_TOKEN']
        for key, value in bad:
            with self.subTest(field=key, case=str(value)[:30]):
                self.env = {**environment(), key: value}
                self.invoke(['gate', '--checkout-commit', SHA], success=False)
        self.assertEqual(self.calls, [])

    def test_invalid_checkout_and_arguments_never_reach_network(self):
        for value in ['main', 'A' * 40, SHA + '\n', MARKER, 'a' * 39]:
            self.invoke(['gate', '--checkout-commit', value], success=False)
        self.invoke(['gate', '--checkout-commit', SHA, '--' + MARKER], success=False)
        self.assertEqual(self.calls, [])

    def test_unprotected_missing_wrong_or_advanced_main_is_rejected(self):
        for value in [{}, [], {'name': 'main', 'protected': 1, 'commit': {'sha': SHA}},
                      {**branch(), 'protected': 'true'}, {**branch(), 'protected': False},
                      {**branch(), 'name': 'other'}, {**branch(), 'commit': None},
                      {**branch(), 'commit': {'sha': 'c' * 40}}]:
            self.main = value
            self.invoke(['gate', '--checkout-commit', SHA], success=False)

    def test_advance_between_initial_gate_and_receipt_produces_no_receipt(self):
        self.invoke(['gate', '--checkout-commit', SHA])
        self.main['commit']['sha'] = 'c' * 40
        self.invoke(self.receipt_args(), success=False)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls, [api.BRANCH_ROUTE, api.BRANCH_ROUTE])

    def test_upload_identity_attempt_and_expiry_are_exact(self):
        bad = [('id', True), ('id', 97.0), ('id', '97'), ('id', 98), ('expired', True),
               ('expired', 0), ('expired', 'false'), ('digest', DIGEST), ('digest', 'sha256:' + 'c' * 64),
               ('name', 'static-candidate-' + SHA + '-248-1'), ('name', MARKER),
               ('expires_at', None), ('expires_at', '2000-01-01T00:00:00Z'),
               ('expires_at', '2999-01-01T00:00:00'), ('expires_at', '2999-13-01T00:00:00Z'),
               ('expires_at', '2999-01-01T00:00:00+00:00')]
        for key, value in bad:
            with self.subTest(field=key, case=value):
                self.upload = {**uploaded(), key: value}
                self.invoke(self.receipt_args(), success=False)
                self.assertFalse(self.output.exists())
        for key in ['id', 'name', 'digest', 'expired', 'expires_at', 'workflow_run']:
            self.upload = uploaded()
            del self.upload[key]
            self.invoke(self.receipt_args(), success=False)

    def test_upload_run_repository_and_source_mismatches_fail(self):
        bad = [('id', 249), ('id', 248.0), ('id', True), ('repository_id', 42),
               ('repository_id', '1351274990'), ('head_repository_id', 42), ('head_repository_id', True),
               ('head_sha', 'b' * 40), ('head_branch', 'other')]
        for key, value in bad:
            self.upload = uploaded()
            self.upload['workflow_run'][key] = value
            self.invoke(self.receipt_args(), success=False)
        self.assertFalse(self.output.exists())

    def test_cli_artifact_identity_rejects_invalid_strings_before_network(self):
        args = self.receipt_args()
        for flag, values in [('--artifact-id', ['0', '01', '-1', '1.5', '9' * 50, MARKER]),
                             ('--artifact-digest', ['sha256:' + DIGEST, 'B' * 64, DIGEST + '\n', MARKER])]:
            for value in values:
                changed = args.copy()
                changed[changed.index(flag) + 1] = value
                self.invoke(changed, success=False)
        self.assertEqual(self.calls, [])

    def test_roundtrip_report_accepts_only_exact_existing_artifact_contract(self):
        cases = [('artifact_verified', 1), ('artifact_verified', False), ('deployment_authorized', 0),
                 ('deployment_authorized', True), ('reproducibility', 'not_checked'), ('pending_gates', []),
                 ('schema_version', True), ('file_count', float(self.report_value['file_count'])),
                 ('commit', 'b' * 40), ('manifest_sha256', 'c' * 64), (MARKER, MARKER)]
        for key, value in cases:
            self.report.write_text(json.dumps({**self.report_value, key: value}))
            self.invoke(self.receipt_args(), success=False)
        self.report.write_text(json.dumps(self.report_value)[:-1] + ',"artifact_verified":true}')
        self.invoke(self.receipt_args(), success=False)
        self.report.write_text(' ' * 16385)
        self.invoke(self.receipt_args(), success=False)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.output.exists())

    def test_descriptor_is_strict_and_receipt_hash_binds_original_bytes(self):
        original = json.loads(self.descriptor_bytes)
        for key, value in [('repository', 'fork/website'), ('commit', 'b' * 40), ('schema_version', True),
                           ('total_bytes', float('nan')), (MARKER, MARKER)]:
            self.metadata.write_text(json.dumps({**original, key: value}))
            self.invoke(self.receipt_args(), success=False)
        self.metadata.write_bytes(self.descriptor_bytes[:-2] + b',"commit":"' + SHA.encode() + b'"}\n')
        self.invoke(self.receipt_args(), success=False)
        self.assertEqual(self.calls, [])

    def test_json_rejects_duplicates_nonfinite_invalid_and_bounded_input(self):
        values = [b'{"protected":true,"protected":false}', b'{"nested":{"id":1,"id":2}}',
                  b'{"x":NaN}', b'{"x":Infinity}', b'[]', b'null', b'{', b'\xff', b'\xef\xbb\xbf{}',
                  b'[' * 2000, b' ' * (api.MAX_JSON + 1)]
        for value in values:
            with self.assertRaises(api.ArtifactError): api.decode_object(value, api.MAX_JSON)

    def test_http_transport_is_one_fixed_get_and_never_redirects_authentication(self):
        response = Response(json.dumps(branch()).encode())
        with patch.object(urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = response
            value = api.github_get(api.BRANCH_ROUTE, self.env)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, api.API_URL + api.BRANCH_ROUTE)
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.env['GH_TOKEN'])
            self.assertEqual(build.return_value.open.call_args.kwargs, {'timeout': 10})
            self.assertIsInstance(build.call_args.args[0], api.NoRedirect)
            self.assertIsNone(build.call_args.args[0].redirect_request(request, None, 302, '', {}, 'https://example.invalid'))
            self.assertEqual(response.reads, [api.MAX_JSON + 1])
            self.assertEqual(value, branch())
            self.assertEqual(build.return_value.open.call_count, 1)

    def test_http_error_redirect_destination_and_overlong_body_fail_silently(self):
        for response in [Response(b'{}', status=301), Response(b'{}', status=403),
                         Response(b'{}', url='https://example.invalid/' + MARKER),
                         Response(b' ' * (api.MAX_JSON + 2)), Response(b'{"protected":1,"protected":2}')]:
            with patch.object(urllib.request, 'build_opener') as build:
                build.return_value.open.return_value = response
                self.invoke(['gate', '--checkout-commit', SHA], success=False, fetch=api.github_get)
                self.assertEqual(build.return_value.open.call_count, 1)
        errors = [OSError(MARKER), TimeoutError(MARKER),
                  urllib.error.HTTPError('https://example.invalid/' + MARKER, 302, MARKER, {}, io.BytesIO(MARKER.encode()))]
        for error in errors:
            with patch.object(urllib.request, 'build_opener') as build:
                build.return_value.open.side_effect = error
                self.invoke(['gate', '--checkout-commit', SHA], success=False, fetch=api.github_get)
                self.assertEqual(build.return_value.open.call_count, 1)

    def test_bad_authentication_or_api_route_cannot_open_transport(self):
        with patch.object(urllib.request, 'build_opener') as build:
            for value in [None, '', 'line\n' + MARKER, 'x' * 8193, '\u2600']:
                with self.assertRaises(api.ArtifactError):
                    api.github_get(api.BRANCH_ROUTE, {**self.env, 'GH_TOKEN': value})
            for route in ['/repos/fork/website/branches/main', api.ARTIFACT_ROUTE + '../1',
                          'https://example.invalid', api.BRANCH_ROUTE + '?' + MARKER]:
                with self.assertRaises(api.ArtifactError): api.github_get(route, self.env)
            build.assert_not_called()

    def test_dependency_output_and_error_content_are_not_copied(self):
        def noisy(_route, _env):
            print(MARKER)
            print(MARKER, file=sys.stderr)
            raise RuntimeError(MARKER)
        self.invoke(['gate', '--checkout-commit', SHA], success=False, fetch=noisy)

    def test_receipt_exclusive_write_and_input_output_path_guards(self):
        self.output.write_text(MARKER)
        self.invoke(self.receipt_args(), success=False)
        self.assertEqual(self.output.read_text(), MARKER)
        self.output.unlink()
        link = self.root / 'linked-parent'
        link.symlink_to(self.root, target_is_directory=True)
        args = self.receipt_args()
        args[-1] = str(link / 'new.json')
        self.invoke(args, success=False)
        self.assertFalse((self.root / 'new.json').exists())
        self.metadata.unlink()
        self.metadata.symlink_to(self.descriptor_source)
        self.invoke(self.receipt_args(), success=False)
        self.metadata.unlink()
        self.metadata.write_bytes(self.descriptor_bytes)
        extra = self.root / 'hardlink.json'
        os.link(self.metadata, extra)
        self.invoke(self.receipt_args(), success=False)

    def test_optimized_python_runs_valid_gate_and_still_rejects_bad_context(self):
        for valid in [True, False]:
            values = environment()
            if not valid: values['GITHUB_REF_PROTECTED'] = 'false'
            code = ('import importlib.util,json,sys\n'
                    f'sys.path.insert(0,{str(CLI.parent)!r})\n'
                    f's=importlib.util.spec_from_file_location("tested",{str(CLI)!r})\n'
                    'm=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n'
                    f'e=json.loads({json.dumps(values)!r});b=json.loads({json.dumps(branch())!r})\n'
                    f'raise SystemExit(m.cli(["gate","--checkout-commit",{SHA!r}],e,lambda *_:b))\n')
            result = subprocess.run([sys.executable, '-O', '-c', code], text=True, capture_output=True,
                                    timeout=10, env={'PATH': os.defpath})
            self.assertNotIn(MARKER, result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0 if valid else 1)
            if valid: self.assertIs(json.loads(result.stdout)['deployment_authorized'], False)
            else: self.assertEqual(json.loads(result.stderr), {'error': 'untrusted_context'})


if __name__ == '__main__':
    unittest.main()
