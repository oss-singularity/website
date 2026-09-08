"""Fixed-zone Cloudflare observations and the sole permitted cache mutation."""
import re
import urllib.request

from release_source import checks, digest, encode, token
from site_artifact import ArtifactError, require

HOST = 'oss-singularity.io'
ZONE = '1c3baf2f37c27885367ae4c85a5a4ae8'
BASE = '/zones/' + ZONE
API = 'https://api.cloudflare.com/client/v4'
READS = {'', '/settings', '/dns_records?per_page=100&page=1',
         '/rulesets/phases/http_request_cache_settings/entrypoint',
         '/rulesets/phases/http_response_cache_settings/entrypoint',
         '/security-center/securitytxt'}


def security_text(value):
    labels = {'acknowledgments': 'Acknowledgments', 'canonical': 'Canonical', 'contact': 'Contact',
              'encryption': 'Encryption', 'expires': 'Expires', 'hiring': 'Hiring', 'policy': 'Policy',
              'preferred_languages': 'Preferred-Languages'}
    require(type(value) is dict and value.get('enabled') is True
            and set(value) <= set(labels) | {'enabled'}
            and value.get('canonical') == ['https://' + HOST + '/.well-known/security.txt']
            and value.get('contact') and value.get('expires'), 'security_text_mismatch')
    lines = []
    for key, label in sorted(labels.items()):
        item = value.get(key)
        if item is None:
            continue
        if key in {'expires', 'preferred_languages'}:
            require(type(item) is str, 'security_text_mismatch')
            items = [item]
        else:
            require(type(item) is list and len(item) <= 32, 'security_text_mismatch')
            items = item
        for text in items:
            require(type(text) is str and 0 < len(text) <= 4096
                    and not re.search(r'[\x00-\x1f\x7f]', text), 'security_text_mismatch')
            lines.append(label + ': ' + text)
    return ('\n'.join(lines) + '\n').encode()


