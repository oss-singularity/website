"""Offline tests for the promotion command's derivations and output contract."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import warnings
import zipfile

import commons_artifact as artifact
import commons_candidate as consumer
import commons_promotion as promotion
import commons_rehearsal as rehearsal
from release_source import module
from site_artifact import ArtifactError

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('cpc', Path(__file__).resolve().parent / 'commons-promotion.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
check_fixtures = module('commons_check_fixtures_cli', 'test-release-checks.py')

SHA = 'a' * 40
LIVE_SHA = '3' * 40
RUN, ATTEMPT, CANDIDATE, RECEIPT, SUITE, CHECK = 248, 2, 97, 98, 3333, 4444
VERSION = '11111111-1111-4111-8111-111111111111'
DEPLOYMENT = '22222222-2222-4222-8222-222222222222'
DATABASE = '44444444-4444-4444-8444-444444444444'
MARKER = 'PRIVATE_COMMONS_PROMOTION_CLI_FIXTURE_9147'
ENV = {'GH_TOKEN': 'read-' + MARKER, 'GH_POLICY_TOKEN': 'policy-' + MARKER,
       'COMMONS_CF_TOKEN': 'cf-' + MARKER, 'COMMONS_ACCOUNT_ID': 'e' * 32,
       'COMMONS_ZONE_ID': 'f' * 32, 'COMMONS_D1_ID': DATABASE, 'STATIC_ORIGIN_IP': '1.2.3.4'}


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
    observation = {'active_version': 'v-live', 'latest_version_id': 'v-live',
                   'versions': {'v-live': {'number': 5, 'metadata': {}, 'annotations': {}}},
                   'deployments': [{'id': 'dep-live', 'strategy': 'percentage',
                                    'versions': [{'version_id': 'v-live', 'percentage': 100}]}]}
    detail = {'resources': {
        'bindings': [
            {'name': 'DB', 'type': 'd1', 'id': 'd1-uuid'},
            {'name': 'ADMIN_TOKEN', 'type': 'secret_text'}, {'name': 'GITHUB_READ_TOKEN', 'type': 'secret_text'},
            {'name': 'RELEASE_SHA', 'type': 'plain_text', 'text': '1' * 40},
        ],
        'script': {'modules': [{'name': 'worker.mjs'}]},
        'script_runtime': {'compatibility_date': '2026-09-04'},
    }}

    def test_derive_plan_inherits_bindings_and_reads_current_identity(self):
        plan = cli.derive_plan(self.observation, self.detail, 'message', 'tag')
        self.assertEqual(plan['predecessor_version'], 'v-live')
        self.assertEqual(plan['predecessor_deployment_id'], 'dep-live')
        self.assertEqual(plan['predecessor_generation'], 5)
        self.assertEqual(plan['release_sha'], '1' * 40)
        self.assertEqual(plan['bindings'], [
            {'name': 'DB', 'type': 'inherit'}, {'name': 'ADMIN_TOKEN', 'type': 'inherit'}, {'name': 'GITHUB_READ_TOKEN', 'type': 'inherit'},
            {'name': 'RELEASE_SHA', 'type': 'inherit'}])
        self.assertEqual(plan['installed_bindings'], self.detail['resources']['bindings'])
        self.assertEqual(plan['compatibility_date'], '2026-09-04')

    def test_derive_plan_refuses_unreadable_provider_state(self):
        for detail in [{}, {'resources': {}},
                       {'resources': {'bindings': [], 'script_runtime': {}}}]:
            with self.assertRaises(ArtifactError):
                cli.derive_plan(self.observation, detail, 'm', 't')
        for observation in [
                {**self.observation, 'deployments': []},
                {**self.observation, 'versions': {}},
                {key: value for key, value in self.observation.items() if key != 'deployments'}]:
            with self.assertRaisesRegex(ArtifactError, 'provider_state_unverified'):
                cli.derive_plan(observation, self.detail, 'm', 't')


class OutputTests(unittest.TestCase):
    def test_missing_environment_reports_missing_credential_without_secret(self):
        output = io.StringIO()
        secret = 'super-op-secret-token-value'
        with redirect_stdout(output):
            code = cli.cli(['--packet', 'x', '--commit', 'a' * 40, '--message', 'm',
                            '--tag', 't', ], environ={'COMMONS_CF_TOKEN': secret})
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['error'], 'missing_credential')
        self.assertNotIn(secret, output.getvalue())

    def test_mode_and_argument_contracts(self):
        for argv in [[], ['--packet', 'x', '--from-rehearsal', '1', '1', SHA, '--message', 'm', '--tag', 't'],
                     ['--packet', 'x', '--message', 'm', '--tag', 't'],
                     ['--packet', 'x', '--commit', 'a' * 40, '--message', 'm', '--tag', 't', '--extra'],
                     ['--from-rehearsal', '1', '1', '--message', 'm', '--tag', 't']]:
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli.cli(argv, dict(ENV))
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())['error'], 'invalid_arguments')
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.cli(['--from-rehearsal', 'x', '1', SHA, '--message', 'm', '--tag', 't'], dict(ENV))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['error'], 'invalid_identity')


def zipped(members):
    output = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        warnings.simplefilter('ignore', UserWarning)
        for name, content in members:
            archive.writestr(name, content)
    return output.getvalue()


def uploaded(number, role, raw):
    return {'id': number, 'name': f'commons-{role}-{SHA}-{RUN}-{ATTEMPT}',
            'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'expired': False,
            'expires_at': (datetime.now(timezone.utc) + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'workflow_run': {'id': RUN, 'repository_id': 1351274990, 'head_repository_id': 1351274990,
                             'head_branch': 'main', 'head_sha': SHA}, 'unrelated': MARKER}


def schema_rows():
    _files, migrations = artifact.source_inputs(ROOT / 'services/commons')
    with sqlite3.connect(':memory:') as database:
        database.row_factory = sqlite3.Row
        for name in sorted(migrations):
            database.executescript(migrations[name].decode())
        return [dict(row) for row in database.execute(artifact.SCHEMA_QUERY)]


def source_modules():
    files, _migrations = artifact.source_inputs(ROOT / 'services/commons')
    return files


def multipart_form(files):
    """The live provider's multipart script form for the given module bytes."""
    boundary = '----CfWorkerUploadFixture'
    parts = b''
    for name in sorted(files):
        parts += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{name}"'
                  f'\r\nContent-Type: application/javascript+module\r\n\r\n').encode() + files[name] + b'\r\n'
    return parts + f'--{boundary}--'.encode()


