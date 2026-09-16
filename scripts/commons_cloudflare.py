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
                'request_limit_fail_open': r.get('request_limit_fail_open'),
            } for r in routes],
            'schedules': [{
                'cron': s.get('cron'),
                'created_on': s.get('created_on'),
            } for s in schedules],
            'subdomain': subdomain.get('name'),
            'd1_schema_fingerprint': schema_fingerprint,
        }

    def stage_version(self, candidate_content, candidate_commit, message, tag,
                      bindings=None, main_module='worker.mjs', compatibility_date=None):
        """Upload new Worker code as a new version. Does NOT change traffic.

        The Workers version API takes multipart/form-data with a JSON "metadata"
        form part plus one part per module; a CF-WORKER-METADATA header is
        refused. Binding names passed as {"name": ..., "type": "inherit"} reuse
        the installed values, including secrets and D1, which cannot be
        re-uploaded through this endpoint. An already-multipart candidate is
        split into its exact module parts; a single module is wrapped.
        Returns the new version ID. The version is immutable after creation.
        """
        script_path = '/accounts/' + self.account_id + '/workers/scripts/' + self.script_name
        boundary = '----CfWorkerUpload' + hashlib.sha256(candidate_content).hexdigest()[:16]
        metadata = {'main_module': main_module, 'bindings': bindings or []}
        if compatibility_date is not None:
            metadata['compatibility_date'] = compatibility_date
        if message is not None or tag is not None:
            # Annotations ride in the metadata part; the provider rejects
            # workers/triggered_by there and reserves it for its own records.
            metadata['annotations'] = {}
            if message is not None:
                metadata['annotations']['workers/message'] = message
            if tag is not None:
                metadata['annotations']['workers/tag'] = tag

        def form_part(headers, payload):
            return ('--' + boundary + '\r\n' + headers + '\r\n\r\n').encode() + payload + b'\r\n'

        raw_body = form_part('Content-Disposition: form-data; name="metadata"\r\nContent-Type: application/json',
                             encode(metadata).encode())
        if candidate_content.startswith(b'--'):
            outer = candidate_content.split(b'\r\n', 1)[0][2:]
            require(re.fullmatch(r'[A-Za-z0-9()+_,.=:-]{1,128}', outer.decode('ascii', 'replace')),
                    'invalid_candidate')
            for part in candidate_content.split(b'--' + outer)[1:-1]:
                head, _, module = part.partition(b'\r\n\r\n')
                name = re.search(r'name="([^"]+)"', head.decode('utf-8', 'replace'))
                require(name is not None, 'invalid_candidate')
                require(module.endswith(b'\r\n'), 'invalid_candidate')
                raw_body += form_part('Content-Disposition: form-data; name="' + name[1]
                                      + '"; filename="' + name[1] + '"\r\nContent-Type: application/javascript+module',
                                      module[:-2])
        else:
            raw_body += form_part('Content-Disposition: form-data; name="' + main_module
                                  + '"; filename="' + main_module + '"\r\nContent-Type: application/javascript+module',
                                  candidate_content)
        raw_body += b'--' + boundary.encode() + b'--'

        url = BASE + script_path + '/versions'
        req = urllib.request.Request(url, method='POST', data=raw_body, headers={
            'Authorization': 'Bearer ' + self.token,
            'Content-Type': 'multipart/form-data; boundary=' + boundary,
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read(65536))
        except urllib.error.HTTPError as error:
            error.read(4096)
            raise ArtifactError('provider_request_failed') from None
        except Exception:
            raise ArtifactError('provider_request_failed') from None

        require(result.get('success') is True, 'provider_request_failed')
        version = result.get('result', {})
        new_id = version.get('id')
        require(type(new_id) is str and len(new_id) > 0, 'provider_request_failed')
        return new_id

    def script_content(self):
        """Read the deployed script as the provider's own multipart form."""
        url = BASE + '/accounts/' + self.account_id + '/workers/scripts/' + self.script_name
        request = urllib.request.Request(url, method='GET',
                                         headers={'Authorization': 'Bearer ' + self.token})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
        except ArtifactError:
            raise
        except Exception:
            raise ArtifactError('provider_request_failed') from None
        require(0 < len(raw) <= 8 * 1024 * 1024, 'invalid_candidate')
        return raw

    def script_settings(self):
        """Read the installed script settings.

        The provider omits the observability key entirely while observability
        is disabled; the raw result is returned for bounded normalization by
        the caller.
        """
        return self._api('GET', '/accounts/' + self.account_id + '/workers/scripts/'
                         + self.script_name + '/settings').get('result', {})

    def script_subdomain(self):
        """Read this script's workers.dev exposure ({'enabled', 'previews_enabled'})."""
        return self._api('GET', '/accounts/' + self.account_id + '/workers/scripts/'
                         + self.script_name + '/subdomain').get('result', {})

    def schema_rows(self, query):
        """Run one fixed read-only inventory SELECT against the D1 database.

        The query text is pinned by the caller (the artifact module's
        SCHEMA_QUERY); this method only transports it. Exactly one result set
        with a bounded, non-empty row list is returned.
        """
        require(type(query) is str and 0 < len(query) <= 1024, 'invalid_schema')
        result = self._api('POST', '/accounts/' + self.account_id + '/d1/database/'
                           + self.d1_uuid + '/query', {'sql': query})
        require(result.get('success') is True, 'provider_request_failed')
        sets = result.get('result')
        require(type(sets) is list and len(sets) == 1 and type(sets[0]) is dict
                and sets[0].get('success') is True, 'provider_request_failed')
        rows = sets[0].get('results')
        require(type(rows) is list and 0 < len(rows) <= 256, 'provider_request_failed')
        return sets

    def version_detail(self, version_id):
        """Read one immutable version's server-side detail for staged verification."""
        return self._api('GET', '/accounts/' + self.account_id + '/workers/scripts/'
                         + self.script_name + '/versions/' + version_id).get('result', {})

    def activate_version(self, version_id, message):
        """Deploy a specific version at 100% traffic.

        Deployment annotations accept only workers/message; the version-upload
        annotation workers/triggered_by is refused here with error 10210.
        """
        script_path = '/accounts/' + self.account_id + '/workers/scripts/' + self.script_name

        result = self._api('POST', script_path + '/deployments', {
            'strategy': 'percentage',
            'versions': [{'version_id': version_id, 'percentage': 100}],
            'annotations': {
                'workers/message': message,
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