class Cloudflare:
    def __init__(self, environ, opener=None):
        self.environ = environ
        self.opener = opener or urllib.request.build_opener(checks.rehearsal.NoRedirect())

    def request(self, suffix, method='GET'):
        require((method == 'GET' and suffix in READS) or (method == 'POST' and suffix == '/purge_cache'),
                'invalid_provider_route')
        body = encode({'purge_everything': True}) if method == 'POST' else None
        url = API + BASE + suffix
        request = urllib.request.Request(url, method=method, data=body, headers={
            'Authorization': 'Bearer ' + token(self.environ, 'CF_RELEASE_TOKEN'),
            'Content-Type': 'application/json', 'Cache-Control': 'no-cache'})
        try:
            with self.opener.open(request, timeout=20) as response:
                require(response.status == 200 and response.geturl() == url, 'provider_request_failed')
                raw = response.read(checks.MAX_JSON + 1)
            value = checks.decode(raw)
            require(type(value) is dict and value.get('success') is True and not value.get('errors'),
                    'provider_request_failed')
            return value
        except ArtifactError:
            raise
        except Exception:
            raise ArtifactError('provider_request_failed') from None

    def observe(self, expected_security):
        snapshots = {suffix: self.request(suffix) for suffix in sorted(READS)}
        result = {suffix: value.get('result') for suffix, value in snapshots.items()}
        zone = result['']
        require(type(zone) is dict and zone.get('id') == ZONE and zone.get('name') == HOST
                and zone.get('status') == 'active', 'zone_mismatch')
        require(type(zone.get('development_mode')) is int and zone['development_mode'] <= 0,
                'edge_policy_mismatch')
        settings = result['/settings']
        require(type(settings) is list and all(type(item) is dict for item in settings), 'edge_policy_mismatch')
        values = {item.get('id'): item.get('value') for item in settings}
        for name, expected in {'ssl': 'strict', 'http3': 'on', 'brotli': 'on',
                               'tls_1_3': 'on', 'min_tls_version': '1.2', 'development_mode': 'off'}.items():
            require(values.get(name) == expected, 'edge_policy_mismatch')
        records = result['/dns_records?per_page=100&page=1']
        info = snapshots['/dns_records?per_page=100&page=1'].get('result_info')
        require(type(records) is list and 0 < len(records) < 100 and type(info) is dict
                and info.get('total_count') == len(records) and info.get('total_pages') == 1,
                'dns_inventory_incomplete')
        proxied = {record.get('name') for record in records if record.get('proxied') is True}
        require(proxied == {HOST, 'www.' + HOST}, 'dns_boundary_mismatch')
        service_names = {name + '.' + HOST for name in
                         ['mail', 'ftp', 'cpanel', 'cpcalendars', 'cpcontacts', 'webdisk', 'webmail', 'whm']}
        services = [record for record in records if record.get('name') in service_names]
        require({record.get('name') for record in services} == service_names
                and all(record.get('proxied') is False for record in services), 'dns_boundary_mismatch')
        require(any(record.get('type') == 'MX' for record in records), 'dns_boundary_mismatch')
        request_rules = result['/rulesets/phases/http_request_cache_settings/entrypoint']
        response_rules = result['/rulesets/phases/http_response_cache_settings/entrypoint']
        require(type(request_rules) is dict and type(response_rules) is dict, 'edge_policy_mismatch')
        cache_rules = [rule for rule in request_rules.get('rules', [])
                       if rule.get('ref') == 'oss_static_edge_only_cache' and rule.get('enabled') is True]
        require(len(cache_rules) == 1, 'edge_policy_mismatch')
        require(cache_rules[0].get('action') == 'set_cache_settings' and cache_rules[0].get('expression') ==
                '(http.host eq "oss-singularity.io") or (http.host eq "www.oss-singularity.io")', 'edge_policy_mismatch')
        params = cache_rules[0].get('action_parameters', {})
        require(params.get('cache') is True and params.get('edge_ttl', {}).get('mode') == 'override_origin'
                and params.get('edge_ttl', {}).get('default') == 7200
                and params.get('browser_ttl', {}).get('mode') == 'bypass', 'edge_policy_mismatch')
        bypass = [rule for rule in request_rules.get('rules', []) if rule.get('ref') == 'oss_commons_api_bypass']
        require(len(bypass) == 1 and bypass[0].get('enabled') is True
                and bypass[0].get('action') == 'set_cache_settings' and bypass[0].get('action_parameters') == {'cache': False}
                and bypass[0].get('expression') ==
                '(http.host eq "oss-singularity.io" and starts_with(http.request.uri.path, "/api/"))'
                and request_rules['rules'].index(bypass[0]) > request_rules['rules'].index(cache_rules[0]),
                'edge_policy_mismatch')
        guard = [rule for rule in response_rules.get('rules', [])
                 if rule.get('ref') == 'upstream_no_cache_response_guard' and rule.get('enabled') is True]
        require(len(guard) == 1 and guard[0].get('expression') ==
                'any(http.response.headers["cf-edge-cache"][*] == "no-cache")'
                and guard[0].get('action') == 'set_cache_control'
                and guard[0].get('action_parameters') ==
                {'no-store': {'cloudflare_only': True, 'operation': 'set'}}, 'edge_policy_mismatch')
        require(security_text(result['/security-center/securitytxt']) == expected_security, 'security_text_mismatch')
        # Keep the complete current DNS/provider snapshot private. Order is
        # significant for rules; DNS and settings lists are sets keyed by ID.
        result['/dns_records?per_page=100&page=1'] = sorted(records, key=lambda item: item['id'])
        result['/settings'] = sorted(settings, key=lambda item: item['id'])
        # Cloudflare returns seconds since Development Mode expired. Normalize
        # only this moving timer after requiring that caching is enabled. Keep
        # modification timestamps and every other configuration field intact.
        result[''] = {**zone, 'development_mode': 0}
        for setting in result['/settings']:
            if setting['id'] == 'development_mode' and 'time_remaining' in setting:
                remaining = setting['time_remaining']
                require(remaining is False or (type(remaining) is int and remaining <= 0),
                        'edge_policy_mismatch')
                setting['time_remaining'] = 0
        return {'configuration_sha256': digest(encode(result)),
                'dns_sha256': digest(encode(result['/dns_records?per_page=100&page=1'])),
                'security_sha256': digest(expected_security), 'zone': HOST, 'strict_tls': True}

    def purge(self):
        result = self.request('/purge_cache', 'POST').get('result')
        require(type(result) is dict and result.get('id') == ZONE, 'cache_purge_unconfirmed')
