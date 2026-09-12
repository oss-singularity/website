"""Bounded public/origin GET verification for the fixed OSS static destination."""
from concurrent.futures import ThreadPoolExecutor
import ipaddress
from pathlib import Path, PurePosixPath
import re
import secrets
import socket
import ssl
import subprocess
import tempfile
import urllib.parse

from release_source import checks, digest
from release_edge import HOST
import static_plan as plan
import site_artifact as tree
from site_artifact import ArtifactError, require

WWW = 'www.' + HOST
MAX_BODY = 4 * 1024 * 1024
# Only this previously deployed block may use the historical redirect contract.
# New candidates must preserve encoded paths, including after a failed rollout.
LEGACY_ACCESS_SHA256 = '311c2a2d372f048622612d734a27bcac082a3ca45b4b9fe91ccd88fe45a6b77f'
REDIRECT_QUERY = '?oss_redirect_probe=1&literal=a%2Fb&plus=a+b&escaped=a%26b%3Dc&empty=&repeat=1&repeat=2'
REDIRECT_PATHS = ('/guide/' + REDIRECT_QUERY, '/guide/',
                  *(f'/oss-redirect-check/{segment}' + REDIRECT_QUERY for segment in
                    ('a%20b', 'a%23b', 'a%3Fb', 'a%2520b', 'a%2Fb', 'a%2fb', 'a%C3%A4b')))
MIMES = {'.html': {'text/html'}, '.css': {'text/css'}, '.js': {'text/javascript', 'application/javascript'},
         '.json': {'application/json'}, '.txt': {'text/plain'}, '.sha256': {'text/plain', 'application/octet-stream'},
         '.xml': {'application/xml', 'text/xml'}, '.svg': {'image/svg+xml'}, '.webp': {'image/webp'},
         '.png': {'image/png'}, '.ico': {'image/x-icon', 'image/vnd.microsoft.icon'},
         '.webmanifest': {'application/manifest+json', 'application/json'}}


def headers(raw):
    require(type(raw) is bytes and len(raw) <= 128 * 1024, 'invalid_http_headers')
    blocks = [part for part in re.split(rb'\r?\n\r?\n', raw) if part.startswith(b'HTTP/')]
    require(bool(blocks), 'invalid_http_headers')
    values = {}
    for line in blocks[-1].decode('iso-8859-1').splitlines()[1:]:
        require(':' in line and not line.startswith((' ', '\t')), 'invalid_http_headers')
        name, value = line.split(':', 1)
        require(re.fullmatch('[A-Za-z0-9-]+', name), 'invalid_http_headers')
        values.setdefault(name.lower(), []).append(value.strip())
    return {name: ', '.join(value) for name, value in values.items()}


def security_headers(files):
    require(type(files) is dict and type(files.get('.htaccess')) is bytes, 'invalid_http_contract')
    values = {}
    for line in files['.htaccess'].decode().splitlines():
        match = re.fullmatch(r'\s*Header always set ([A-Za-z-]+) "([^"]+)"\s*', line)
        if match:
            require(match[1].lower() not in values, 'invalid_http_contract')
            values[match[1].lower()] = match[2]
    require({'content-security-policy', 'strict-transport-security', 'x-content-type-options',
             'x-frame-options', 'referrer-policy'} <= set(values), 'invalid_http_contract')
    return values


