"""Release orchestration against installed processes and offline external services."""
from copy import deepcopy
from contextlib import contextmanager
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import release_source as source
import release_edge as edge
import release_http as http
import release_deployment as deployments
import release_transport as transport
import static_publication as publication
from site_artifact import ArtifactError, MANIFEST

ROOT = Path(__file__).resolve().parent
fixture = source.module('publication_remote_fixture', 'test-static-remote.py')
fixtures = source.module('publication_candidate_fixture', 'test-release-candidate.py')
OLD, NEW = fixture.OLD, fixture.NEW
ACCESS = (ROOT.parent / 'site/.htaccess').read_bytes()
SECURITY = b'Contact: https://example.invalid/security\n'


class InstalledRemote(transport.SSH):
    def __init__(self, installed, trace, lost=None, crash=None):
        self.installed, self.trace, self.lost, self.crash = installed, trace, lost, crash
        hashes = {item.name: source.digest(item.read_bytes()) for item in installed.runtime.iterdir()}
        self.runtime = source.digest(source.encode(hashes))

    def call(self, request):
        operation = request['operation']
        self.trace.append(operation)
        if operation == 'apply' and self.crash:
            boundary, self.crash = self.crash, None
            test = fixture.RemoteTests()
            test.stop(self.installed, request, boundary)
            raise transport.RemoteFailure('remote_outcome_unconfirmed')
        result = subprocess.run([sys.executable, '-I', '-S', str(self.installed.runtime / 'static-remote-release.py'),
            '--config', str(self.installed.config)], input=source.encode(request), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env={**os.environ, 'SSH_ORIGINAL_COMMAND': 'oss-static-release-v1'}, timeout=10)
        if result.returncode != 0:
            raise transport.RemoteFailure('remote_outcome_unconfirmed')
        value = source.checks.decode(result.stdout)
        transport.validate_report(value, self.runtime)
        if self.lost == operation:
            self.lost = None
            raise transport.RemoteFailure('remote_outcome_unconfirmed')
        return value


class FixedEdge:
    def __init__(self, trace, observation):
        self.trace, self.observation = trace, observation
    def observe(self, _security):
        self.trace.append('edge-observe')
        return deepcopy(self.observation)
    def purge(self):
        self.trace.append('purge')


class ObservedHTTP:
    def __init__(self, installed, trace, fail=False, interfere=False):
        self.installed, self.trace, self.fail, self.interfere = installed, trace, fail, interfere
    def verify(self, files, _edge, _sha):
        self.trace.append('http')
        actual = fixture.file_contents(self.installed.target)
        for name, raw in files.items():
            if name != '.htaccess':
                if actual.get(name) != raw:
                    raise ArtifactError('http_bytes_mismatch')
        if self.fail:
            self.fail = False
            if self.interfere:
                (self.installed.target / 'index.html').write_bytes(b'newer operator content')
            raise ArtifactError('http_bytes_mismatch')
        return {'exact_files': len(files) - 1}


class RecordedDeployments:
    def __init__(self, trace):
        self.trace, self.intent, self.outcome = trace, None, None
    def start(self, sha, intent, previous):
        self.trace.append('intent')
        self.intent = deepcopy(intent)
        return 42
    def finish(self, number, outcome):
        if number != 42:
            raise AssertionError('wrong deployment')
        self.trace.append(outcome)
        self.outcome = outcome


@contextmanager
def prepared_case(**options):
    values = {'.htaccess': ACCESS, 'index.html': b'old page', '404.html': b'missing',
              '.well-known/security.txt': SECURITY, 'assets/styles/site.css': b'body {}'}
    old = fixture.payload(values)
    new = fixture.payload({**values, 'index.html': b'new page', 'assets/scripts/new.js': b'new script'})
    with fixture.installation(new, predecessor=old) as installed:
        trace = []
        remote = InstalledRemote(installed, trace, options.get('lost'), options.get('crash'))
        baseline = remote.status()
        product = source.Candidate(NEW, 13, 1, fixture.descriptor(new, NEW), new,
            {'descriptor': json.loads(fixture.descriptor(new, NEW)),
             'artifacts': {'candidate': {'id': 71, 'digest_sha256': 'a' * 64},
                           'receipt': {'id': 72, 'digest_sha256': 'b' * 64}}})
        observation = {'security_sha256': source.digest(SECURITY), 'configuration_sha256': 'c' * 64}
        recorded = RecordedDeployments(trace)
        folder = installed.base / 'client'
        folder.mkdir(mode=0o700)
        def fresh(stage):
            trace.append(stage)
            if options.get('stale') == stage:
                raise ArtifactError('stale_main')
        def execute():
            return publication.transition(product, baseline, old, observation, OLD,
                {'workflow_run_id': 3, 'workflow_run_attempt': 1}, folder, remote,
                ObservedHTTP(installed, trace, options.get('fail', False), options.get('interfere', False)),
                FixedEdge(trace, observation), recorded, None, fresh)
        yield installed, execute, recorded, remote, trace


