"""Real Commons packets with offline GitHub identity and failure fixtures."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import commons_artifact as artifact
import commons_rehearsal as api
from site_artifact import ArtifactError

ROOT = Path(__file__).resolve().parent.parent
SHA, DIGEST = 'a' * 40, 'b' * 64
MARKER = 'PRIVATE_REHEARSAL_FIXTURE_6142'
spec = importlib.util.spec_from_file_location('commons_rehearsal_cli', ROOT / 'scripts/commons-release-rehearsal.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def environment():
    return {'GITHUB_ACTIONS': 'true', 'GITHUB_SERVER_URL': 'https://github.com',
            'GITHUB_API_URL': 'https://api.github.com', 'GITHUB_REPOSITORY': api.REPOSITORY,
            'GITHUB_REPOSITORY_ID': str(api.REPOSITORY_ID), 'GITHUB_EVENT_NAME': 'push',
            'GITHUB_REF': 'refs/heads/main', 'GITHUB_REF_PROTECTED': 'true', 'GITHUB_SHA': SHA,
            'GITHUB_WORKFLOW_SHA': SHA, 'GITHUB_WORKFLOW_REF': api.WORKFLOW_REF,
            'GITHUB_RUN_ID': '248', 'GITHUB_RUN_ATTEMPT': '2', 'GH_TOKEN': MARKER}


def uploaded():
    return {'id': 97, 'name': 'commons-candidate-' + SHA + '-248-2', 'digest': 'sha256:' + DIGEST,
            'expired': False, 'expires_at': (datetime.now(timezone.utc) + timedelta(days=7)).isoformat().replace('+00:00', 'Z'),
            'workflow_run': {'id': 248, 'repository_id': api.REPOSITORY_ID,
                             'head_repository_id': api.REPOSITORY_ID, 'head_branch': 'main', 'head_sha': SHA},
            'unused': MARKER}


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oss-commons-rehearsal-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        shutil.copytree(ROOT / 'services/commons', self.source)
        self.packet, self.output = self.root / 'commons.json', self.root / 'receipt.json'
        artifact.create(self.source, SHA, self.packet)
        self.env, self.upload = environment(), uploaded()
        self.branch = {'name': 'main', 'protected': True, 'commit': {'sha': SHA}, 'unused': MARKER}
        self.calls = []

    def fetch(self, route, environ):
        self.assertEqual(environ, self.env)
        self.calls.append(route)
        if route == api.shared.BRANCH_ROUTE:
            return deepcopy(self.branch)
        self.assertEqual(route, api.shared.ARTIFACT_ROUTE + '97')
        return deepcopy(self.upload)

    def receipt(self):
        return api.receipt(SHA, '97', DIGEST, self.packet, self.source, self.output, self.env, self.fetch)

    def test_real_roundtrip_records_identity_without_claiming_completion_or_deployment(self):
        result = self.receipt()
        self.assertEqual(json.loads(self.output.read_bytes()), result)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result['kind'], 'commons-release-rehearsal')
        self.assertEqual(result['artifact'], {'id': 97, 'digest_sha256': DIGEST, 'metadata_verified': True})
        self.assertEqual(result['packet_sha256'], artifact.digest(self.packet.read_bytes()))
        self.assertEqual(result['descriptor'], json.loads(self.packet.read_bytes())['descriptor'])
        self.assertEqual(result['checks'], {'artifact_verified': True, 'rebuild_matched': True, 'transport_roundtrip': True})
        self.assertIs(result['deployment_authorized'], False)
        for pending in ['successful-github-run', 'required-github-checks', 'trusted-provenance-consumption',
                        'fresh-installed-schema', 'scoped-provider-access', 'durable-recovery']:
            self.assertIn(pending, result['pending_gates'])
        self.assertNotIn(MARKER, json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(self.calls, [api.shared.BRANCH_ROUTE, api.shared.ARTIFACT_ROUTE + '97'])

    def test_forks_other_workflows_and_unprotected_or_mismatched_contexts_fail_before_reads(self):
        for key, value in [('GITHUB_ACTIONS', 'false'), ('GITHUB_SERVER_URL', 'https://example.invalid'),
                           ('GITHUB_REPOSITORY', 'fork/website'), ('GITHUB_REPOSITORY_ID', '123'),
                           ('GITHUB_EVENT_NAME', 'pull_request_target'), ('GITHUB_REF', 'refs/heads/feature'),
                           ('GITHUB_REF_PROTECTED', 'false'), ('GITHUB_SHA', 'c' * 40),
                           ('GITHUB_WORKFLOW_SHA', 'd' * 40), ('GITHUB_WORKFLOW_REF', api.shared.WORKFLOW_REF),
                           ('GITHUB_RUN_ID', '0'), ('GITHUB_RUN_ATTEMPT', '02')]:
            with self.subTest(key=key):
                self.env = {**environment(), key: value}
                with self.assertRaises(ArtifactError): self.receipt()
                self.assertEqual(self.calls, [])
                self.assertFalse(self.output.exists())

    def test_current_main_must_still_match_before_recording_receipt(self):
        for value in [{'name': 'main', 'protected': False, 'commit': {'sha': SHA}},
                      {'name': 'main', 'protected': True, 'commit': {'sha': 'c' * 40}}]:
            self.branch = value
            self.calls.clear()
            with self.assertRaises(ArtifactError): self.receipt()
            self.assertEqual(self.calls, [api.shared.BRANCH_ROUTE])
            self.assertFalse(self.output.exists())

    def test_stale_expired_replaced_static_or_foreign_artifacts_are_rejected(self):
        variants = [('id', True), ('name', 'static-candidate-' + SHA + '-248-2'),
                    ('name', 'commons-candidate-' + SHA + '-248-1'), ('digest', 'sha256:' + 'c' * 64),
                    ('expired', True), ('expires_at', '2020-01-01T00:00:00Z'),
                    ('expires_at', '2030-99-01T00:00:00Z')]
        for key, value in variants:
            with self.subTest(key=key, value=value):
                self.upload = {**uploaded(), key: value}
                with self.assertRaises(ArtifactError): self.receipt()
                self.assertFalse(self.output.exists())
        for key, value in [('id', 249), ('repository_id', 123), ('head_repository_id', 123),
                           ('head_branch', 'feature'), ('head_sha', 'c' * 40)]:
            self.upload = uploaded()
            self.upload['workflow_run'][key] = value
            with self.assertRaises(ArtifactError): self.receipt()

    def test_changed_downloaded_code_or_schema_fails_before_authenticated_reads(self):
        original = self.packet.read_bytes()
        for mutate in ['code', 'schema']:
            value = json.loads(original)
            if mutate == 'code':
                files, _ = artifact.unpack(original, SHA, api.SCHEMA_SHA256)
                files['worker.mjs'] += b'\n// changed after upload\n'
                self.packet.write_bytes(artifact.packet(files, SHA, api.SCHEMA_SHA256))
            else:
                value['descriptor']['schema']['sha256'] = 'c' * 64
                self.packet.write_bytes(artifact.encode(value))
            with self.assertRaises(ArtifactError): self.receipt()
            self.assertEqual(self.calls, [])
            self.assertFalse(self.output.exists())

    def test_source_is_captured_again_and_existing_receipts_are_never_overwritten(self):
        original = (self.source / 'worker.mjs').read_bytes()
        (self.source / 'worker.mjs').write_bytes(original + b'\n// changed source\n')
        with self.assertRaisesRegex(ArtifactError, 'rebuild_mismatch'): self.receipt()
        self.assertEqual(self.calls, [])
        (self.source / 'worker.mjs').write_bytes(original)
        self.output.write_text('keep previous receipt')
        with self.assertRaises(OSError): self.receipt()
        self.assertEqual(self.output.read_text(), 'keep previous receipt')

    def test_receipt_cli_and_unexpected_failure_output_do_not_expose_private_inputs(self):
        args = ['receipt', '--checkout-commit', SHA, '--artifact-id', '97', '--artifact-digest', DIGEST,
                '--candidate', str(self.packet), '--source-dir', str(self.source), '--out', str(self.output)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(cli.cli(args, self.env, self.fetch), 0)
        self.assertEqual(stderr.getvalue(), '')
        self.assertFalse(json.loads(stdout.getvalue())['deployment_authorized'])
        for error in [RuntimeError(MARKER), ArtifactError(MARKER)]:
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(cli, 'main', side_effect=error), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(cli.cli(args, self.env, self.fetch), 1)
            self.assertEqual(stdout.getvalue(), '')
            self.assertEqual(json.loads(stderr.getvalue()), {'error': 'rehearsal_failed'})

    def test_shared_transport_rejects_unrelated_resources_and_provider_routes(self):
        for route in ['/user', '/zones/example/dns_records', '/repos/fork/website/branches/main',
                      '/repos/oss-singularity/website/actions/runs/248', api.shared.ARTIFACT_ROUTE + '../97']:
            with self.assertRaisesRegex(ArtifactError, 'invalid_api_route'):
                api.shared.github_get(route, self.env)


if __name__ == '__main__':
    unittest.main()