def exact(response, raw, name, surface, contract, security_digest, statuses=(200,)):
    require(response['status'] in statuses and response['body'] == raw, 'http_bytes_mismatch')
    require(response['tls_verified'] is True, 'tls_unverified')
    values = response['headers']
    require(values.get('content-type', '').split(';')[0].strip().lower() in
            MIMES.get(PurePosixPath(name).suffix, set()), 'http_mime_mismatch')
    require('set-cookie' not in values, 'unexpected_http_cookie')
    if surface == 'edge':
        require(bool(values.get('cf-ray')), 'edge_unverified')
        if name == '.well-known/security.txt':
            require(security_digest == digest(raw) and values.get('server', '').lower() == 'cloudflare'
                    and response['url'] == 'https://' + HOST + '/.well-known/security.txt',
                    'security_text_mismatch')
            return
        require('no-store' in values.get('cache-control', '').lower(), 'http_cache_mismatch')
    for key, expected in contract.items():
        if key == 'strict-transport-security':
            match = re.search(r'(?:^|;)\s*max-age=(\d+)(?:;|$)', values.get(key, ''))
            require(match and int(match[1]) >= 31536000, 'http_security_mismatch')
        else:
            require(values.get(key) == expected, 'http_security_mismatch')
    if surface == 'origin':
        cache = values.get('cache-control', '').lower()
        suffix = PurePosixPath(name).suffix
        if suffix == '.html':
            require('no-cache' in cache, 'http_cache_mismatch')
        elif suffix in {'.css', '.js', '.svg', '.webp'}:
            require('public' in cache and 'max-age=31536000' in cache and 'immutable' in cache, 'http_cache_mismatch')
        elif suffix == '.json' or name in {'robots.txt', 'sitemap.xml', 'site.webmanifest', 'llms.txt'}:
            require('public' in cache and 'max-age=3600' in cache, 'http_cache_mismatch')
    if name == 'index.html' or name.startswith('assets/styles/'):
        require(values.get('content-encoding', '').lower() in {'gzip', 'br', 'zstd'}, 'http_compression_missing')