class PublicationTests(unittest.TestCase):
    def test_real_installed_publication_preserves_non_target_and_rollback(self):
        with prepared_case() as (installed, execute, recorded, remote, trace):
            result = execute()
            self.assertTrue(result['publication_verified'])
            self.assertEqual(recorded.outcome, 'success')
            self.assertLess(trace.index('intent'), trace.index('prepare'))
            self.assertEqual(trace[trace.index('apply') + 1:trace.index('apply') + 3], ['purge', 'http'])
            self.assertLess(trace.index('http'), trace.index('maintain'))
            self.assertEqual((installed.peer / 'sentinel').read_bytes(), b'unrelated data\n')
            self.assertEqual((installed.target / '.well-known/provider-marker').read_bytes(), b'preserve')
            observed = remote.status(recorded.intent['identity'], recorded.intent['descriptor_sha256'])
            remote.operation('rollback', observed['ticket'])
            self.assertEqual(fixture.file_contents(installed.target), installed.original)

    def test_lost_prepare_apply_and_maintenance_retain_the_original_identity(self):
        for operation in ['prepare', 'apply', 'maintain']:
            with self.subTest(operation=operation), prepared_case(lost=operation) as (_installed, execute, recorded, _remote, trace):
                result = execute()
                self.assertTrue(result['publication_verified'])
                self.assertEqual(trace.count('prepare'), 1)
                self.assertEqual(recorded.outcome, 'success')
                self.assertEqual(len(recorded.intent['identity']), 32)

    def test_real_process_exit_after_write_is_reconciled_before_continuation(self):
        with prepared_case(crash='forward-written-0') as (_installed, execute, _recorded, _remote, trace):
            result = execute()
            self.assertTrue(result['publication_verified'])
            first = trace.index('apply')
            second = trace.index('apply', first + 1)
            self.assertIn('reconcile', trace[first + 1:second])
            self.assertIn('before_recovered_apply', trace[first + 1:second])

    def test_failed_live_verification_restores_original_bytes_and_reports_failure(self):
        with prepared_case(fail=True) as (installed, execute, recorded, _remote, trace):
            with self.assertRaisesRegex(ArtifactError, 'publication_rolled_back'):
                execute()
            self.assertEqual(fixture.file_contents(installed.target), installed.original)
            self.assertEqual(recorded.outcome, 'rolled_back')
            self.assertEqual(trace.count('purge'), 2)
            self.assertNotIn('success', trace)

    def test_lost_rollback_response_is_observed_without_repeating_rollback(self):
        with prepared_case(fail=True, lost='rollback') as (installed, execute, recorded, _remote, trace):
            with self.assertRaisesRegex(ArtifactError, 'publication_rolled_back'):
                execute()
            self.assertEqual(trace.count('rollback'), 1)
            self.assertEqual(recorded.outcome, 'rolled_back')
            self.assertEqual(fixture.file_contents(installed.target), installed.original)

    def test_newer_operator_bytes_are_preserved_and_leave_an_unresolved_intent(self):
        with prepared_case(fail=True, interfere=True) as (installed, execute, recorded, _remote, _trace):
            with self.assertRaisesRegex(ArtifactError, 'publication_requires_reconciliation'):
                execute()
            self.assertEqual((installed.target / 'index.html').read_bytes(), b'newer operator content')
            self.assertEqual(recorded.outcome, 'unresolved')

    def test_stale_main_blocks_preparation_or_application_and_keeps_old_site(self):
        for stage in ['before_prepare', 'before_apply', 'before_recovered_apply']:
            with self.subTest(stage=stage), prepared_case(stale=stage,
                    crash='forward-written-0' if stage == 'before_recovered_apply' else None) as (installed, execute, recorded, _remote, trace):
                with self.assertRaisesRegex(ArtifactError, 'publication_rolled_back'):
                    execute()
                self.assertEqual(fixture.file_contents(installed.target), installed.original)
                self.assertEqual(recorded.outcome, 'rolled_back')
                if stage == 'before_prepare':
                    self.assertNotIn('prepare', trace)
                elif stage == 'before_apply':
                    self.assertNotIn('apply', trace)

    def test_invalid_workflow_never_reaches_a_network_or_provider(self):
        for env in [{}, {'GITHUB_ACTIONS': 'true'}, {'GITHUB_REPOSITORY': 'other/repo'}]:
            with self.assertRaisesRegex(ArtifactError, 'untrusted_workflow'):
                publication.context(NEW, 'publish', env)

    def test_maintenance_and_bookkeeping_failures_keep_a_verified_site_in_place(self):
        for failure in ['maintain', 'bookkeeping']:
            with self.subTest(failure=failure), prepared_case() as (installed, execute, recorded, remote, trace):
                original = remote.operation
                def operation(name, ticket):
                    if name == 'maintain' and failure == 'maintain':
                        raise transport.RemoteFailure('remote_outcome_unconfirmed')
                    return original(name, ticket)
                remote.operation = operation
                finish = recorded.finish
                def record(number, outcome):
                    if outcome == 'success' and failure == 'bookkeeping':
                        raise ArtifactError('deployment_record_unconfirmed')
                    return finish(number, outcome)
                recorded.finish = record
                with self.assertRaises(ArtifactError): execute()
                self.assertEqual((installed.target / 'index.html').read_bytes(), b'new page')
                self.assertNotIn('rollback', trace)
                self.assertNotEqual(recorded.outcome, 'success')

    def test_dispatch_context_binds_main_source_mode_and_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / 'event.json'
            event.write_text(json.dumps({'inputs': {'mode': 'plan'}}))
            env = {'GITHUB_ACTIONS': 'true', 'GITHUB_SERVER_URL': 'https://github.com',
                   'GITHUB_API_URL': source.checks.API, 'GITHUB_REPOSITORY': source.checks.REPOSITORY,
                   'GITHUB_REPOSITORY_ID': str(source.checks.REPOSITORY_ID), 'GITHUB_REF': 'refs/heads/main',
                   'GITHUB_REF_PROTECTED': 'true', 'GITHUB_SHA': NEW, 'GITHUB_WORKFLOW_SHA': NEW,
                   'GITHUB_WORKFLOW_REF': source.checks.REPOSITORY + '/' + publication.WORKFLOW + '@refs/heads/main',
                   'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_EVENT_PATH': str(event),
                   'GITHUB_RUN_ID': '43', 'GITHUB_RUN_ATTEMPT': '1'}
            self.assertEqual(publication.context(NEW, 'plan', env), {'workflow_run_id': 43, 'workflow_run_attempt': 1})
            for key, value in [('GITHUB_REF_PROTECTED', 'false'), ('GITHUB_WORKFLOW_SHA', OLD),
                               ('GITHUB_EVENT_NAME', 'pull_request_target')]:
                with self.assertRaises(ArtifactError): publication.context(NEW, 'plan', {**env, key: value})
            with self.assertRaisesRegex(ArtifactError, 'publication_disabled'): publication.context(NEW, 'publish', env)


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.CandidateTests.setUpClass()
        cls.addClassCleanup(fixtures.CandidateTests.doClassCleanups)

    def test_real_candidate_consumer_uses_selected_ids_and_exact_original_archives(self):
        template = fixtures.CandidateTests()
        values = template.fixtures()
        values[source.BASE + '/actions/workflows/351107990/runs?head_sha=' + fixtures.SHA + '&per_page=100&page=1'] = {
            'total_count': 1, 'workflow_runs': [fixtures.run()]}
        values[source.BASE + '/actions/runs/248/artifacts?per_page=100&page=1'] = {
            'total_count': 2, 'artifacts': [fixtures.uploaded(fixtures.CANDIDATE, 'candidate', template.candidate_bytes),
                fixtures.uploaded(fixtures.RECEIPT, 'rehearsal-receipt', template.receipt_bytes)]}
        class FakeGitHub:
            def get(self, route, _environ=None): return deepcopy(values[route])
            def download(self, number, limit):
                raw = {fixtures.CANDIDATE: template.candidate_bytes, fixtures.RECEIPT: template.receipt_bytes}[number]
                if len(raw) > limit: raise ArtifactError('size_limit')
                return raw
        with tempfile.TemporaryDirectory() as folder:
            value = source.consume(FakeGitHub(), fixtures.SHA, Path(folder))
            self.assertEqual(value.descriptor, template.payload['release.json'])
            self.assertEqual(value.files, {name.removeprefix('payload/'): raw for name, raw in template.payload.items()
                                          if name.startswith('payload/')})
        values[source.BASE + '/actions/workflows/351107990/runs?head_sha=' + fixtures.SHA + '&per_page=100&page=1']['workflow_runs'] *= 2
        with self.assertRaises(ArtifactError):
            source.selected_run(FakeGitHub(), fixtures.SHA)

    def test_policy_credential_is_used_only_for_the_two_administration_reads(self):
        # Explicit synthetic public markers; no real credential in these tests.
        environ = {'GH_TOKEN': 'public-fixture-standard', 'GH_POLICY_TOKEN': 'public-fixture-policy'}
        api = source.GitHub(environ)
        for route, expected in [(source.checks.MAIN, environ['GH_TOKEN']), (source.checks.RULES, environ['GH_TOKEN']),
                                (source.checks.PROTECTION, environ['GH_POLICY_TOKEN']), (source.checks.SETUP, environ['GH_POLICY_TOKEN'])]:
            self.assertEqual(api.request(route).get_header('Authorization'), 'Bearer ' + expected)
        for route in ['/user', '/repos/other/repo/branches/main', source.BASE + '/actions/artifacts/1/zip']:
            self.assertFalse(api.read_route(route))

    def test_storage_redirect_receives_no_github_credential_and_is_bounded(self):
        signed = 'https://example.blob.core.windows.net/artifact?signature=public-fixture'
        calls = []
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                if len(calls) == 1:
                    raise urllib.error.HTTPError(request.full_url, 302, 'Found', {'Location': signed}, io.BytesIO())
                return fixtures.Response(b'zip', signed)
        api = source.GitHub({'GH_TOKEN': 'public-fixture-standard'}, Opener())
        self.assertEqual(api.download(12, 4), b'zip')
        self.assertIsNone(calls[-1].get_header('Authorization'))
        for url in ['http://example.blob.core.windows.net/x', 'https://example.invalid/x',
                    'https://user@example.blob.core.windows.net/x', 'https://example.blob.core.windows.net:444/x']:
            calls.clear()
            signed = url
            with self.assertRaises(ArtifactError): api.download(12, 4)
            self.assertEqual(len(calls), 1)

    def test_backend_tree_must_exactly_match_the_independently_selected_api_release(self):
        class GitHub:
            def get(self, _route): return {'truncated': False, 'tree': [
                {'path': 'services/commons', 'type': 'tree', 'mode': '040000', 'sha': OLD}]}
        self.assertTrue(source.compatibility(GitHub(), NEW, OLD)['source_unchanged'])
        with self.assertRaisesRegex(ArtifactError, 'api_compatibility_unverified'):
            source.compatibility(GitHub(), NEW, NEW)

    def test_independent_queue_wait_is_bounded_and_never_retries_failure_or_stale_main(self):
        now, waits = [0], []
        states = {number: 'in_progress' for number in [*source.checks.WORKFLOWS, 351107990]}
        class GitHub:
            sha = NEW
            def get(self, route):
                if route == source.checks.MAIN:
                    return {'protected': True, 'commit': {'sha': self.sha}}
                number = int(route.split('/workflows/')[1].split('/')[0])
                return {'total_count': 1, 'workflow_runs': [{
                    'id': number + 1,
                    'head_sha': NEW, 'head_branch': 'main', 'workflow_id': number,
                    'head_repository': {'id': source.checks.REPOSITORY_ID},
                    'event': 'dynamic' if number == 345943682 else 'push',
                    'status': states[number], 'conclusion': 'success' if now[0] < 100 else 'failure'}]}
        api = GitHub()
        def pause(seconds):
            waits.append(seconds)
            now[0] += seconds
            states[351107990 if now[0] == 20 else 345943682] = 'completed'
            states[345834976] = 'completed'
        source.wait_for_sources(api, NEW, pause, lambda: now[0])
        self.assertEqual(waits, [20, 20])
        now[0] = 100
        with self.assertRaisesRegex(ArtifactError, 'unsuccessful_run'):
            source.wait_for_sources(api, NEW, pause, lambda: now[0])
        api.sha = OLD
        with self.assertRaisesRegex(ArtifactError, 'stale_main'):
            source.wait_for_sources(api, NEW, pause, lambda: now[0])
        api.sha, now[0] = NEW, 0
        states = {number: 'queued' for number in states}
        def stuck(seconds): now[0] += seconds
        with self.assertRaisesRegex(ArtifactError, 'source_wait_expired'):
            source.wait_for_sources(api, NEW, stuck, lambda: now[0])
        self.assertEqual(now[0], 360)


