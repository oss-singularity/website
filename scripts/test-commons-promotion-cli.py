"""Offline tests for the promotion command's derivations and output contract."""
import importlib.util
import json
import unittest

from site_artifact import ArtifactError

spec = importlib.util.spec_from_file_location('cpc', 'commons-promotion.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def multipart(*names, bad=False):
    boundary = '----B'
    parts = ''.join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{n}"; filename="{n}"'
        f'\r\nContent-Type: application/javascript+module\r\n\r\nbody-{n}\r\n'
        for n in names)
    return parts.encode() + f'--{boundary}--'.encode()


class PacketTests(unittest.TestCase):
    def test_packet_modules_lists_candidate_modules_without_metadata(self):
        content = multipart('worker.mjs', 'util.mjs')
        self.assertEqual(cli.packet_modules(content), ['worker.mjs', 'util.mjs'])

    def test_packet_modules_rejects_damaged_packets(self):
        for broken in [b'', b'not-multipart', b'--B\r\nnothing', multipart('a.mjs', 'a.mjs')]:
            with self.assertRaisesRegex(ArtifactError, 'invalid_candidate'):
                cli.packet_modules(broken)


class DeriveTests(unittest.TestCase):
    observation = {'active_version': 'v-live', 'versions': {}}
    detail = {'resources': {
        'bindings': [
            {'name': 'DB', 'type': 'd1', 'id': 'd1-uuid'},
            {'name': 'ADMIN_TOKEN', 'type': 'secret_text'},
            {'name': 'RELEASE_SHA', 'type': 'plain_text', 'text': '1' * 40},
        ],
        'script': {'modules': [{'name': 'worker.mjs'}]},
        'script_runtime': {'compatibility_date': '2026-09-04'},
    }}

    def test_derive_plan_inherits_bindings_and_reads_current_identity(self):
        plan = cli.derive_plan(self.observation, self.detail, 'message', 'tag')
        self.assertEqual(plan['predecessor_version'], 'v-live')
        self.assertEqual(plan['release_sha'], '1' * 40)
        self.assertEqual(plan['bindings'], [
            {'name': 'DB', 'type': 'inherit'}, {'name': 'ADMIN_TOKEN', 'type': 'inherit'},
            {'name': 'RELEASE_SHA', 'type': 'inherit'}])
        self.assertEqual(plan['installed_bindings'], self.detail['resources']['bindings'])
        self.assertEqual(plan['compatibility_date'], '2026-09-04')

    def test_derive_plan_refuses_unreadable_provider_state(self):
        for detail in [{}, {'resources': {}},
                       {'resources': {'bindings': [], 'script_runtime': {}}}]:
            with self.assertRaises(ArtifactError):
                cli.derive_plan(self.observation, detail, 'm', 't')


class OutputTests(unittest.TestCase):
    def test_missing_environment_reports_missing_credential_without_secret(self):
        output = __import__('io').StringIO()
        from contextlib import redirect_stdout
        secret = 'super-op-secret-token-value'
        with redirect_stdout(output):
            code = cli.cli(['--packet', 'x', '--commit', 'a' * 40, '--message', 'm',
                            '--tag', 't', ], environ={'COMMONS_CF_TOKEN': secret})
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['error'], 'missing_credential')
        self.assertNotIn(secret, output.getvalue())


if __name__ == '__main__':
    unittest.main()
