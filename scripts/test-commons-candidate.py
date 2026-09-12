"""Real producer packets and adversarial completed-run fixtures, entirely offline."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
import warnings
import zipfile

import commons_artifact as artifact
import commons_candidate as api
import commons_rehearsal as rehearsal
from release_source import module

ROOT = Path(__file__).resolve().parent.parent
cli = module('commons_candidate_cli_tests', 'commons-candidate.py')
check_fixtures = module('commons_check_fixtures', 'test-release-checks.py')
SHA, TREE = 'a' * 40, 'd' * 40
RUN, ATTEMPT, CANDIDATE, RECEIPT, SUITE, CHECK = 248, 2, 97, 98, 3333, 4444
MARKER = 'PRIVATE_COMMONS_CANDIDATE_FIXTURE_8173'
ENV = {'GH_TOKEN': 'read-' + MARKER, 'GH_POLICY_TOKEN': 'policy-' + MARKER}


def zipped(members, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(output, 'w', compression=compression) as archive:
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


class CommonsCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='oss-commons-producer-')
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
            if route == api.checks.MAIN:
                return {'name': 'main', 'protected': True, 'commit': {'sha': SHA}}
            return uploaded(CANDIDATE, 'candidate', cls.candidate_zip)
        rehearsal.receipt(SHA, str(CANDIDATE), hashlib.sha256(cls.candidate_zip).hexdigest(),
                          packet, ROOT / 'services/commons', receipt, env, fetch)
        cls.receipt_value = json.loads(receipt.read_bytes())
        cls.receipt_zip = zipped([('receipt.json', receipt.read_bytes())])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oss-commons-candidate-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        shutil.copytree(ROOT / 'services/commons', self.source)
        self.candidate, self.receipt, self.output = [self.root / name for name in ['candidate.zip', 'receipt.zip', 'verified.json']]
        self.candidate.write_bytes(self.candidate_zip)
        self.receipt.write_bytes(self.receipt_zip)
        self.values, self.calls = self.fixture(), []

    def fixture(self):
        values = check_fixtures.fixture()
        repo = {'id': 1351274990, 'full_name': 'oss-singularity/website', 'fork': False}
        app = {'id': 15368, 'slug': 'github-actions', 'owner': {'login': 'github'}}
        run = {'id': RUN, 'run_attempt': ATTEMPT, 'workflow_id': 356455115, 'check_suite_id': SUITE,
               'path': '.github/workflows/commons-release-rehearsal.yml', 'event': 'push',
               'head_branch': 'main', 'head_sha': SHA, 'status': 'completed', 'conclusion': 'success',
               'repository': deepcopy(repo), 'head_repository': deepcopy(repo), 'unrelated': MARKER}
        route = api.candidate.run_route(RUN)
        values[api.WORKFLOW_ROUTE] = {'id': 356455115, 'path': api.WORKFLOW_PATH, 'state': 'active'}
        values[api.WORKFLOW_ROUTE + '/runs?head_sha=' + SHA + '&per_page=100&page=1'] = check_fixtures.collection('workflow_runs', [deepcopy(run)])
        values[route], values[route + '/attempts/2'] = deepcopy(run), deepcopy(run)
        steps = re.findall(r'^      - name: (.+)$', (ROOT / api.WORKFLOW_PATH).read_text(), re.MULTILINE)
        job = {'id': 5555, 'run_id': RUN, 'run_attempt': ATTEMPT, 'head_sha': SHA, 'name': 'candidate',
               'status': 'completed', 'conclusion': 'success',
               'check_run_url': api.checks.API + api.BASE + '/check-runs/' + str(CHECK),
               'steps': [{'number': index, 'name': name, 'status': 'completed', 'conclusion': 'success'}
                         for index, name in enumerate(steps, 1)]}
        values[route + '/attempts/2/jobs?per_page=100&page=1'] = check_fixtures.collection('jobs', [job])
        values[api.BASE + '/check-suites/' + str(SUITE)] = {'id': SUITE, 'app': app, 'repository': repo,
            'head_sha': SHA, 'head_branch': 'main', 'status': 'completed', 'conclusion': 'success'}
        values[api.BASE + '/check-runs/' + str(CHECK)] = {'id': CHECK, 'name': 'candidate', 'head_sha': SHA,
            'check_suite': {'id': SUITE}, 'app': app, 'status': 'completed', 'conclusion': 'success'}
        values[api.candidate.artifact_route(CANDIDATE)] = uploaded(CANDIDATE, 'candidate', self.candidate_zip)
        values[api.candidate.artifact_route(RECEIPT)] = uploaded(RECEIPT, 'rehearsal-receipt', self.receipt_zip)
        values[route + '/artifacts?per_page=100&page=1'] = check_fixtures.collection('artifacts',
            [deepcopy(values[api.candidate.artifact_route(number)]) for number in [CANDIDATE, RECEIPT]])
        names = {'services/commons/' + name for name in artifact.MODULES | artifact.LOCAL_MODULES}
        names |= {'services/commons/migrations/' + name for name in artifact.MIGRATIONS} | {api.WORKFLOW_PATH}
        entries = []
        for name in sorted(names):
            raw = (ROOT / name).read_bytes()
            entries.append({'path': name, 'mode': '100644', 'type': 'blob',
                            'sha': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()})
        values[api.BASE + '/git/commits/' + SHA] = {'sha': SHA, 'tree': {'sha': TREE}, 'message': MARKER}
        values[api.BASE + '/git/trees/' + TREE + '?recursive=1'] = {'sha': TREE, 'truncated': False, 'tree': entries}
        return values

    def fetch(self, route, environ):
        self.assertEqual(environ, ENV)
        self.assertTrue(api.CommonsGitHub(ENV).read_route(route), route)
        self.calls.append(route)
        return deepcopy(self.values[route])

    def args(self):
        return ['verify', '--expected-commit', SHA, '--run-id', str(RUN), '--run-attempt', str(ATTEMPT),
                '--candidate-id', str(CANDIDATE), '--receipt-id', str(RECEIPT),
                '--candidate-archive', str(self.candidate), '--receipt-archive', str(self.receipt),
                '--source-dir', str(self.source), '--out', str(self.output)]

    def invoke(self, success=False, args=None, fetch=None):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = cli.cli(args or self.args(), ENV, fetch or self.fetch)
        self.assertNotIn(MARKER, output.getvalue() + errors.getvalue())
        self.assertNotIn(str(self.root), output.getvalue() + errors.getvalue())
        if success:
            self.assertEqual(status, 0, errors.getvalue())
            self.assertEqual(errors.getvalue(), '')
            return json.loads(output.getvalue())
        self.assertEqual(status, 1, output.getvalue())
        self.assertEqual(output.getvalue(), '')
        value = json.loads(errors.getvalue())
        self.assertEqual(set(value), {'error'})
        self.assertRegex(value['error'], '^[a-z_]+$')
        return value['error']

    def replace_archive(self, role, raw):
        number, path = (CANDIDATE, self.candidate) if role == 'candidate' else (RECEIPT, self.receipt)
        path.write_bytes(raw)
        self.values[api.candidate.artifact_route(number)] = uploaded(number, 'candidate' if role == 'candidate' else 'rehearsal-receipt', raw)
        self.values[api.candidate.run_route(RUN) + '/artifacts?per_page=100&page=1']['artifacts'] = [
            deepcopy(self.values[api.candidate.artifact_route(item)]) for item in [CANDIDATE, RECEIPT]]

    def test_real_producer_packet_and_completed_provenance_verify_without_deployment(self):
        result = self.invoke(success=True)
        self.assertEqual(json.loads(self.output.read_bytes()), result)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertIs(result['commons_candidate_verified'], True)
        self.assertIs(result['required_checks_verified'], True)
        self.assertIs(result['deployment_authorized'], False)
        self.assertEqual(result['required_approving_review_count'], 0)
        self.assertEqual(result['packet_sha256'], hashlib.sha256(self.packet).hexdigest())
        self.assertEqual(result['checks']['consumer_rebuild'], 'matched')
        self.assertEqual(len(result['descriptor']['modules']), 6)
        self.assertEqual(len(result['required_checks']), 4)
        self.assertEqual(result['source']['git_tree'], TREE)
        self.assertEqual(len(result['source']['input_blobs']), 10)
        self.assertIn('fresh-installed-schema', result['pending_gates'])
        self.assertNotIn('required-github-checks', result['pending_gates'])
        self.assertEqual(self.calls.count(api.checks.PROTECTION), 2)
        self.assertEqual(self.calls.count(api.WORKFLOW_ROUTE), 2)

    def test_invalid_explicit_identities_stop_before_any_api_read(self):
        for flag, bad in [('--expected-commit', 'main'), ('--expected-commit', 'A' * 40),
                          ('--run-id', '01'), ('--run-id', '0'), ('--run-attempt', '2.0'),
                          ('--run-attempt', '2\n'), ('--candidate-id', '-1'), ('--receipt-id', '9' * 20),
                          ('--receipt-id', str(CANDIDATE))]:
            with self.subTest(flag=flag, bad=bad):
                args = self.args(); args[args.index(flag) + 1] = bad
                self.invoke(args=args)
        self.invoke(args=self.args() + ['--' + MARKER])
        self.assertEqual(self.calls, [])
        self.assertFalse(self.output.exists())

    def test_foreign_stale_failed_or_retried_run_cannot_use_an_earlier_positive_receipt(self):
        route = api.candidate.run_route(RUN)
        for field, bad in [('run_attempt', 3), ('run_attempt', 2.0), ('id', 99), ('workflow_id', 351107990),
                           ('path', api.WORKFLOW_PATH + '@other'), ('event', 'pull_request'), ('head_sha', 'b' * 40),
                           ('head_branch', 'other'), ('status', 'in_progress'), ('conclusion', 'failure')]:
            self.values = self.fixture()
            self.values[route][field] = bad
            with self.subTest(field=field): self.invoke()
        for key in ['repository', 'head_repository']:
            self.values = self.fixture(); self.values[route][key]['fork'] = True; self.invoke()
            self.values = self.fixture(); self.values[route][key]['id'] = 17; self.invoke()
        self.values = self.fixture(); self.values[route + '/attempts/2']['conclusion'] = 'cancelled'; self.invoke()
        self.assertFalse(self.output.exists())

    def test_competing_runs_artifacts_and_partial_lists_fail(self):
        routes = [(api.WORKFLOW_ROUTE + '/runs?head_sha=' + SHA + '&per_page=100&page=1', 'workflow_runs'),
                  (api.candidate.run_route(RUN) + '/artifacts?per_page=100&page=1', 'artifacts')]
        for route, key in routes:
            for mutation in ['partial', 'duplicate', 'additional']:
                self.values = self.fixture()
                value = self.values[route]
                if mutation == 'partial': value['total_count'] += 1
                else:
                    extra = deepcopy(value[key][0])
                    if mutation == 'additional': extra['id'] += 5000
                    value[key].append(extra); value['total_count'] += 1
                with self.subTest(key=key, mutation=mutation): self.invoke()
        self.assertFalse(self.output.exists())

    def test_provenance_requires_complete_candidate_job_and_github_actions_check(self):
        route = api.candidate.run_route(RUN) + '/attempts/2/jobs?per_page=100&page=1'
        for field, bad in [('name', 'unrelated'), ('run_attempt', 1), ('head_sha', 'b' * 40),
                           ('conclusion', 'skipped'), ('check_run_url', 'https://example.invalid/1')]:
            self.values = self.fixture(); self.values[route]['jobs'][0][field] = bad; self.invoke()
        self.values = self.fixture(); self.values[route]['jobs'][0]['steps'].pop(); self.invoke()
        self.values = self.fixture(); self.values[route]['jobs'][0]['steps'][0]['conclusion'] = 'failure'; self.invoke()
        check = api.BASE + '/check-runs/' + str(CHECK)
        self.values = self.fixture(); self.values[check]['app']['id'] = 18; self.invoke()
        self.values = self.fixture(); self.values[check]['check_suite']['id'] = SUITE + 1; self.invoke()
        self.values = self.fixture(); self.values[check]['conclusion'] = 'failure'; self.invoke()
        self.assertFalse(self.output.exists())

    def test_retained_earlier_artifacts_are_never_used_as_current_evidence(self):
        route = api.candidate.run_route(RUN) + '/artifacts?per_page=100&page=1'
        earlier = uploaded(107, 'candidate', b'expired bytes are not downloaded')
        earlier['name'] = f'commons-candidate-{SHA}-{RUN}-1'
        earlier['expired'] = True
        self.values[route]['artifacts'].append(earlier)
        self.values[route]['total_count'] = 3
        result = self.invoke(success=True)
        self.assertEqual(result['unconsumed_earlier_artifacts'], [{'id':107,'named_attempt':1,'role':'candidate'}])
        self.assertNotIn(api.candidate.artifact_route(107), self.calls)
        self.output.unlink()
        for name in [f'commons-candidate-{SHA}-{RUN}-2', f'commons-candidate-{SHA}-{RUN}-3', 'other-artifact']:
            earlier['name'] = name
            self.invoke()
        earlier['name'] = f'commons-candidate-{SHA}-{RUN}-1'
        earlier['workflow_run']['head_repository_id'] = 17
        self.invoke()
        self.assertFalse(self.output.exists())

    def test_artifact_name_expiry_digest_and_repository_are_independent(self):
        route = api.candidate.artifact_route(CANDIDATE)
        for field, bad in [('name', f'commons-candidate-{SHA}-{RUN}-1'), ('expired', True),
                           ('expired', 0), ('expires_at', '2000-01-01T00:00:00Z'), ('expires_at', 'bad'),
                           ('digest', 'sha256:' + '0' * 64), ('id', CANDIDATE + 1)]:
            self.values = self.fixture(); self.values[route][field] = bad
            with self.subTest(field=field): self.invoke()
        for field in ['repository_id', 'head_repository_id', 'id']:
            self.values = self.fixture(); self.values[route]['workflow_run'][field] = 19; self.invoke()
        self.assertFalse(self.output.exists())

    def test_remote_tree_binds_source_migrations_and_rehearsal_workflow(self):
        route = api.BASE + '/git/trees/' + TREE + '?recursive=1'
        for suffix in ['worker.mjs', '0001_commons.sql', 'commons-release-rehearsal.yml']:
            self.values = self.fixture()
            next(item for item in self.values[route]['tree'] if item['path'].endswith(suffix))['sha'] = 'b' * 40
            with self.subTest(file=suffix): self.invoke()
        for field, bad in [('truncated', True), ('sha', 'e' * 40)]:
            self.values = self.fixture(); self.values[route][field] = bad; self.invoke()
        self.values = self.fixture(); self.values[route]['tree'][0]['mode'] = '120000'; self.invoke()
        self.values = self.fixture(); self.values[route]['tree'].append(deepcopy(self.values[route]['tree'][0])); self.invoke()
        self.values = self.fixture()
        self.values[route]['tree'].append({'path':'services/commons/surprise.mjs','type':'blob','mode':'100644','sha':'e'*40})
        self.invoke()
        self.values = self.fixture(); self.values[api.BASE + '/git/commits/' + SHA]['sha'] = 'b' * 40; self.invoke()
        self.assertFalse(self.output.exists())

    def test_local_source_and_schema_changes_are_not_validated_by_a_receipt(self):
        (self.source / 'worker.mjs').write_text('throw new Error("downloaded code must not execute");\n')
        self.assertEqual(self.invoke(), 'source_identity_mismatch')
        shutil.copyfile(ROOT / 'services/commons/worker.mjs', self.source / 'worker.mjs')
        with (self.source / 'migrations/0001_commons.sql').open('a') as stream: stream.write('\nSELECT 1;\n')
        self.assertEqual(self.invoke(), 'schema_profile_changed')
        self.assertFalse(self.output.exists())

    def test_well_formed_repacked_changed_code_is_not_canonical_source(self):
        files, schema = artifact.source_files(self.source)
        files['worker.mjs'] += b'\nthrow new Error("must not execute");\n'
        raw = artifact.packet(files, SHA, schema)
        self.replace_archive('candidate', zipped([('commons.json', raw)]))
        self.assertEqual(self.invoke(), 'rebuild_mismatch')
        self.assertFalse(self.output.exists())

    def test_receipt_cannot_supply_identity_schema_authority_or_missing_checks(self):
        for field, bad in [('deployment_authorized', True), ('run_attempt', 2.0), ('repository_id', 19),
                           ('packet_sha256', 'f' * 64), ('pending_gates', []), ('extra', MARKER)]:
            value = deepcopy(self.receipt_value); value[field] = bad
            self.replace_archive('receipt', zipped([('receipt.json', json.dumps(value).encode())]))
            with self.subTest(field=field): self.assertEqual(self.invoke(), 'invalid_receipt')
        raw = json.dumps(self.receipt_value).encode().replace(b'{', b'{"schema_version":1,', 1)
        self.replace_archive('receipt', zipped([('receipt.json', raw)])); self.invoke()
        self.assertFalse(self.output.exists())

    def test_bounded_exact_zip_layout_rejects_paths_duplicates_links_and_special_files(self):
        malformed = [zipped([('../commons.json', self.packet)]), zipped([('commons.json/inside', self.packet)]),
                     zipped([('commons.json', self.packet), ('commons.json', self.packet)]),
                     zipped([('commons.json', self.packet), ('extra', b'')])]
        nul_name = bytearray(self.candidate_zip)
        central = nul_name.index(b'PK\x01\x02')
        nul_name[35] = 0
        nul_name[central + 51] = 0
        malformed.append(bytes(nul_name))
        for kind in [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFDIR]:
            info = zipfile.ZipInfo('commons.json'); info.create_system = 3; info.external_attr = (kind | 0o600) << 16
            malformed.append(zipped([(info, self.packet)]))
        for raw in malformed:
            self.replace_archive('candidate', raw)
            self.assertIn(self.invoke(), {'archive_layout', 'invalid_archive', 'archive_limit'})
        self.assertFalse(self.output.exists())

    def test_archive_size_crc_stream_headers_and_comments_are_verified(self):
        raw = bytearray(zipped([('commons.json', self.packet)], zipfile.ZIP_STORED))
        name_size, extra_size = struct.unpack_from('<2H', raw, 26)
        raw[30 + name_size + extra_size] ^= 1
        self.replace_archive('candidate', bytes(raw)); self.invoke()
        raw = bytearray(self.candidate_zip); raw[14] ^= 1
        self.replace_archive('candidate', bytes(raw)); self.invoke()
        raw = bytearray(self.candidate_zip); raw[-2:] = b'\x01\x00'; raw += b'x'
        self.replace_archive('candidate', bytes(raw)); self.invoke()
        self.replace_archive('candidate', zipped([('commons.json', b'0' * (artifact.MAX_PACKET + 1))])); self.invoke()
        self.replace_archive('candidate', zipped([('commons.json', self.packet)], zipfile.ZIP_BZIP2)); self.invoke()
        self.assertFalse(self.output.exists())

    def test_failed_or_forged_required_checks_and_policy_block_an_otherwise_valid_candidate(self):
        route = api.BASE + '/check-runs/10101'
        self.values[route]['conclusion'] = 'failure'; self.invoke()
        self.values = self.fixture(); self.values[api.checks.PROTECTION]['enforce_admins']['enabled'] = False; self.invoke()
        self.values = self.fixture(); self.values[api.checks.RULES] = [{'type': 'new-rule'}]; self.invoke()
        self.values = self.fixture(); self.values[api.checks.SETUP]['state'] = 'not-configured'; self.invoke()
        self.assertFalse(self.output.exists())

    def test_repeated_remote_observations_reject_changes_during_verification(self):
        for route, field, bad in [(api.WORKFLOW_ROUTE, 'state', 'disabled_manually'),
                                  (api.candidate.artifact_route(RECEIPT), 'expired', True),
                                  (api.checks.PROTECTION, 'enforce_admins', {'enabled': False})]:
            seen = 0
            def changing(selected, environ):
                nonlocal seen
                value = self.fetch(selected, environ)
                if selected == route:
                    seen += 1
                    if seen == 2: value[field] = bad
                return value
            with self.subTest(route=route): self.invoke(fetch=changing)
        self.assertFalse(self.output.exists())

    def test_input_replacement_after_capture_does_not_relabel_or_execute_new_bytes(self):
        replaced = False
        def replacing(route, environ):
            nonlocal replaced
            if not replaced:
                self.candidate.write_bytes(b'changed after capture')
                replaced = True
            return self.fetch(route, environ)
        result = self.invoke(success=True, fetch=replacing)
        self.assertEqual(result['packet_sha256'], hashlib.sha256(self.packet).hexdigest())
        self.assertEqual(self.candidate.read_bytes(), b'changed after capture')

    def test_no_follow_input_output_and_existing_output_preservation(self):
        original = self.root / 'original.zip'
        self.candidate.rename(original); self.candidate.symlink_to(original)
        self.invoke(); self.assertEqual(self.calls, [])
        self.candidate.unlink(); os.link(original, self.candidate)
        self.invoke(); self.assertEqual(self.calls, [])
        self.candidate.unlink(); self.candidate.write_bytes(self.candidate_zip)
        self.output.write_bytes(b'existing receipt')
        self.invoke(); self.assertEqual(self.output.read_bytes(), b'existing receipt')
        self.output.unlink(); self.output.symlink_to(original)
        self.invoke(); self.assertEqual(original.read_bytes(), self.candidate_zip)

    def test_fixed_get_routes_separate_policy_credentials_and_refuse_redirects(self):
        github = api.CommonsGitHub(ENV)
        for route in ['https://example.invalid', api.BASE + '/actions/workflows/351107990',
                      api.BASE + '/git/commits/main', api.BASE + '/git/trees/' + TREE + '?recursive=0',
                      '/repos/fork/website/branches/main', api.BASE + '/actions/artifacts/01',
                      api.BASE + '/actions/artifacts/97/zip']:
            self.assertFalse(github.read_route(route), route)
        for route in [api.checks.PROTECTION, api.checks.SETUP, api.WORKFLOW_ROUTE, api.BASE + '/git/commits/' + SHA]:
            request = github.request(route)
            self.assertEqual(request.method, 'GET')
            expected = ENV['GH_POLICY_TOKEN'] if route in {api.checks.PROTECTION, api.checks.SETUP} else ENV['GH_TOKEN']
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + expected)
        class Redirect:
            def __init__(self): self.calls = 0
            def open(self, request, timeout):
                self.calls += 1
                raise urllib.error.HTTPError(request.full_url, 302, MARKER, {'Location':'https://example.invalid/'}, None)
        opener = Redirect(); github = api.CommonsGitHub(ENV, opener)
        with self.assertRaisesRegex(api.ArtifactError, '^github_read_failed$'):
            github.get(api.WORKFLOW_ROUTE)
        self.assertEqual(opener.calls, 1)

    def test_unexpected_errors_are_private_and_do_not_create_success_output(self):
        def failing(_route, _environ): raise RuntimeError(MARKER)
        self.assertEqual(self.invoke(fetch=failing), 'commons_candidate_failed')
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