def module_content():
    return multipart_form(source_modules())


def git_objects(values, commit, tree_sha, files):
    """Synthetic Git commit/tree objects binding the given module bytes by blob id."""
    entries = [{'path': 'services/commons/' + name, 'mode': '100644', 'type': 'blob',
                'sha': hashlib.sha1(b'blob ' + str(len(files[name])).encode()
                                    + b'\0' + files[name]).hexdigest()}
               for name in sorted(files)]
    values[consumer.checks.BASE + '/git/commits/' + commit] = {'sha': commit, 'tree': {'sha': tree_sha}}
    values[consumer.checks.BASE + '/git/trees/' + tree_sha + '?recursive=1'] = {
        'sha': tree_sha, 'truncated': False, 'tree': entries}


class FakeGitHub:
    def __init__(self, values, archives):
        self.values, self.archives, self.calls = values, archives, []

    def get(self, route, _environ=None):
        assert consumer.CommonsGitHub(ENV).read_route(route), route
        self.calls.append(route)
        return deepcopy(self.values[route])

    def download(self, number, _limit):
        self.calls.append('download:' + str(number))
        return self.archives[number]


class FakeProvider:
    """Synthetic target with live response shapes and single mutations."""
    def __init__(self, rows, installed, content, routes, *, latest=VERSION,
                 foreign_latest_after_stage=False):
        self.active = VERSION
        self.latest = latest
        self.installed = installed
        self.content = content
        self.rows = rows
        self.routes = routes
        self.calls = []
        self.staged_detail = None
        self.staged = None
        self.staged_annotations = None
        self.foreign_latest_after_stage = foreign_latest_after_stage
        self._foreign_applied = False

    def observe(self):
        self.calls.append('observe')
        versions = {VERSION: {'number': 3, 'metadata': {}, 'annotations': {}}}
        latest = self.latest
        if self.staged is not None:
            versions[self.staged] = {'number': 4, 'metadata': {},
                                     'annotations': {'workers/message': 'm', 'workers/tag': 't'}}
            latest = self.staged
            if self.foreign_latest_after_stage and not self._foreign_applied:
                self._foreign_applied = True
                versions['foreign-pending-upload'] = {'number': 5, 'metadata': {}, 'annotations': {}}
                latest = 'foreign-pending-upload'
        return {'active_version': self.active, 'versions': versions,
                'latest_version_id': latest,
                'account_id': ENV['COMMONS_ACCOUNT_ID'], 'zone_id': ENV['COMMONS_ZONE_ID'],
                'script_name': cli.SCRIPT_NAME,
                'deployments': [{'id': DEPLOYMENT, 'strategy': 'percentage',
                                 'versions': [{'version_id': self.active, 'percentage': 100}]}],
                'routes': [dict(route) for route in self.routes],
                'schedules': [{'cron': '17 * * * *', 'created_on': '2026-09-05T08:07:13.971361Z'}],
                'subdomain': 'mail-85f', 'd1_schema_fingerprint': 'fp'}

    def version_detail(self, version_id):
        self.calls.append('detail:' + version_id)
        if version_id == self.staged:
            return {'annotations': dict(self.staged_annotations),
                    'resources': self.staged_detail['resources']}
        return {'resources': {'bindings': [dict(item) for item in self.installed],
                'script': {'etag': 'd' * 64, 'handlers': ['fetch', 'scheduled'],
                           'named_handlers': [{'name': 'cleanup'}, {'name': 'safeUrl'}]},
                'script_runtime': {'compatibility_date': '2026-09-04', 'usage_model': 'standard'}}}

    def script_settings(self):
        self.calls.append('settings')
        return {'annotations': {'workers/triggered_by': 'version_upload'},
                'bindings': [dict(item) for item in self.installed],
                'compatibility_date': '2026-09-04', 'compatibility_flags': [], 'logpush': False,
                'placement': {}, 'tags': ['commons', 'oss-singularity'], 'tail_consumers': [],
                'usage_model': 'standard'}

    def script_subdomain(self):
        self.calls.append('subdomain')
        return {'enabled': False, 'previews_enabled': False}

    def schema_rows(self, _query):
        self.calls.append('schema')
        return [{'success': True, 'results': [dict(row) for row in self.rows]}]

    def script_content(self):
        self.calls.append('content')
        return self.content

    def stage_version(self, content, commit, message, tag, bindings=None,
                      main_module='worker.mjs', compatibility_date=None):
        self.calls.append('stage')
        names = cli.packet_modules(content)
        assert set(names) == set(artifact.MODULES)
        self.staged = 'staged-' + commit[:4]
        self.staged_annotations = {'workers/message': message, 'workers/tag': tag}
        resolved = []
        for binding in bindings:
            if binding['type'] == 'inherit':
                resolved.append(dict(next(item for item in self.installed
                                          if item['name'] == binding['name'])))
            else:
                resolved.append(dict(binding))
        self.staged_detail = {'resources': {
            'bindings': resolved,
            'script': {'etag': 'e' * 64, 'handlers': ['fetch', 'scheduled'],
                       'named_handlers': [{'name': 'cleanup'}, {'name': 'safeUrl'}]},
            'script_runtime': {'compatibility_date': compatibility_date}}}
        return self.staged

    def activate_version(self, version_id, _message):
        self.calls.append('activate:' + version_id)
        self.active = version_id


