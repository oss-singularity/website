"""Offline tests for the real Cloudflare adapter — no live API calls."""
import json
import unittest
from unittest.mock import patch, MagicMock

import commons_cloudflare as adapter
from site_artifact import ArtifactError


SAMPLE_VERSIONS = {
    'result': {
        'items': [
            {
                'id': 'v3-uuid', 'number': 3,
                'metadata': {'created_on': '2026-09-05T18:03:41Z', 'source': 'api',
                             'author_id': 'abc', 'author_email': '', 'has_preview': True},
                'annotations': {'workers/message': 'Upgrade v3',
                                'workers/tag': 'commons-v3', 'workers/triggered_by': 'upload'},
            },
            {
                'id': 'v2-uuid', 'number': 2,
                'metadata': {'created_on': '2026-09-05T10:09:52Z', 'source': 'api',
                             'author_id': 'abc', 'author_email': '', 'has_preview': True},
                'annotations': {'workers/message': 'Upgrade v2',
                                'workers/tag': 'commons-v2', 'workers/triggered_by': 'upload'},
            },
        ]
    }
}

SAMPLE_DEPLOYMENTS = {
    'result': {
        'deployments': [
            {
                'id': 'dep-v3-uuid', 'source': 'api', 'strategy': 'percentage',
                'annotations': {'workers/message': 'Upgrade v3', 'workers/triggered_by': 'upload'},
                'versions': [{'version_id': 'v3-uuid', 'percentage': 100}],
                'created_on': '2026-09-05T18:03:41Z',
            },
            {
                'id': 'dep-v2-uuid', 'source': 'api', 'strategy': 'percentage',
                'annotations': {'workers/message': 'Upgrade v2', 'workers/triggered_by': 'upload'},
                'versions': [{'version_id': 'v2-uuid', 'percentage': 100}],
                'created_on': '2026-09-05T10:09:52Z',
            },
        ]
    }
}

SAMPLE_ROUTES = {
    'result': [
        {'id': 'route-1', 'pattern': 'oss-singularity.io/api/*', 'script': 'oss-singularity-commons',
         'request_limit_fail_open': False},
    ]
}

SAMPLE_SCHEDULES = {
    'result': {
        'schedules': [
            {'cron': '*/5 * * * *', 'created_on': '2026-09-05T18:03:41Z', 'modified_on': '2026-09-05T18:03:41Z'},
        ]
    }
}

SAMPLE_SUBDOMAIN = {
    'result': {'name': 'oss-singularity-commons'}
}

SAMPLE_D1 = {
    'result': {
        'uuid': '28795e91-189f-4db0-8c49-06925baca919',
        'name': 'oss-singularity-commons',
        'created_at': '2026-09-05T10:09:52Z',
        'version': 'd1-schema-v1',
        'file_size': 8192,
    }
}


def _make_adapter():
    return adapter.CloudflareAdapter(
        token='test-token',
        account_id='test-account',
        zone_id='test-zone',
        script_name='test-script',
        d1_uuid='test-d1-uuid',
    )