class EdgeTests(unittest.TestCase):
    def snapshot(self):
        settings = {'ssl': 'strict', 'http3': 'on', 'brotli': 'on', 'tls_1_3': 'on',
                    'min_tls_version': '1.2', 'development_mode': 'off'}
        records = [{'id': str(index), 'name': name + '.' + edge.HOST, 'type': 'A', 'proxied': False}
                   for index, name in enumerate(['mail', 'ftp', 'cpanel', 'cpcalendars', 'cpcontacts',
                                                 'webdisk', 'webmail', 'whm'])]
        records += [{'id': 'apex', 'name': edge.HOST, 'type': 'A', 'proxied': True},
                    {'id': 'www', 'name': 'www.' + edge.HOST, 'type': 'CNAME', 'proxied': True},
                    {'id': 'mx', 'name': edge.HOST, 'type': 'MX', 'proxied': False}]
        result = {
            '': {'id': edge.ZONE, 'name': edge.HOST, 'status': 'active', 'development_mode': -120},
            '/settings': [{'id': key, 'value': value, 'modified_on': '2026-09-01T00:00:00Z'}
                          for key, value in settings.items()],
            '/dns_records?per_page=100&page=1': records,
            '/rulesets/phases/http_request_cache_settings/entrypoint': {'rules': [{
                'ref': 'oss_static_edge_only_cache', 'enabled': True,
                'action': 'set_cache_settings',
                'expression': '(http.host eq "oss-singularity.io") or (http.host eq "www.oss-singularity.io")',
                'action_parameters': {'cache': True, 'edge_ttl': {'mode': 'override_origin', 'default': 7200},
                                      'browser_ttl': {'mode': 'bypass'}}}, {
                'ref': 'oss_commons_api_bypass', 'enabled': True, 'action': 'set_cache_settings',
                'action_parameters': {'cache': False},
                'expression': '(http.host eq "oss-singularity.io" and starts_with(http.request.uri.path, "/api/"))'}]},
            '/rulesets/phases/http_response_cache_settings/entrypoint': {'rules': [{
                'ref': 'upstream_no_cache_response_guard', 'enabled': True,
                'expression': 'any(http.response.headers["cf-edge-cache"][*] == "no-cache")',
                'action': 'set_cache_control',
                'action_parameters': {'no-store': {'cloudflare_only': True, 'operation': 'set'}}}]},
            '/security-center/securitytxt': {'enabled': True, 'contact': ['https://example.invalid/security'],
                'canonical': ['https://' + edge.HOST + '/.well-known/security.txt'], 'expires': '2027-01-01T00:00:00Z'}}
        return {route: {'result': value, 'result_info': {'total_count': len(records), 'total_pages': 1}}
                for route, value in result.items()}

    def test_elapsed_timer_and_record_order_are_stable_but_configuration_changes_are_visible(self):
        snapshots = self.snapshot()
        class API(edge.Cloudflare):
            def request(self, route, method='GET'): return deepcopy(snapshots[route])
        api = API({})
        security = edge.security_text(snapshots['/security-center/securitytxt']['result'])
        before = api.observe(security)
        snapshots['']['result']['development_mode'] = -999
        snapshots['/settings']['result'].reverse()
        snapshots['/dns_records?per_page=100&page=1']['result'].reverse()
        self.assertEqual(before, api.observe(security))
        snapshots['/dns_records?per_page=100&page=1']['result'][0]['priority'] = 50
        self.assertNotEqual(before['dns_sha256'], api.observe(security)['dns_sha256'])
        snapshots['/settings']['result'][0]['modified_on'] = '2026-09-02T00:00:00Z'
        self.assertNotEqual(before['configuration_sha256'], api.observe(security)['configuration_sha256'])

    def test_active_development_mode_disabled_guard_and_expanded_proxy_scope_fail(self):
        original = self.snapshot()
        snapshots = deepcopy(original)
        class API(edge.Cloudflare):
            def request(self, route, method='GET'): return deepcopy(snapshots[route])
        api = API({})
        security = edge.security_text(original['/security-center/securitytxt']['result'])
        guard_route = '/rulesets/phases/http_response_cache_settings/entrypoint'
        variants = [('', ['development_mode'], 20),
                    (guard_route, ['rules', 0, 'enabled'], False),
                    (guard_route, ['rules', 0, 'action_parameters', 'no-store', 'operation'], 'remove'),
                    ('/rulesets/phases/http_request_cache_settings/entrypoint', ['rules', 1, 'enabled'], False),
                    ('/dns_records?per_page=100&page=1', [0, 'proxied'], True)]
        for route, keys, value in variants:
            snapshots = deepcopy(original)
            item = snapshots[route]['result']
            for key in keys[:-1]: item = item[key]
            item[keys[-1]] = value
            with self.subTest(route=route, keys=keys), self.assertRaises(ArtifactError): api.observe(security)

    def test_cache_purge_is_the_only_mutation_and_never_leaves_the_fixed_zone(self):
        calls = []
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                return fixtures.Response(source.encode({'success': True, 'errors': [], 'result': {'id': edge.ZONE}}),
                                         request.full_url)
        api = edge.Cloudflare({'CF_RELEASE_TOKEN': 'synthetic-public-fixture'}, Opener())
        api.purge()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].full_url, edge.API + edge.BASE + '/purge_cache')
        self.assertEqual(json.loads(calls[0].data), {'purge_everything': True})
        for method, route in [('POST', '/dns_records'), ('DELETE', '/purge_cache'),
                               ('GET', '/../../accounts'), ('PUT', '/settings')]:
            with self.assertRaisesRegex(ArtifactError, 'invalid_provider_route'): api.request(route, method)
        self.assertEqual(len(calls), 1)


