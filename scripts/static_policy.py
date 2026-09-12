"""Independent static-file policy for the installed remote release endpoint.

An SSH request cannot change this policy or the server configuration it binds.
The operator must verify the handler guard on the actual web server before
enabling a production credential; filesystem fixtures do not prove HTTP behavior.
"""
import base64
import binascii
import re

import static_plan as plan
from site_artifact import ArtifactError, MANIFEST, require

MAX_CANDIDATE_BYTES = 8 * 1024 * 1024
MAX_STATIC_FILE_BYTES = 4 * 1024 * 1024
STATIC_GUARD = b'''# BEGIN OSS STATIC HANDLER GUARD v1
Options -ExecCGI -Includes -MultiViews
RemoveHandler .html .css .js .svg .webp .png .ico .json .xml .webmanifest .txt .sha256
RemoveInputFilter .html .css .js .svg .webp .png .ico .json .xml .webmanifest .txt .sha256
RemoveOutputFilter .html .css .js .svg .webp .png .ico .json .xml .webmanifest .txt .sha256
RemoveType .html .css .js .svg .webp .png .ico .json .xml .webmanifest .txt .sha256
AddType text/html .html
AddType text/css .css
AddType text/javascript .js
AddType image/svg+xml .svg
AddType image/webp .webp
AddType image/png .png
AddType image/x-icon .ico
AddType application/json .json
AddType application/xml .xml
AddType application/manifest+json .webmanifest
AddType text/plain .txt .sha256
<FilesMatch "\\.(?:html|css|js|svg|webp|png|ico|json|xml|webmanifest|txt|sha256)$">
  SetHandler default-handler
  AcceptPathInfo Off
</FilesMatch>
# END OSS STATIC HANDLER GUARD v1
'''
SLUG = r'[a-z0-9]+(?:-[a-z0-9]+)*'
ASSET = SLUG + r'(?:\.[a-f0-9]{8,64})?'
ROOT_FILES = {'index.html', '404.html', 'favicon.ico', 'robots.txt', 'sitemap.xml', 'llms.txt',
              'site.webmanifest', MANIFEST, '.htaccess',
              '.well-known/agent-home.json', '.well-known/security.txt'}


def decode(value, limit):
    require(type(value) is str and len(value) <= 4 * ((limit + 2) // 3), 'invalid_encoding')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ArtifactError('invalid_encoding') from None
    require(len(raw) <= limit and base64.b64encode(raw).decode('ascii') == value,
            'invalid_encoding')
    return raw


def allowed_path(name):
    plan.path(name)
    if name in ROOT_FILES:
        return True
    if name.split('/')[0] in {'api', 'cgi-bin'}:
        return False
    return any(re.fullmatch(pattern, name) is not None for pattern in (
        SLUG + r'/index\.html',
        r'assets/scripts/' + ASSET + r'\.js',
        r'assets/styles/' + ASSET + r'\.css',
        r'assets/(?:brand|projects|social)/' + ASSET + r'\.(?:svg|webp|png|ico)',
        r'data/' + SLUG + r'(?:\.schema)?\.json',
    ))


def validate_policy(value):
    require(type(value) is dict and type(value.get('schema')) is int
            and value['schema'] in {1, 2}, 'invalid_policy')
    if value['schema'] == 1:
        plan.object_fields(value, {'schema', 'htaccess', 'installed_htaccess_sha256',
                                   'ancestor_htaccess'}, 'invalid_policy')
        versions = [{name: value[name] for name in ('htaccess', 'installed_htaccess_sha256')}]
    else:
        plan.object_fields(value, {'schema', 'htaccess_versions', 'ancestor_htaccess'}, 'invalid_policy')
        versions = value['htaccess_versions']
        require(type(versions) is list and len(versions) == 2, 'invalid_policy')
    pairs = []
    for version in versions:
        plan.object_fields(version, {'htaccess', 'installed_htaccess_sha256'}, 'invalid_policy')
        access = decode(version['htaccess'], 8192)
        require(access and STATIC_GUARD not in access, 'invalid_policy')
        installed = version['installed_htaccess_sha256']
        plan.hex_value(installed, 64, 'invalid_policy')
        require(all(access != previous and installed != previous_hash
                    for previous, previous_hash in pairs), 'invalid_policy')
        pairs.append((access, installed))
    require(type(value['ancestor_htaccess']) is dict
            and len(value['ancestor_htaccess']) <= 32, 'invalid_policy')
    for name, expected in value['ancestor_htaccess'].items():
        require(type(name) is str and name.startswith('/'), 'invalid_policy')
        if expected is not None:
            plan.hex_value(expected, 64, 'invalid_policy')
    return pairs


def validate_candidate(files, policy):
    """Additional server restrictions, independent of the runner's product tests."""
    plan.payload(files)
    pairs = validate_policy(policy)
    require(any(files['.htaccess'] == access for access, _installed in pairs),
            'server_configuration_changed')
    require(sum(len(raw) for raw in files.values()) <= MAX_CANDIDATE_BYTES,
            'candidate_limit')
    for name, raw in files.items():
        require(allowed_path(name), 'non_static_path')
        require(len(raw) <= MAX_STATIC_FILE_BYTES, 'candidate_limit')


def validate_installed_access(raw, policy):
    pairs = validate_policy(policy)
    matches = [access for access, installed in pairs if plan.digest(raw) == installed]
    require(len(matches) == 1 and raw.endswith(STATIC_GUARD) and raw.count(STATIC_GUARD) == 1,
            'server_configuration_changed')
    access = matches[0]
    require(raw.count(access) == 1, 'server_configuration_changed')
    return access


def validate_replacement(installed, candidate_access, policy):
    """Bind the candidate to its approved full hash without changing either overlay."""
    previous_access = validate_installed_access(installed, policy)
    offset = installed.index(previous_access)
    composite = installed[:offset] + candidate_access + installed[offset + len(previous_access):]
    require(validate_installed_access(composite, policy) == candidate_access,
            'server_configuration_changed')


def validate_parents(names, entries):
    """No nested override may affect a path the credential can publish."""
    for name in names:
        for parent in plan.parents(name):
            require(parent + '/.htaccess' not in entries, 'nested_server_configuration')
