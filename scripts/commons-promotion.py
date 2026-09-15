"""Fixed operator command for one Commons worker promotion (procedure steps 3-7).

Derives everything the engine needs from the live provider state — the
installed bindings become inherit entries, the compatibility date, predecessor
version and current release identity are read from the active version — and
takes only the candidate packet (the provider's own multipart form), its commit
and the account identifiers from the environment. Consuming and verifying the
canonical candidate (procedure steps 1-2) happens through the implemented
contracts before this command runs; it deliberately performs no candidate
download or planning itself.

Environment: COMMONS_CF_TOKEN (provider token), COMMONS_ACCOUNT_ID,
COMMONS_ZONE_ID, COMMONS_D1_ID, STATIC_ORIGIN_IP (live acceptance).
Account identifiers stay outside the repository by the hosting boundary.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys

import commons_cloudflare as adapter
import commons_promotion as engine
from release_deployment import Deployments
from release_http import HTTP
from release_source import token
from site_artifact import ArtifactError, require

SCRIPT_NAME = 'oss-singularity-commons'


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ArtifactError('invalid_arguments')


def packet_modules(content):
    """Return the module names of a multipart candidate packet, in order."""
    lines = content.split(b'\r\n', 1)
    require(len(lines) == 2 and lines[0].startswith(b'--'), 'invalid_candidate')
    names = []
    for part in content.split(b'--' + lines[0][2:])[1:-1]:
        head = part.partition(b'\r\n\r\n')[0].decode('utf-8', 'replace')
        if 'name="metadata"' in head:
            continue
        found = head.split('name="', 1)[1].split('"', 1)[0] if 'name="' in head else None
        require(found is not None, 'invalid_candidate')
        names.append(found)
    require(0 < len(names) <= 32 and len(set(names)) == len(names), 'invalid_candidate')
    return names


def derive_plan(observation, active_detail, message, tag):
    """Build the engine plan from live state: inherit bindings, current identity."""
    require(type(observation) is dict and observation.get('active_version'),
            'provider_state_unverified')
    detail = active_detail
    resources = detail.get('resources')
    require(type(resources) is dict, 'provider_state_unverified')
    installed = resources.get('bindings')
    engine.binding_fingerprint(installed)
    runtime = resources.get('script_runtime')
    require(type(runtime) is dict and type(runtime.get('compatibility_date')) is str,
            'provider_state_unverified')
    release_sha = None
    for item in installed:
        if item.get('name') == 'RELEASE_SHA':
            release_sha = item.get('text')
    require(type(release_sha) is str and len(release_sha) == 40, 'provider_state_unverified')
    return {
        'predecessor_version': observation['active_version'], 'release_sha': release_sha,
        'message': message, 'tag': tag,
        'bindings': [{'name': item['name'], 'type': 'inherit'} for item in installed],
        'installed_bindings': installed, 'main_module': None,
        'compatibility_date': runtime['compatibility_date'],
        'plan_sha256': None,
    }


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    parser = Parser(description=__doc__)
    parser.add_argument('--packet', required=True, help='Path to the candidate packet (multipart)')
    parser.add_argument('--commit', required=True)
    parser.add_argument('--message', required=True)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args(argv)
    account_id = token(environ, 'COMMONS_ACCOUNT_ID')
    zone_id = token(environ, 'COMMONS_ZONE_ID')
    d1_id = token(environ, 'COMMONS_D1_ID')
    cf_token = token(environ, 'COMMONS_CF_TOKEN')
    origin_ip = token(environ, 'STATIC_ORIGIN_IP')
    from pathlib import Path as _Path
    content = _Path(args.packet).read_bytes()
    candidate = {'commit': args.commit, 'content': content, 'modules': packet_modules(content)}
    a = adapter.CloudflareAdapter(token=cf_token, account_id=account_id, zone_id=zone_id,
                                  script_name=SCRIPT_NAME, d1_uuid=d1_id)
    observation = a.observe()
    detail = a.version_detail(observation['active_version'])
    plan = derive_plan(observation, detail, args.message, args.tag)
    plan['main_module'] = 'worker.mjs'
    http = HTTP(origin_ip)
    intent = engine.PromotionIntent(environ)
    return engine.promote(a, intent, plan, candidate, lambda sha: http.api(sha) == sha)


def cli(argv=None, environ=None):
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
            result = main(argv, environ)
    except SystemExit as error:
        if error.code == 0:
            print(buffer.getvalue(), end='')
            return 0
        result = {'error': 'invalid_arguments', 'promoted': False}
    except ArtifactError as error:
        result = {'error': error.code if error.code in {
            'invalid_arguments', 'invalid_candidate', 'provider_state_unverified',
            'unfinished_promotion', 'promotion_record_closed', 'invalid_promotion_record',
            'promotion_record_unconfirmed', 'promotion_history_unverified',
            'promotion_unresolved', 'invalid_promotion_outcome', 'invalid_plan',
            'staged_version_unverified', 'staged_bindings_changed', 'staged_modules_changed',
            'staged_settings_changed', 'invalid_bindings', 'provider_request_failed',
            'ambiguous_staged_version', 'live_acceptance_failed', 'invalid_bindings',
            'missing_credential', 'invalid_origin', 'http_transport_failed', 'api_unverified',
            'deployment_record_unconfirmed', 'invalid_deployment_route',
        } else 'promotion_failed', 'promoted': False}
    except Exception:
        result = {'error': 'promotion_failed', 'promoted': False}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    raise SystemExit(cli())