class DeploymentTests(unittest.TestCase):
    def test_unfinished_unknown_and_failed_unverified_attempts_block_new_publication(self):
        item = {'id': 42, 'environment': deployments.ENVIRONMENT, 'task': deployments.TASK,
                'production_environment': True, 'payload': {'kind': 'static-publication-intent'}}
        class Records(deployments.Deployments):
            def request(self, _method, route, body=None):
                return [item] if route == deployments.LIST else statuses
        api = Records({})
        for statuses in [[], [{'state': 'in_progress'}], [{'state': 'error'}],
                         [{'state': 'success', 'description': 'arbitrary success'}],
                         [{'state': 'failure', 'description': 'failed'}]]:
            with self.assertRaises(ArtifactError): api.previous()
        for state, description in [('success', deployments.SUCCESS), ('failure', deployments.ROLLED_BACK)]:
            statuses = [{'state': state, 'description': description}]
            self.assertEqual(api.previous(), 42)


class HTTPTests(unittest.TestCase):
    def response(self, raw=b'hello', name='index.html', surface='edge'):
        contract = http.security_headers({'.htaccess': ACCESS})
        values = {**contract, 'content-type': 'text/html', 'content-encoding': 'gzip',
                  'cache-control': 'no-store' if surface == 'edge' else 'no-cache', 'cf-ray': 'public-fixture'}
        return {'status': 200, 'body': raw, 'tls_verified': True, 'url': 'https://' + http.HOST + '/' + name,
                'headers': values}, contract

    def test_byte_security_cache_cookie_mime_and_tls_regressions_are_rejected(self):
        original, contract = self.response()
        http.exact(original, b'hello', 'index.html', 'edge', contract, None)
        variants = [('body', b'wrong'), ('status', 403), ('tls_verified', False)]
        for key, value in variants:
            changed = deepcopy(original)
            changed[key] = value
            with self.assertRaises(ArtifactError): http.exact(changed, b'hello', 'index.html', 'edge', contract, None)
        for key, value in [('content-security-policy', ''), ('cache-control', 'public'), ('set-cookie', 'fixture=1'),
                           ('content-type', 'text/plain'), ('content-encoding', ''), ('cf-ray', ''),
                           ('strict-transport-security', 'max-age=100')]:
            changed = deepcopy(original)
            changed['headers'][key] = value
            with self.assertRaises(ArtifactError): http.exact(changed, b'hello', 'index.html', 'edge', contract, None)

    def test_managed_security_text_has_only_its_explicit_separate_contract(self):
        response = {'status': 200, 'body': SECURITY, 'tls_verified': True,
                    'url': 'https://' + http.HOST + '/.well-known/security.txt',
                    'headers': {'content-type': 'text/plain', 'cf-ray': 'fixture', 'server': 'cloudflare'}}
        contract = http.security_headers({'.htaccess': ACCESS})
        http.exact(response, SECURITY, '.well-known/security.txt', 'edge', contract, source.digest(SECURITY))
        with self.assertRaises(ArtifactError): http.exact(response, SECURITY, '.well-known/security.txt', 'edge', contract, None)
        with self.assertRaises(ArtifactError): http.exact(response, SECURITY, 'robots.txt', 'edge', contract, source.digest(SECURITY))

    def test_header_continuations_and_unbounded_headers_fail(self):
        self.assertEqual(http.headers(b'HTTP/2 200\r\nContent-Type: text/html\r\n\r\n'), {'content-type': 'text/html'})
        for raw in [b'not HTTP', b'HTTP/2 200\r\n injected\r\n\r\n', b'x' * (128 * 1024 + 1)]:
            with self.assertRaises(ArtifactError): http.headers(raw)

    def test_only_transient_transport_errors_retry_and_the_connected_surface_is_checked(self):
        calls, exits, connected = [], [28, 56, 0], ['1.1.1.1']
        def runner(args, **options):
            calls.append((args, options))
            Path(args[args.index('--output') + 1]).write_bytes(b'ok')
            Path(args[args.index('--dump-header') + 1]).write_bytes(b'HTTP/2 200\r\nContent-Type: text/plain\r\n\r\n')
            return subprocess.CompletedProcess(args, exits.pop(0), ('200 0 ' + connected[0]).encode())
        client = http.HTTP('1.1.1.1', runner)
        self.assertEqual(client.get('/test', 'origin', retry=True)['body'], b'ok')
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][1]['env'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        self.assertIn('oss-singularity.io:443:1.1.1.1', calls[0][0])
        self.assertNotIn('--insecure', calls[0][0])
        exits[:] = [60, 0]
        with self.assertRaisesRegex(ArtifactError, 'http_transport_failed'):
            client.get('/test', 'origin', retry=True)
        self.assertEqual(exits, [0])
        with self.assertRaisesRegex(ArtifactError, 'http_surface_mismatch'): client.get('/test')
        exits[:] = [0]
        connected[0] = '8.8.8.8'
        with self.assertRaisesRegex(ArtifactError, 'http_surface_mismatch'): client.get('/test', 'origin')
        for path in ['//example.invalid/', '/test#fragment', '/test\nheader', '/back\\slash']:
            with self.assertRaisesRegex(ArtifactError, 'invalid_http_target'): client.get(path)

    def test_canonical_redirects_preserve_encoded_path_query_and_cannot_escape(self):
        class Client(http.HTTP):
            destination = 'https://' + http.HOST + '/guide/?oss_redirect_probe=1&literal=a%2Fb'
            def get(self, *args, **kwargs): return {'status': 301, 'headers': {'location': self.destination}}
        client = Client('1.1.1.1')
        client.redirects()
        for destination in ['https://example.invalid/guide/?oss_redirect_probe=1&literal=a%2Fb',
                            'https://' + http.HOST + '/guide/', 'http://' + http.HOST + '/guide/',
                            'https://user@' + http.HOST + '/guide/?oss_redirect_probe=1&literal=a%2Fb']:
            client.destination = destination
            with self.assertRaisesRegex(ArtifactError, 'redirect_mismatch'): client.redirects()

    def test_public_api_rejects_cached_or_unpublished_results(self):
        state, published = ['DYNAMIC'], ['published']
        class Client(http.HTTP):
            def get(self, path, **options):
                kind = 'mission' if '/missions?' in path else 'review' if '/reviews?' in path else 'field-note'
                body = {'items': [{'kind': kind, 'status': published[0], 'provenance': 'community'}], 'next_cursor': None}
                return {'status': 200, 'tls_verified': True, 'body': source.encode(body), 'headers': {
                    'cf-ray': 'fixture', 'content-type': 'application/json', 'cache-control': 'no-store',
                    'x-content-type-options': 'nosniff', 'x-robots-tag': 'noindex, nofollow',
                    'content-security-policy': "default-src 'none'; frame-ancestors 'none'", 'cf-cache-status': state[0]}}
        client = Client('1.1.1.1')
        self.assertEqual(len(client.public_api()), 3)
        state[0] = 'HIT'
        with self.assertRaisesRegex(ArtifactError, 'api_unverified'): client.public_api()
        state[0], published[0] = 'DYNAMIC', 'pending'
        with self.assertRaisesRegex(ArtifactError, 'api_unverified'): client.public_api()


class TransportTests(unittest.TestCase):
    def test_ssh_has_one_identity_no_agent_no_fallback_and_bounded_sanitized_responses(self):
        # Syntactically shaped public test bytes, never usable credentials.
        environ = {'STATIC_ORIGIN_IP': '1.1.1.1', 'STATIC_SSH_USER': 'fixture', 'STATIC_SSH_PORT': '21098',
                   'STATIC_SSH_HOST_KEY': 'ssh-ed25519 ' + base64.b64encode(
                       b'\0\0\0\x0bssh-ed25519\0\0\0 ' + b'x' * 32).decode(),
                   'STATIC_SSH_KEY': '-----BEGIN ' + 'OPENSSH PRIVATE KEY-----\n' + 'synthetic-public-fixture' * 12
                       + '\n-----END OPENSSH PRIVATE KEY-----\n', 'STATIC_RUNTIME_SHA256': 'a' * 64}
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            response = {'schema_version': 1, 'kind': 'static-remote-transition', 'target': transport.plan.TARGET,
                        'runtime_sha256': 'a' * 64, 'filesystem_only': True, 'publication_verified': False,
                        'deployment_authorized': False, 'baseline_commit': OLD,
                        'baseline_manifest_sha256': 'b' * 64, 'generation': 1,
                        'maintenance_pending': False, 'phase': 'empty'}
            def runner(args, **options):
                calls.append((args, options))
                os.write(options['stdout'], source.encode(response))
                return subprocess.CompletedProcess(args, 0)
            remote = transport.SSH(environ, Path(directory), runner)
            self.assertEqual(remote.status()['generation'], 1)
            args, options = calls[0]
            self.assertEqual(args[-2:], ['1.1.1.1', 'oss-static-release-v1'])
            for option in ['IdentityAgent=none', 'IdentitiesOnly=yes', 'ControlPath=none',
                           'StrictHostKeyChecking=yes', 'PasswordAuthentication=no', 'ClearAllForwardings=yes']:
                self.assertIn(option, args)
            self.assertEqual(options['env'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
            self.assertIs(options['preexec_fn'], transport.limit_response)
            for name in ['identity', 'known_hosts']:
                self.assertEqual((Path(directory) / name).stat().st_mode & 0o777, 0o600)
            self.assertFalse(list(Path(directory).glob('response-*')))
            response['runtime_sha256'] = 'c' * 64
            with self.assertRaisesRegex(ArtifactError, 'remote_identity_mismatch'): remote.status()
            response['runtime_sha256'], response['padding'] = 'a' * 64, 'x' * 17000
            with self.assertRaisesRegex(ArtifactError, 'remote_response_invalid'): remote.status()
            self.assertFalse(list(Path(directory).glob('response-*')))


if __name__ == '__main__':
    unittest.main()