class HTTP:
    def __init__(self, origin, runner=subprocess.run):
        self.origin = str(ipaddress.ip_address(origin))
        require(ipaddress.ip_address(self.origin).is_global, 'invalid_origin')
        self.runner = runner

    def get(self, path, surface='edge', host=HOST, scheme='https', user_agent=None, retry=False, method='GET'):
        require(surface in {'origin', 'edge'} and host in {HOST, WWW} and scheme in {'http', 'https'}, 'invalid_http_target')
        require(method in {'GET', 'HEAD'}, 'invalid_http_target')
        require(type(path) is str and path.startswith('/') and not path.startswith('//')
                and len(path) <= 2048 and not re.search(r'[\x00-\x20\x7f\\#]', path), 'invalid_http_target')
        url = scheme + '://' + host + path
        port = 443 if scheme == 'https' else 80
        with tempfile.TemporaryDirectory(prefix='oss-http-') as directory:
            folder = Path(directory)
            args = ['curl', '--disable', '--silent', '--show-error', '--noproxy', '*',
                    '--proto', '=http,https', '--compressed', '--connect-timeout', '10', '--max-time', '25',
                    '--max-filesize', str(MAX_BODY), '--dump-header', str(folder / 'headers'),
                    '--output', str(folder / 'body'), '--write-out', '%{http_code} %{ssl_verify_result} %{remote_ip}']
            if surface == 'origin':
                address = '[' + self.origin + ']' if ':' in self.origin else self.origin
                args += ['--resolve', host + ':' + str(port) + ':' + address]
            if user_agent is not None:
                require(user_agent == 'TelegramBot', 'invalid_http_target')
                args += ['--user-agent', user_agent]
            if method == 'HEAD':
                args += ['--head']
            args += ['--url', url]
            for attempt in range(3 if retry else 1):
                try:
                    result = self.runner(args, check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         timeout=30, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
                except Exception:
                    raise ArtifactError('http_transport_failed') from None
                if result.returncode == 0:
                    break
                require(retry and attempt < 2 and result.returncode in {7, 28, 52, 56}, 'http_transport_failed')
            require(result.returncode == 0 and len(result.stdout) < 256, 'http_transport_failed')
            fields = result.stdout.decode('ascii').strip().split()
            require(len(fields) == 3 and fields[0].isdigit() and (scheme != 'https' or fields[1] == '0'), 'tls_unverified')
            remote = str(ipaddress.ip_address(fields[2]))
            require((surface == 'origin' and remote == self.origin) or (surface == 'edge' and remote != self.origin),
                    'http_surface_mismatch')
            raw = tree.read_external(folder / 'body', MAX_BODY)
            return {'status': int(fields[0]), 'headers': headers(tree.read_external(folder / 'headers', 128 * 1024)),
                    'body': raw, 'tls_verified': scheme == 'https' and fields[1] == '0', 'url': url}

    def api_read(self, path):
        require(path in {'/api/v1', '/api/v1/missions?limit=100', '/api/v1/contributions?limit=100',
                         '/api/v1/reviews?limit=100'}, 'invalid_http_target')
        result = self.get(path, retry=True)
        values = result['headers']
        require(result['status'] == 200 and result['tls_verified'] and values.get('cf-ray')
                and values.get('content-type', '').startswith('application/json')
                and 'no-store' in values.get('cache-control', '')
                and values.get('x-content-type-options') == 'nosniff'
                and values.get('x-robots-tag') == 'noindex, nofollow'
                and values.get('content-security-policy') == "default-src 'none'; frame-ancestors 'none'"
                and values.get('cf-cache-status', '').upper() not in {'HIT', 'STALE', 'UPDATING', 'REVALIDATED'}
                and 'access-control-allow-origin' not in values and 'set-cookie' not in values,
                'api_unverified')
        return checks.decode(result['body'])

    def api(self, expected_sha=None):
        value = self.api_read('/api/v1')
        sha = value.get('release_sha') if type(value) is dict else None
        require(type(sha) is str and re.fullmatch(r'[a-f0-9]{40}', sha)
                and (expected_sha is None or sha == expected_sha), 'api_unverified')
        return sha

    def public_api(self):
        verified = []
        for route, kinds in [('missions', {'mission'}), ('contributions', {'field-note', 'project'}),
                             ('reviews', {'review'})]:
            path = '/api/v1/' + route + '?limit=100'
            for _ in range(2):
                value = self.api_read(path)
                require(type(value) is dict and type(value.get('items')) is list
                        and len(value['items']) <= 100 and 'next_cursor' in value, 'api_unverified')
                require(all(type(item) is dict and item.get('status') == 'published'
                            and item.get('kind') in kinds and item.get('provenance') in {'seed', 'community'}
                            for item in value['items']), 'api_unverified')
            verified.append({'path': path, 'gets': 2, 'uncached_public_page': True})
        return verified

    def predecessor(self, baseline, access):
        """Capture historical rollback bytes; never run a historical checker."""
        response = self.get('/' + tree.MANIFEST, surface='origin', retry=True)
        raw = response['body']
        require(response['status'] == 200 and digest(raw) == baseline['baseline_manifest_sha256']
                and len(raw) <= tree.MAX_MANIFEST_BYTES, 'baseline_mismatch')
        names = []
        for line in raw.decode().splitlines():
            match = re.fullmatch(r'[a-f0-9]{64}  (?:\./)?(.+)', line)
            require(match is not None, 'baseline_mismatch')
            name = match[1]
            plan.path(name)
            require(name not in names and name != tree.MANIFEST, 'baseline_mismatch')
            names.append(name)
        require(0 < len(names) < tree.MAX_FILES and '.htaccess' in names, 'baseline_mismatch')
        files = {tree.MANIFEST: raw, '.htaccess': access}
        def capture(name):
            result = self.get('/' + name, surface='origin', retry=True)
            require(result['status'] in ({200, 404} if name == '404.html' else {200}), 'baseline_mismatch')
            return name, result['body']
        # Shared origins can limit simultaneous TLS connections. Keep direct
        # origin reads serial; edge verification can use bounded parallelism.
        files.update(capture(name) for name in names if name != '.htaccess')
        plan.payload(files)
        return files

    def probe(self, files, contract, security_digest):
        css = sorted(name for name in files if name.startswith('assets/styles/') and name.endswith('.css'))
        require(bool(css), 'invalid_http_contract')
        nonce = secrets.token_hex(12)
        for name, path in [('index.html', '/'), (css[0], '/' + css[0])]:
            for state in ['MISS', 'HIT']:
                response = self.get(path + '?oss_release_probe=' + nonce)
                exact(response, files[name], name, 'edge', contract, security_digest)
                require(response['headers'].get('cf-cache-status') == state
                        and re.search(r'\bh3(?:-[0-9]+)?=', response['headers'].get('alt-svc', '')),
                        'cache_transition_failed')
        return {'html': ['MISS', 'HIT'], 'stylesheet': ['MISS', 'HIT'], 'http3_advertised': True}

    def redirects(self, legacy=False):
        paths = ('/guide/?oss_redirect_probe=1&literal=a%2Fb',) if legacy else REDIRECT_PATHS
        tasks = [(path, surface, host, scheme, method) for path in paths
                 for surface in ['origin', 'edge']
                 for host, scheme in [(HOST, 'http'), (WWW, 'http'), (WWW, 'https')]
                 for method in ['GET', 'HEAD']]
        def inspect(task):
            path, surface, host, scheme, method = task
            expected = urllib.parse.urlsplit(path)
            current = scheme + '://' + host + path
            for _ in range(3):
                parsed = urllib.parse.urlsplit(current)
                request = parsed.path + ('?' + parsed.query if parsed.query else '')
                result = self.get(request, surface, parsed.hostname, parsed.scheme, retry=True, method=method)
                require(result['status'] in {301, 308}, 'redirect_mismatch')
                location = result['headers'].get('location', '')
                require(type(location) is str and 0 < len(location) <= 4096
                        and re.fullmatch(r'[\x21-\x7e]+', location) is not None,
                        'redirect_mismatch')
                try:
                    target = urllib.parse.urljoin(current, location)
                    changed = urllib.parse.urlsplit(target)
                    port = changed.port
                except ValueError:
                    raise ArtifactError('redirect_mismatch') from None
                require(changed.hostname in {HOST, WWW} and port is None
                        and changed.username is None and changed.password is None
                        and changed.path == expected.path and changed.query == expected.query
                        and not changed.fragment and changed.scheme == 'https', 'redirect_mismatch')
                if target == 'https://' + HOST + path:
                    break
                require(target != current, 'redirect_mismatch')
                current = target
            else:
                raise ArtifactError('redirect_mismatch')
        # Bound origin concurrency; also inspect raw Location on HEAD responses.
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(inspect, tasks))

    def tls(self):
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        results = []
        for surface in ['origin', 'edge']:
            for host in [HOST, WWW]:
                try:
                    with socket.create_connection((self.origin if surface == 'origin' else host, 443), timeout=10) as connection:
                        with context.wrap_socket(connection, server_hostname=host) as secured:
                            results.append({'surface': surface, 'host': host,
                                            'certificate_sha256': digest(secured.getpeercert(binary_form=True)),
                                            'hostname_verified': True})
                except Exception:
                    raise ArtifactError('tls_unverified') from None
        return results

    def verify(self, files, edge, api_sha, cache_probe=True, historical=False):
        plan.payload(files)
        contract = security_headers(files)
        security_digest = edge['security_sha256']
        require(security_digest == digest(files['.well-known/security.txt']), 'security_text_mismatch')
        probe = self.probe(files, contract, security_digest) if cache_probe else None
        tasks = [(surface, name, '/' + name) for surface in ['origin', 'edge']
                 for name in sorted(files) if name != '.htaccess']
        tasks += [(surface, name, '/' + name.removesuffix('index.html')) for surface in ['origin', 'edge']
                  for name in sorted(files) if name == 'index.html' or name.endswith('/index.html')]
        def inspect(task):
            surface, name, path = task
            result = self.get(path, surface, retry=True)
            exact(result, files[name], name, surface, contract, security_digest,
                  (200, 404) if name == '404.html' else (200,))
        for task in [item for item in tasks if item[0] == 'origin']:
            inspect(task)
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(inspect, [item for item in tasks if item[0] == 'edge']))
        for surface in ['origin', 'edge']:
            missing = self.get('/oss-release-missing-' + secrets.token_hex(12), surface, retry=True)
            exact(missing, files['404.html'], '404.html', surface, contract, security_digest, (404,))
        bot = self.get('/', user_agent='TelegramBot', retry=True)
        exact(bot, files['index.html'], 'index.html', 'edge', contract, security_digest)
        legacy = historical and digest(files['.htaccess']) == LEGACY_ACCESS_SHA256
        self.redirects(legacy=legacy)
        tls = self.tls()
        self.api(api_sha)
        public_api = self.public_api()
        return {'exact_files': len(files) - 1, 'surfaces': ['origin', 'edge'],
                'cache_probe': probe, 'tls': tls, 'redirects_verified': True,
                'redirect_contract': 'historical-plain-path' if legacy else 'encoded-path-v1',
                'custom_404_verified': True, 'telegram_bytes_verified': True, 'api_release_sha': api_sha,
                'public_api': public_api}