class _IntentOpener:
    """Deployment-API transport script; the confirmation GET echoes the posted payload."""
    def __init__(self, final, sha):
        state, description = {'promoted': ('success', promotion.PROMOTED),
                              'rolled_back': ('failure', promotion.ROLLED_BACK),
                              'unresolved': ('error', promotion.UNRESOLVED)}[final]
        self.sha = sha
        self.posted = None
        self.confirmations = [
            {'state': 'in_progress', 'description': promotion.IN_PROGRESS},
            {'state': state, 'description': description},
        ]

    def open(self, request, timeout):
        url, method = request.full_url, request.get_method()
        if method == 'GET' and '/deployments?' in url:
            return _FakeResponse([], url)
        if method == 'POST' and url.endswith('/deployments'):
            self.posted = json.loads(request.data.decode())['payload']
            return _FakeResponse({'id': 7}, url, status=201)
        if method == 'GET' and url.endswith('/deployments/7'):
            return _FakeResponse({'id': 7, 'sha': self.sha, 'task': promotion.TASK,
                                  'environment': promotion.ENVIRONMENT, 'payload': self.posted}, url)
        if method == 'POST' and url.endswith('/statuses'):
            return _FakeResponse({'id': 71}, url, status=201)
        if method == 'GET' and url.endswith('/statuses?per_page=1&page=1'):
            return _FakeResponse([self.confirmations.pop(0)], url)
        raise AssertionError(method + ' ' + url)