class CloudflareAdapterTests(unittest.TestCase):
    def test_observe_reads_complete_state(self):
        a = _make_adapter()

        def fake_api(method, path, body=None):
            if 'deployments' in path:
                return SAMPLE_DEPLOYMENTS
            if 'versions' in path:
                return SAMPLE_VERSIONS
            if 'routes' in path:
                return SAMPLE_ROUTES
            if 'schedules' in path:
                return SAMPLE_SCHEDULES
            if 'subdomain' in path:
                return SAMPLE_SUBDOMAIN
            if 'd1/database' in path:
                return SAMPLE_D1
            raise AssertionError(f'Unexpected path: {path}')

        with patch.object(a, '_api', side_effect=fake_api):
            obs = a.observe()

        self.assertEqual(obs['active_version'], 'v3-uuid')
        self.assertEqual(obs['latest_version_id'], 'v3-uuid')
        self.assertEqual(len(obs['versions']), 2)
        self.assertEqual(len(obs['deployments']), 2)
        self.assertEqual(len(obs['routes']), 1)
        self.assertEqual(len(obs['schedules']), 1)
        self.assertEqual(obs['subdomain'], 'oss-singularity-commons')
        self.assertEqual(obs['d1_schema_fingerprint'], 'd1-schema-v1')

    def test_observe_handles_empty_deployments(self):
        a = _make_adapter()
        empty_deployments = {'result': {'deployments': []}}

        def fake_api(method, path, body=None):
            if 'deployments' in path:
                return empty_deployments
            if 'versions' in path:
                return SAMPLE_VERSIONS
            if 'routes' in path:
                return SAMPLE_ROUTES
            if 'schedules' in path:
                return SAMPLE_SCHEDULES
            if 'subdomain' in path:
                return SAMPLE_SUBDOMAIN
            if 'd1/database' in path:
                return SAMPLE_D1
            raise AssertionError(f'Unexpected path: {path}')

        with patch.object(a, '_api', side_effect=fake_api):
            obs = a.observe()

        self.assertIsNone(obs['active_version'])
        self.assertEqual(len(obs['deployments']), 0)

    def test_stage_version_wraps_single_module_and_returns_new_id(self):
        a = _make_adapter()
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, _limit=-1):
                return json.dumps({'success': True, 'result': {'id': 'new-version-uuid'}}).encode()

        def fake_urlopen(request, timeout=None):
            captured['url'] = request.full_url
            captured['method'] = request.get_method()
            captured['headers'] = dict(request.header_items())
            captured['body'] = request.data
            return FakeResponse()

        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            new_id = a.stage_version(b'export default {}', 'abc123', 'Test message', 'test-tag')

        self.assertEqual(new_id, 'new-version-uuid')
        self.assertTrue(captured['url'].endswith('/workers/scripts/test-script/versions'))
        self.assertEqual(captured['method'], 'POST')
        self.assertIn(b'Content-Disposition: form-data; name="worker.mjs"', captured['body'])
        # The declared boundary must be the exact boundary used by the body.
        boundary = captured['headers']['Content-type'].split('boundary=')[1]
        self.assertIn(('--' + boundary + '\r\n').encode(), captured['body'])
        self.assertTrue(captured['body'].endswith(('--' + boundary + '--').encode()))

    def test_stage_version_reuses_the_candidates_own_multipart_boundary(self):
        a = _make_adapter()
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, _limit=-1):
                return json.dumps({'success': True, 'result': {'id': 'new-version-uuid'}}).encode()

        def fake_urlopen(request, timeout=None):
            captured['headers'] = dict(request.header_items())
            captured['body'] = request.data
            return FakeResponse()

        multipart = (b'--cf-existing-boundary\r\n'
                     b'Content-Disposition: form-data; name="worker.mjs"\r\n\r\n'
                     b'code\r\n--cf-existing-boundary--')
        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            new_id = a.stage_version(multipart, 'abc123', 'Test message', 'test-tag')

        self.assertEqual(new_id, 'new-version-uuid')
        self.assertEqual(captured['body'], multipart)
        self.assertIn('boundary=cf-existing-boundary', captured['headers']['Content-type'])

    def test_activate_version_returns_deployment_id(self):
        a = _make_adapter()

        with patch.object(a, '_api') as mock_api:
            mock_api.return_value = {'success': True, 'result': {'id': 'new-deployment-uuid'}}
            dep_id = a.activate_version('v3-uuid', 'Test activation')
            self.assertEqual(dep_id, 'new-deployment-uuid')

    def test_activate_version_fails_on_api_error(self):
        a = _make_adapter()

        with patch.object(a, '_api') as mock_api:
            mock_api.return_value = {'success': False, 'errors': [{'code': 10000, 'message': 'fail'}]}
            with self.assertRaises(ArtifactError):
                a.activate_version('v3-uuid', 'Test')

    def test_restore_predecessor_calls_activate(self):
        a = _make_adapter()

        with patch.object(a, '_api') as mock_api:
            mock_api.return_value = {'success': True, 'result': {'id': 'rollback-dep-uuid'}}
            dep_id = a.restore_predecessor('v2-uuid', 'Rollback test')
            self.assertEqual(dep_id, 'rollback-dep-uuid')

    def test_observe_fingerprints_d1_schema(self):
        a = _make_adapter()
        d1_with_fingerprint = {
            'result': {
                **SAMPLE_D1['result'],
                'version': 'schema-fp-abc123',
            }
        }

        def fake_api(method, path, body=None):
            if 'deployments' in path:
                return SAMPLE_DEPLOYMENTS
            if 'versions' in path:
                return SAMPLE_VERSIONS
            if 'routes' in path:
                return SAMPLE_ROUTES
            if 'schedules' in path:
                return SAMPLE_SCHEDULES
            if 'subdomain' in path:
                return SAMPLE_SUBDOMAIN
            if 'd1/database' in path:
                return d1_with_fingerprint
            raise AssertionError(f'Unexpected path: {path}')

        with patch.object(a, '_api', side_effect=fake_api):
            obs = a.observe()

        self.assertEqual(obs['d1_schema_fingerprint'], 'schema-fp-abc123')

    def test_stage_version_preserves_multipart_candidate(self):
        a = _make_adapter()
        multipart = b'--boundary\r\nContent-Disposition: form-data; name="worker.mjs"\r\n\r\ncode\r\n--boundary--'

        # Mock the entire urllib flow
        with patch('urllib.request.urlopen') as mock_urlopen, \
             patch('urllib.request.Request') as mock_request:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps({
                'success': True,
                'result': {'id': 'new-version-uuid'}
            }).encode()
            mock_urlopen.return_value.__enter__.return_value = mock_response

            result = a.stage_version(multipart, 'abc123', 'Test message', 'test-tag')
            self.assertEqual(result, 'new-version-uuid')

    def test_stage_version_fails_on_api_error(self):
        a = _make_adapter()
        content = b'simple code'

        with patch('urllib.request.urlopen') as mock_urlopen:
            import urllib.error
            mock_urlopen.return_value.__enter__.side_effect = urllib.error.HTTPError(
                'url', 500, 'Error', {}, None)

            with self.assertRaises(ArtifactError):
                a.stage_version(content, 'abc123', 'Test', 'tag')


if __name__ == '__main__':
    unittest.main()
