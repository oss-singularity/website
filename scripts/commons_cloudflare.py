"""Real Cloudflare API adapter for Commons Worker operations.

Reads current state and executes version/deployment mutations against the
Cloudflare API. This module requires an account-wide API token with Workers
and D1 read permissions. It is designed as a private operator tool, NOT for
CI workflows — the token must be provided at call time, never hard-coded.

API reference: https://developers.cloudflare.com/api/
"""
import hashlib
import json
import re
import urllib.error
import urllib.request

from site_artifact import ArtifactError, require

BASE = 'https://api.cloudflare.com/client/v4'
MAX_RESPONSE = 4 * 1024 * 1024

# Known Workers script metadata fields from the real API.
SCRIPT_FIELDS = {'id', 'etag', 'created_on', 'modified_on', 'usage_model',
                 'has_modules', 'has_assets'}
VERSION_FIELDS = {'id', 'number', 'metadata', 'annotations'}
DEPLOYMENT_FIELDS = {'id', 'source', 'strategy', 'annotations', 'versions',
                     'created_on'}
ROUTE_FIELDS = {'id', 'pattern', 'script', 'request_limit_fail_open'}
SCHEDULE_FIELDS = {'cron', 'created_on', 'modified_on'}
SUBDOMAIN_FIELDS = {'name'}
D1_DATABASE_FIELDS = {'uuid', 'name', 'created_at', 'version', 'file_size'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class CloudflareAdapter:
    """Real Cloudflare API adapter for one specific Worker script and D1 database."""

    def __init__(self, token, account_id, zone_id, script_name, d1_uuid):
        self.token = token
        self.account_id = account_id
        self.zone_id = zone_id
        self.script_name = script_name
        self.d1_uuid = d1_uuid

    def _api(self, method, path, body=None):
        url = BASE + path
        data = encode(body).encode() if body is not None else None
        request = urllib.request.Request(url, method=method, data=data, headers={
            'Authorization': 'Bearer ' + self.token,
            'Content-Type': 'application/json',
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(MAX_RESPONSE + 1)
                require(len(raw) <= MAX_RESPONSE, 'response_limit')
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            detail = error.read(MAX_RESPONSE + 1).decode('utf-8', 'replace')
            raise ArtifactError('provider_request_failed') from None
        except Exception:
            raise ArtifactError('provider_request_failed') from None

    def observe(self):
        """Read the complete current Worker state: version, deployment, routes,
        schedules, subdomain, and D1 schema fingerprint."""
        account = '/accounts/' + self.account_id
        script_path = account + '/workers/scripts/' + self.script_name
        zone_path = '/zones/' + self.zone_id

        # Active deployment (most recent)
        deps = self._api('GET', script_path + '/deployments')
        deployments = deps.get('result', {}).get('deployments', [])
        require(type(deployments) is list, 'invalid_deployments')
        active_deployment = deployments[0] if deployments else None

        # Active version ID from the most recent deployment
        active_version_id = None
        if active_deployment:
            versions = active_deployment.get('versions', [])
            if versions:
                active_version_id = versions[0].get('version_id')

        # All versions (for the version list and predecessor)
        all_versions = self._api('GET', script_path + '/versions')
        version_items = all_versions.get('result', {}).get('items', [])
        require(type(version_items) is list, 'invalid_versions')

        # Routes (may require zone-level Workers permissions)
        routes = []
        try:
            routes_resp = self._api('GET', zone_path + '/workers/routes')
            routes = routes_resp.get('result', [])
            require(type(routes) is list, 'invalid_routes')
        except ArtifactError:
            pass  # Token may not have workers_routes:read on this zone

        # Schedules (cron triggers) — may require specific permissions
        schedules = []
        try:
            schedules_resp = self._api('GET', script_path + '/schedules')
            schedules_data = schedules_resp.get('result', {})
            schedules = schedules_data.get('schedules', []) if isinstance(schedules_data, dict) else []
            require(type(schedules) is list, 'invalid_schedules')
        except ArtifactError:
            pass

        # Subdomain
        subdomain_resp = self._api('GET', account + '/workers/subdomain')
        subdomain = subdomain_resp.get('result', {})

        # D1 schema fingerprint
        d1_resp = self._api('GET', account + '/d1/database/' + self.d1_uuid)
        d1 = d1_resp.get('result', {})
        schema_fingerprint = d1.get('version', '')

        return {
            'schema_version': 1,
            'target': self.script_name,
            'account_id': self.account_id,
            'zone_id': self.zone_id,
            'script_name': self.script_name,
            'active_version': active_version_id,
            'latest_version_id': version_items[0]['id'] if version_items else None,
            'versions': {v['id']: {
                'number': v['number'],
                'metadata': v.get('metadata', {}),
                'annotations': v.get('annotations', {}),
            } for v in version_items},
            'deployments': [{
                'id': d['id'],
                'strategy': d.get('strategy'),
                'versions': [{
                    'version_id': v['version_id'],
                    'percentage': v['percentage'],
                } for v in d.get('versions', [])],
            } for d in deployments],
            'routes': [{
                'id': r.get('id'),
                'pattern': r.get('pattern'),
                'script': r.get('script'),
            } for r in routes],
            'schedules': [{
                'cron': s.get('cron'),
                'created_on': s.get('created_on'),
            } for s in schedules],
            'subdomain': subdomain.get('name'),
            'd1_schema_fingerprint': schema_fingerprint,
        }

    def stage_version(self, candidate_content, candidate_commit, message, tag):
        """Upload new Worker code as a new version. Does NOT change traffic.

        Returns the new version ID. The version is immutable after creation.
        """
        script_path = '/accounts/' + self.account_id + '/workers/scripts/' + self.script_name

        # Upload the worker content (multipart form data with module upload)
        # The Worker API expects multipart/form-data for module uploads
        boundary = '----CfWorkerUpload' + hashlib.sha256(candidate_content).hexdigest()[:16]
        body_parts = []

        # Parse the multipart candidate content (same format as GET response)
        # The candidate_content is already in multipart format from the artifact
        if b'Content-Disposition: form-data' in candidate_content:
            # Already multipart — use as-is with new annotations
            raw_body = candidate_content
        else:
            # Single module — wrap in multipart
            body_parts.append('--' + boundary)
            body_parts.append('Content-Disposition: form-data; name="worker.mjs"')
            body_parts.append('')
            body_parts.append(candidate_content.decode('utf-8', 'replace'))
            body_parts.append('--' + boundary + '--')
            raw_body = '\r\n'.join(body_parts).encode('utf-8')

        # Upload with metadata annotations
        url = BASE + script_path + '/versions'
        req = urllib.request.Request(url, method='POST', data=raw_body, headers={
            'Authorization': 'Bearer ' + self.token,
            'Content-Type': 'multipart/form-data; boundary=' + boundary,
            'CF-WORKER-METADATA': encode({
                'main_module': 'worker.mjs',
                'bindings': [],
            }),
            'CF-WORKER-ANNOTATIONS': encode({
                'workers/message': message,
                'workers/tag': tag,
                'workers/triggered_by': 'upload',
            }),
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read(65536))
        except urllib.error.HTTPError as error:
            raise ArtifactError('provider_request_failed') from None
        except Exception:
            raise ArtifactError('provider_request_failed') from None

        require(result.get('success') is True, 'provider_request_failed')
        version = result.get('result', {})
        new_id = version.get('id')
        require(type(new_id) is str and len(new_id) > 0, 'provider_request_failed')
        return new_id

    def activate_version(self, version_id, message):
        """Deploy a specific version at 100% traffic."""
        script_path = '/accounts/' + self.account_id + '/workers/scripts/' + self.script_name

        result = self._api('POST', script_path + '/deployments', {
            'strategy': 'percentage',
            'versions': [{'version_id': version_id, 'percentage': 100}],
            'annotations': {
                'workers/message': message,
                'workers/triggered_by': 'upload',
            },
        })
        require(result.get('success') is True, 'provider_request_failed')
        deployment = result.get('result', {})
        dep_id = deployment.get('id')
        require(type(dep_id) is str and len(dep_id) > 0, 'provider_request_failed')
        return dep_id

    def restore_predecessor(self, original_version_id, message):
        """Roll back to a specific previous version at 100% traffic."""
        return self.activate_version(original_version_id, message + ' (rollback)')