class StageFormTests(unittest.TestCase):
    def setUp(self):
        packet = Path(tempfile.mkdtemp(prefix='oss-stage-form-')) / 'commons.json'
        self.addCleanup(shutil.rmtree, packet.parent, ignore_errors=True)
        artifact.create(ROOT / 'services/commons', SHA, packet)
        self.packet = packet.read_bytes()

    def test_stage_form_carries_one_real_module_part_per_module(self):
        form = cli.stage_form(self.packet, SHA)
        names = cli.packet_modules(form)
        self.assertEqual(set(names), set(artifact.MODULES))
        for name in names:
            marker = (f'Content-Disposition: form-data; name="{name}"; filename="{name}"'
                      f'\r\nContent-Type: application/javascript+module').encode()
            self.assertIn(marker, form)
        (files, _migrations) = artifact.source_inputs(ROOT / 'services/commons')
        for name, raw in files.items():
            self.assertIn(raw, form)
        self.assertNotIn(b'"descriptor"', form)

    def test_stage_form_refuses_a_packet_bound_to_another_commit(self):
        with self.assertRaisesRegex(ArtifactError, 'descriptor_mismatch'):
            cli.stage_form(self.packet, 'b' * 40)


class FromRehearsalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='oss-commons-promotion-cli-')
        cls.addClassCleanup(temporary.cleanup)
        root = Path(temporary.name)
        packet, receipt = root / 'commons.json', root / 'receipt.json'
        artifact.create(ROOT / 'services/commons', SHA, packet)
        cls.packet = packet.read_bytes()
        cls.candidate_zip = zipped([('commons.json', cls.packet)])
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_SERVER_URL': 'https://github.com',
               'GITHUB_API_URL': 'https://api.github.com', 'GITHUB_REPOSITORY': 'oss-singularity/website',
               'GITHUB_REPOSITORY_ID': '1351274990', 'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main',
               'GITHUB_REF_PROTECTED': 'true', 'GITHUB_SHA': SHA, 'GITHUB_WORKFLOW_SHA': SHA,
               'GITHUB_WORKFLOW_REF': rehearsal.WORKFLOW_REF, 'GITHUB_RUN_ID': str(RUN),
               'GITHUB_RUN_ATTEMPT': str(ATTEMPT)}
        def fetch(route, _environ):
            if route == consumer.checks.MAIN:
                return {'name': 'main', 'protected': True, 'commit': {'sha': SHA}}
            return uploaded(CANDIDATE, 'candidate', cls.candidate_zip)
        rehearsal.receipt(SHA, str(CANDIDATE), hashlib.sha256(cls.candidate_zip).hexdigest(),
                          packet, ROOT / 'services/commons', receipt, env, fetch)
        cls.receipt_zip = zipped([('receipt.json', receipt.read_bytes())])
        cls.rows = schema_rows()
        cls.installed = [
            {'name': 'ADMIN_TOKEN', 'type': 'secret_text'},
            {'database_id': DATABASE, 'id': DATABASE, 'name': 'DB', 'type': 'd1'},
            {'name': 'GITHUB_READ_TOKEN', 'type': 'secret_text'},
            {'name': 'IP_HMAC_SECRET', 'type': 'secret_text'},
            {'name': 'PUBLIC_ORIGIN', 'text': 'https://oss-singularity.io', 'type': 'plain_text'},
            {'name': 'RELEASE_SHA', 'text': LIVE_SHA, 'type': 'plain_text'},
        ]
        cls.routes = [{'id': 'c' * 32, 'pattern': 'oss-singularity.io/api/*',
                       'script': cli.SCRIPT_NAME, 'request_limit_fail_open': False}]
        cls.modules = source_modules()
        cls.content = multipart_form(cls.modules)

    def setUp(self):
        values = check_fixtures.fixture()
        repo = {'id': 1351274990, 'full_name': 'oss-singularity/website', 'fork': False}
        app = {'id': 15368, 'slug': 'github-actions', 'owner': {'login': 'github'}}
        run = {'id': RUN, 'run_attempt': ATTEMPT, 'workflow_id': 356455115, 'check_suite_id': SUITE,
               'path': '.github/workflows/commons-release-rehearsal.yml', 'event': 'push',
               'head_branch': 'main', 'head_sha': SHA, 'status': 'completed', 'conclusion': 'success',
               'repository': deepcopy(repo), 'head_repository': deepcopy(repo), 'unrelated': MARKER}
        route = consumer.candidate.run_route(RUN)
        values[consumer.WORKFLOW_ROUTE] = {'id': 356455115, 'path': consumer.WORKFLOW_PATH, 'state': 'active'}
        values[consumer.WORKFLOW_ROUTE + '/runs?head_sha=' + SHA + '&per_page=100&page=1'] = \
            check_fixtures.collection('workflow_runs', [deepcopy(run)])
        values[route], values[route + '/attempts/2'] = deepcopy(run), deepcopy(run)
        steps = re.findall(r'^      - name: (.+)$', (ROOT / consumer.WORKFLOW_PATH).read_text(), re.MULTILINE)
        job = {'id': 5555, 'run_id': RUN, 'run_attempt': ATTEMPT, 'head_sha': SHA, 'name': 'candidate',
               'status': 'completed', 'conclusion': 'success',
               'check_run_url': consumer.checks.API + consumer.checks.BASE + '/check-runs/' + str(CHECK),
               'steps': [{'number': index, 'name': name, 'status': 'completed', 'conclusion': 'success'}
                         for index, name in enumerate(steps, 1)]}
        values[route + '/attempts/2/jobs?per_page=100&page=1'] = check_fixtures.collection('jobs', [job])
        values[consumer.checks.BASE + '/check-suites/' + str(SUITE)] = {
            'id': SUITE, 'app': app, 'repository': repo, 'head_sha': SHA, 'head_branch': 'main',
            'status': 'completed', 'conclusion': 'success'}
        values[consumer.checks.BASE + '/check-runs/' + str(CHECK)] = {
            'id': CHECK, 'name': 'candidate', 'head_sha': SHA, 'check_suite': {'id': SUITE},
            'app': app, 'status': 'completed', 'conclusion': 'success'}
        values[consumer.candidate.artifact_route(CANDIDATE)] = uploaded(CANDIDATE, 'candidate', self.candidate_zip)
        values[consumer.candidate.artifact_route(RECEIPT)] = uploaded(RECEIPT, 'rehearsal-receipt', self.receipt_zip)
        values[route + '/artifacts?per_page=100&page=1'] = check_fixtures.collection('artifacts',
            [deepcopy(values[consumer.candidate.artifact_route(number)]) for number in [CANDIDATE, RECEIPT]])
        names = {'services/commons/' + name for name in artifact.MODULES | artifact.LOCAL_MODULES}
        names |= {'services/commons/migrations/' + name for name in artifact.MIGRATIONS} | {consumer.WORKFLOW_PATH}
        entries = []
        for name in sorted(names):
            raw = (ROOT / name).read_bytes()
            entries.append({'path': name, 'mode': '100644', 'type': 'blob',
                            'sha': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()})
        values[consumer.checks.BASE + '/git/commits/' + SHA] = {'sha': SHA, 'tree': {'sha': 'd' * 40}}
        values[consumer.checks.BASE + '/git/trees/' + 'd' * 40 + '?recursive=1'] = {
            'sha': 'd' * 40, 'truncated': False, 'tree': entries}
        # The live predecessor claims LIVE_SHA; its own Git objects bind the
        # installed module bytes independently of those bytes themselves.
        git_objects(values, LIVE_SHA, '5' * 40, self.modules)
        self.values = values
        self.archives = {CANDIDATE: self.candidate_zip, RECEIPT: self.receipt_zip}

    def provider(self, **changes):
        return FakeProvider(self.rows, self.installed, self.content, self.routes, **changes)

    def intent(self, final='promoted'):
        return promotion.PromotionIntent(ENV, _IntentOpener(final, SHA))

    def invoke(self, provider, *, accept=None, argv=None, environ=None, final='promoted'):
        github = FakeGitHub(self.values, self.archives)
        arguments = argv or ['--from-rehearsal', str(RUN), str(ATTEMPT), SHA,
                             '--message', 'Promote the candidate', '--tag', 'promotion-tag']
        result = cli.main(argv=arguments, environ=environ or dict(ENV), github=github,
                          fetch=github.get, provider=provider, intent=self.intent(final),
                          accept=accept or (lambda sha: True))
        return result, github

    def test_full_wiring_promotes_the_verified_candidate(self):
        provider = self.provider()
        result, github = self.invoke(provider)
        self.assertTrue(result['promoted'])
        self.assertEqual(result['deployment'], 7)
        stages = [call for call in provider.calls if call == 'stage']
        self.assertEqual(len(stages), 1)
        self.assertIn('download:' + str(CANDIDATE), github.calls)
        self.assertIn('download:' + str(RECEIPT), github.calls)
        self.assertIn('content', provider.calls)
        self.assertIn('activate:' + provider.staged, provider.calls)
        self.assertEqual(result['staged_version'], provider.staged)
        staged_bindings = provider.staged_detail['resources']['bindings']
        by_name = {item['name']: item for item in staged_bindings}
        self.assertEqual(by_name['RELEASE_SHA'], {'name': 'RELEASE_SHA', 'text': SHA, 'type': 'plain_text'})
        self.assertEqual(by_name['DB'], {'database_id': DATABASE, 'id': DATABASE, 'name': 'DB', 'type': 'd1'})
        self.assertEqual(by_name['ADMIN_TOKEN'], {'name': 'ADMIN_TOKEN', 'type': 'secret_text'})
        self.assertEqual(by_name['IP_HMAC_SECRET'], {'name': 'IP_HMAC_SECRET', 'type': 'secret_text'})
        self.assertEqual(by_name['PUBLIC_ORIGIN'],
                         {'name': 'PUBLIC_ORIGIN', 'text': 'https://oss-singularity.io', 'type': 'plain_text'})
        self.assertEqual(len(staged_bindings), 6)

    def test_unowned_pending_versions_refuse_before_the_intent(self):
        provider = self.provider(latest='99999999-9994-9999-8999-999999999999')
        github = FakeGitHub(self.values, self.archives)
        with self.assertRaisesRegex(ArtifactError, 'unowned_pending_version'):
            cli.main(argv=['--from-rehearsal', str(RUN), str(ATTEMPT), SHA, '--message', 'm',
                           '--tag', 't'], environ=dict(ENV), github=github, fetch=github.get,
                     provider=provider, intent=self.intent(), accept=lambda sha: True)
        self.assertNotIn('stage', provider.calls)

    def test_failed_rollback_acceptance_closes_unresolved_and_keeps_predecessor(self):
        provider = self.provider()
        with self.assertRaisesRegex(ArtifactError, 'rollback_acceptance_failed'):
            self.invoke(provider, accept=lambda sha: False, final='unresolved')
        self.assertEqual(provider.active, VERSION)
        self.assertIn('activate:' + VERSION, provider.calls)
        self.assertIn('activate:' + provider.staged, provider.calls)

    def test_candidate_commit_reuse_is_refused_by_the_planner(self):
        installed = [dict(item) for item in self.installed]
        next(item for item in installed if item['name'] == 'RELEASE_SHA')['text'] = SHA
        provider = FakeProvider(self.rows, installed, self.content, self.routes)
        github = FakeGitHub(self.values, self.archives)
        with self.assertRaisesRegex(ArtifactError, 'candidate_commit_reused'):
            cli.main(argv=['--from-rehearsal', str(RUN), str(ATTEMPT), SHA, '--message', 'm',
                           '--tag', 't'], environ=dict(ENV), github=github, fetch=github.get,
                     provider=provider, intent=self.intent(), accept=lambda sha: True)

    def test_ambiguous_rehearsal_artifacts_refuse_before_any_download(self):
        route = consumer.candidate.run_route(RUN) + '/artifacts?per_page=100&page=1'
        extra = deepcopy(self.values[route]['artifacts'][0])
        extra['id'] = 424242
        extra['name'] = f'commons-candidate-{SHA}-{RUN}-{ATTEMPT}'
        self.values[route]['artifacts'].append(extra)
        self.values[route]['total_count'] += 1
        provider = self.provider()
        github = FakeGitHub(self.values, self.archives)
        with self.assertRaisesRegex(ArtifactError, 'invalid_rehearsal_artifacts'):
            cli.main(argv=['--from-rehearsal', str(RUN), str(ATTEMPT), SHA, '--message', 'm',
                           '--tag', 't'], environ=dict(ENV), github=github, fetch=github.get,
                     provider=provider, intent=self.intent(), accept=lambda sha: True)
        self.assertNotIn('download:' + str(CANDIDATE), github.calls)
        self.assertNotIn('stage', provider.calls)

    def test_predecessor_drift_under_unchanged_release_sha_refuses(self):
        provider = self.provider()
        real = self.modules['worker.mjs']
        provider.content = provider.content.replace(
            real, real + b'\n// provider drift retaining the claimed RELEASE_SHA\n', 1)
        github = FakeGitHub(self.values, self.archives)
        with self.assertRaisesRegex(ArtifactError, 'predecessor_source_mismatch'):
            cli.main(argv=['--from-rehearsal', str(RUN), str(ATTEMPT), SHA, '--message', 'm',
                           '--tag', 't'], environ=dict(ENV), github=github, fetch=github.get,
                     provider=provider, intent=self.intent(), accept=lambda sha: True)
        # The drifted bytes never became a baseline, a rollback target or a
        # staged upload; no promotion intent was recorded.
        self.assertIn('content', provider.calls)
        self.assertNotIn('stage', provider.calls)
        self.assertNotIn('activate:', ' '.join(provider.calls))
        self.assertTrue(any(route.endswith('/git/commits/' + LIVE_SHA) for route in github.calls))

    def test_missing_predecessor_git_evidence_fails_closed(self):
        provider = self.provider()
        github = FakeGitHub(self.values, self.archives)
        class NoPredecessorEvidence(FakeGitHub):
            def get(self, route, _environ=None):
                if route.endswith('/git/commits/' + LIVE_SHA):
                    raise ArtifactError('github_read_failed')
                return FakeGitHub.get(self, route, _environ)
        blocked = NoPredecessorEvidence(self.values, self.archives)
        with self.assertRaisesRegex(ArtifactError, 'github_read_failed'):
            cli.main(argv=['--from-rehearsal', str(RUN), str(ATTEMPT), SHA, '--message', 'm',
                           '--tag', 't'], environ=dict(ENV), github=blocked, fetch=blocked.get,
                     provider=provider, intent=self.intent(), accept=lambda sha: True)
        self.assertNotIn('stage', provider.calls)

    def test_older_predecessor_profile_promotes_additive_modules(self):
        older = {'worker.mjs', 'security.mjs', 'identity.mjs', 'participations.mjs',
                 'activity.mjs', 'work-items.mjs'}
        older_commit = '6' * 40
        installed = [dict(item) for item in self.installed]
        next(item for item in installed if item['name'] == 'RELEASE_SHA')['text'] = older_commit
        values = deepcopy(self.values)
        git_objects(values, older_commit, '7' * 40,
                    {name: self.modules[name] for name in older})
        provider = FakeProvider(self.rows, installed,
                                multipart_form({name: self.modules[name] for name in older}),
                                self.routes)
        github = FakeGitHub(values, self.archives)
        result = cli.main(argv=['--from-rehearsal', str(RUN), str(ATTEMPT), SHA, '--message', 'm',
                                '--tag', 't'], environ=dict(ENV), github=github, fetch=github.get,
                          provider=provider, intent=self.intent(), accept=lambda sha: True)
        self.assertTrue(result['promoted'])
        self.assertIn('activate:' + provider.staged, provider.calls)
        self.assertTrue(any(route.endswith('/git/commits/' + older_commit)
                            for route in github.calls))

    def test_stale_predecessor_after_intent_refuses_without_overrunning(self):
        provider = self.provider()
        github = FakeGitHub(self.values, self.archives)
        records = self.intent('unresolved')
        inner_start = records.start
        def intervening_start(sha, payload):
            provider.active = 'intervening-operator-version'
            provider.calls.append('external-activation:intervening-operator-version')
            return inner_start(sha, payload)
        records.start = intervening_start
        with self.assertRaisesRegex(ArtifactError, 'stale_preconditions'):
            cli.main(argv=['--from-rehearsal', str(RUN), str(ATTEMPT), SHA, '--message', 'm',
                           '--tag', 't'], environ=dict(ENV), github=github, fetch=github.get,
                     provider=provider, intent=records, accept=lambda sha: True)
        # A privileged operator activated another version between planning and
        # staging; the engine refused instead of overrunning it.
        self.assertNotIn('stage', provider.calls)
        self.assertNotIn('activate:', ' '.join(provider.calls))
        self.assertEqual(provider.active, 'intervening-operator-version')

    def test_foreign_upload_while_staging_aborts_before_activation(self):
        provider = self.provider(foreign_latest_after_stage=True)
        result, github = self.invoke(provider, final='rolled_back')
        self.assertFalse(result['promoted'])
        self.assertEqual(result['error'], 'stale_preconditions')
        self.assertEqual(provider.active, VERSION)
        self.assertIn('stage', provider.calls)
        self.assertNotIn('activate:' + provider.staged, provider.calls)


class _FakeResponse:
    def __init__(self, value, url, status=200):
        self.raw = promotion.encode(value) if not isinstance(value, bytes) else value
        self.url, self.status = url, status

    def geturl(self):
        return self.url

    def read(self, limit=-1):
        return self.raw[:limit] if limit > 0 else self.raw

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class WorkflowContractTests(unittest.TestCase):
    """The dispatch workflow passes its inputs as data, never as shell source."""

    WORKFLOW = ROOT / '.github/workflows/commons-promotion.yml'

    def run_scripts(self):
        """Every literal `run: |` block of the workflow, dedented to source."""
        lines = self.WORKFLOW.read_text().splitlines()
        blocks, index = [], 0
        while index < len(lines):
            line = lines[index]
            lead = len(line) - len(line.lstrip())
            if line.strip() == 'run: |':
                block = []
                index += 1
                while index < len(lines):
                    inner = lines[index]
                    inner_lead = len(inner) - len(inner.lstrip())
                    if inner.strip() and inner_lead <= lead:
                        break
                    block.append(inner[lead + 2:])
                    index += 1
                blocks.append('\n'.join(block))
            else:
                index += 1
        return blocks

    def test_dispatch_inputs_enter_only_as_fixed_environment_names(self):
        for script in self.run_scripts():
            self.assertNotIn('${{', script)
        text = self.WORKFLOW.read_text()
        for name in ['run_id', 'run_attempt', 'commit', 'message', 'tag']:
            self.assertIn(f'PROMOTION_{name.upper()}: ${{{{ inputs.{name} }}}}', text)
        script = next(block for block in self.run_scripts() if 'commons-promotion.py' in block)
        for name in ['RUN_ID', 'RUN_ATTEMPT', 'COMMIT', 'MESSAGE', 'TAG']:
            self.assertIn(f'"$PROMOTION_{name}"', script)

    def test_hostile_dispatch_inputs_reach_the_command_as_data(self):
        script = next(block for block in self.run_scripts() if 'commons-promotion.py' in block)
        hostile = {
            'PROMOTION_RUN_ID': '35369851512 $(printf R3_PARAMETER_SENTINEL >&2)',
            'PROMOTION_RUN_ATTEMPT': '1`printf R3_BACKTICK_SENTINEL >&2`',
            'PROMOTION_COMMIT': 'a"' * 5 + '$(printf R3_COMMIT_SENTINEL >&2)',
            'PROMOTION_MESSAGE': 'release $(printf R3_MESSAGE_SENTINEL >&2) `printf R3_ALT_SENTINEL >&2`'
                                 ' "double" \'single\' $HOME $PATH\nsecond `pwd` line',
            'PROMOTION_TAG': 'tag-"quoted"-$(pwd)-`pwd`\nline 2 $USER',
        }
        with tempfile.TemporaryDirectory(prefix='oss-promotion-workflow-') as folder:
            root = Path(folder)
            capture, report = root / 'argv.bin', root / 'commons-promotion-report.json'
            stub = root / 'python3'
            stub.write_text('#!/bin/sh\nprintf "%s\\0" "$@" > "$FAKE_PYTHON_CAPTURE"\n'
                            'echo workflow-report-marker\n')
            stub.chmod(0o755)
            environment = dict(os.environ)
            environment.update(hostile)
            environment['PATH'] = str(root) + os.pathsep + environment.get('PATH', '')
            environment['RUNNER_TEMP'] = str(root)
            environment['FAKE_PYTHON_CAPTURE'] = str(capture)
            completed = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                                       env=environment, cwd=root, timeout=60)
            captured_argv = capture.read_bytes().split(b'\0')[:-1]
            captured_report = report.read_text()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        # No substitution, backtick execution or expansion happened anywhere.
        self.assertEqual(completed.stderr, '')
        self.assertEqual(completed.stdout, '')
        expected = ['scripts/commons-promotion.py', '--from-rehearsal',
                    hostile['PROMOTION_RUN_ID'], hostile['PROMOTION_RUN_ATTEMPT'],
                    hostile['PROMOTION_COMMIT'], '--message', hostile['PROMOTION_MESSAGE'],
                    '--tag', hostile['PROMOTION_TAG']]
        self.assertEqual([value.encode() for value in expected], captured_argv)
        self.assertEqual(captured_report, 'workflow-report-marker\n')


if __name__ == '__main__':
    unittest.main()
