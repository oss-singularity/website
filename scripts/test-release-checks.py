#!/usr/bin/env python3
"""Offline policy/provenance fixtures; no GitHub or production access."""
from __future__ import annotations

import base64
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
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

import site_artifact as tree
CLI = Path(__file__).with_name('release-checks.py')
spec = importlib.util.spec_from_file_location('checks_tests', CLI)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
SHA = 'a' * 40
MARKER = 'REJECTED_CHECK_POLICY_FIXTURE_2940'
ENV = {'GH_TOKEN': 'public-fixture-' + MARKER}
BASE = '/repos/oss-singularity/website'
APP = {'id': 15368, 'slug': 'github-actions', 'owner': {'login': 'github'}}
REPO = {'id': 1351274990, 'full_name': 'oss-singularity/website', 'fork': False}
CONTEXTS = ['repository-baseline', 'Analyze (actions)', 'Analyze (python)', 'Analyze (javascript-typescript)']


def collection(key, values): return {'total_count': len(values), key: values}


def protection():
    result = {'url': 'ignored', 'required_status_checks': {'strict': True, 'contexts': CONTEXTS.copy(),
              'checks': [{'context': name, 'app_id': 15368} for name in CONTEXTS]},
              'required_pull_request_reviews': {'dismiss_stale_reviews': True, 'require_code_owner_reviews': False,
                                               'require_last_push_approval': False, 'required_approving_review_count': 0}}
    for key, enabled in [('required_signatures', False), ('enforce_admins', True), ('required_linear_history', True),
                         ('allow_force_pushes', False), ('allow_deletions', False), ('block_creations', False),
                         ('required_conversation_resolution', True), ('lock_branch', False), ('allow_fork_syncing', False)]:
        result[key] = {'enabled': enabled}
    return result


def setup():
    return {'state': 'configured', 'languages': ['actions', 'javascript-typescript', 'python'],
            'query_suite': 'extended', 'threat_model': 'remote', 'updated_at': '2026-01-01T00:00:00Z',
            'schedule': 'weekly', 'runner_type': 'standard', 'runner_label': ''}


def fixture():
    source, names = api.local_contract()
    values = {api.MAIN: {'name': 'main', 'protected': True, 'commit': {'sha': SHA}}, api.PROTECTION: protection(),
              api.RULES: [], api.SETUP: setup(), BASE + '/contents/' + api.BASELINE_PATH + '?ref=' + SHA:
              {'type': 'file', 'path': api.BASELINE_PATH, 'name': 'repository-checks.yml', 'encoding': 'base64',
               'size': len(source), 'content': base64.b64encode(source).decode(),
               'sha': hashlib.sha1(b'blob ' + str(len(source)).encode() + b'\0' + source).hexdigest()}}
    checks = []
    profiles = [(345834976, '.github/workflows/repository-checks.yml', 'push', 101, 1001, CONTEXTS[:1]),
                (345943682, 'dynamic/github-code-scanning/codeql', 'dynamic', 202, 2002, CONTEXTS[1:])]
    for workflow, path, event, run_id, suite_id, contexts in profiles:
        workflow_route = BASE + '/actions/workflows/' + str(workflow)
        values[workflow_route] = {'id': workflow, 'path': path, 'state': 'active'}
        run = {'id': run_id, 'workflow_id': workflow, 'run_attempt': 1, 'check_suite_id': suite_id,
               'path': path, 'event': event, 'head_branch': 'main', 'head_sha': SHA,
               'repository': deepcopy(REPO), 'head_repository': deepcopy(REPO), 'status': 'completed', 'conclusion': 'success'}
        route = BASE + '/actions/runs/' + str(run_id)
        values[workflow_route + '/runs?head_sha=' + SHA + '&per_page=100&page=1'] = collection('workflow_runs', [deepcopy(run)])
        values[route] = deepcopy(run)
        values[route + '/attempts/1'] = deepcopy(run)
        values[BASE + '/check-suites/' + str(suite_id)] = {'id': suite_id, 'app': deepcopy(APP), 'repository': deepcopy(REPO),
                                                       'head_sha': SHA, 'head_branch': 'main', 'status': 'completed', 'conclusion': 'success'}
        jobs = []
        for index, name in enumerate(contexts, 1):
            job_id, check_id = run_id * 10 + index, run_id * 100 + index
            steps = names if event == 'push' else ['Initialize analysis', 'Perform analysis']
            job = {'id': job_id, 'run_id': run_id, 'run_attempt': 1, 'head_sha': SHA, 'name': name,
                   'status': 'completed', 'conclusion': 'success', 'check_run_url': api.API + BASE + '/check-runs/' + str(check_id),
                   'steps': [{'name': step, 'number': number, 'status': 'completed', 'conclusion': 'success'} for number, step in enumerate(steps, 1)]}
            check = {'id': check_id, 'name': name, 'head_sha': SHA, 'app': deepcopy(APP), 'check_suite': {'id': suite_id},
                     'status': 'completed', 'conclusion': 'success', 'unrelated': MARKER}
            jobs.append(job)
            checks.append(deepcopy(check))
            values[BASE + '/check-runs/' + str(check_id)] = check
        values[route + '/attempts/1/jobs?per_page=100&page=1'] = collection('jobs', deepcopy(jobs))
        values[route + '/jobs?filter=all&per_page=100&page=1'] = collection('jobs', deepcopy(jobs))
    values[BASE + '/commits/' + SHA + '/check-runs?filter=all&per_page=100&page=1'] = collection('check_runs', checks)
    return values


class Response:
    def __init__(self, raw, status=200, url=None):
        self.raw, self.status, self.url = raw, status, url or api.API + api.MAIN
        self.reads = []
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def geturl(self): return self.url
    def read(self, limit):
        self.reads.append(limit)
        return self.raw[:limit]


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='oss-check-gate-tests-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'verified.json'
        self.values, self.calls = fixture(), []
        self.checks_route = BASE + '/commits/' + SHA + '/check-runs?filter=all&per_page=100&page=1'

    def fetch(self, route, environ):
        self.assertEqual(environ, ENV)
        self.assertTrue(api.allowed_route(route), route)
        self.calls.append(route)
        return deepcopy(self.values[route])

    def args(self): return ['verify', '--expected-commit', SHA, '--out', str(self.output)]

    def invoke(self, success=False, args=None, fetch=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = api.cli(args or self.args(), ENV, fetch or self.fetch)
        self.assertNotIn(MARKER, stdout.getvalue() + stderr.getvalue())
        if success:
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertEqual(stderr.getvalue(), '')
            return json.loads(stdout.getvalue())
        self.assertEqual(code, 1, stdout.getvalue())
        self.assertEqual(stdout.getvalue(), '')
        value = json.loads(stderr.getvalue())
        self.assertEqual(set(value), {'error'})
        self.assertRegex(value['error'], '^[a-z_]+$')
        self.assertFalse(self.output.exists())
        return value['error']

    def test_complete_four_context_chain_has_no_deployment_or_human_approval_claim(self):
        value = self.invoke(success=True)
        self.assertEqual(value, json.loads(self.output.read_bytes()))
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertIs(value['required_checks_verified'], True)
        self.assertIs(value['deployment_authorized'], False)
        self.assertEqual(value['required_approving_review_count'], 0)
        self.assertEqual(sorted(row['context'] for row in value['required_checks']), sorted(CONTEXTS))
        self.assertEqual({row['app_id'] for row in value['required_checks']}, {15368})
        self.assertEqual({row['event'] for row in value['required_checks']}, {'push', 'dynamic'})
        self.assertNotIn(str(self.root), json.dumps(value))
        self.assertEqual(self.calls[:len(self.calls)//2], self.calls[len(self.calls)//2:])

    def test_wrong_cli_sha_and_unknown_flags_never_read_api(self):
        for sha in ['main', 'A' * 40, SHA + '\n', 'a' * 39, MARKER]:
            args = self.args(); args[2] = sha
            self.invoke(args=args)
        self.invoke(args=self.args() + ['--' + MARKER])
        self.assertEqual(self.calls, [])

    def test_policy_weakening_additions_and_boolean_types_fail(self):
        for field in ['strict']:
            for bad in [False, 1, 'true', None]:
                self.values = fixture()
                self.values[api.PROTECTION]['required_status_checks'][field] = bad
                self.invoke()
        for field in ['enforce_admins', 'required_linear_history', 'required_conversation_resolution', 'allow_force_pushes', 'allow_deletions']:
            self.values = fixture()
            self.values[api.PROTECTION][field]['enabled'] = not self.values[api.PROTECTION][field]['enabled']
            self.invoke()
        for bad in [True, 0.0, 1]:
            self.values = fixture()
            self.values[api.PROTECTION]['required_pull_request_reviews']['required_approving_review_count'] = bad
            self.invoke()
        self.values = fixture(); self.values[api.PROTECTION]['new_security_rule'] = True; self.invoke()
        self.values = fixture(); self.values[api.RULES] = [{'type': 'new_rule'}]; self.invoke()
        self.values = fixture(); self.values[api.RULES] = {}; self.invoke()

    def test_exact_context_and_app_policy_cannot_change(self):
        for change in ['missing', 'added', 'wrong_app', 'boolean_app', 'duplicate', 'different_name']:
            self.values = fixture()
            value = self.values[api.PROTECTION]['required_status_checks']
            if change == 'missing': value['contexts'].pop()
            if change == 'added': value['checks'].append({'context': MARKER, 'app_id': 15368})
            if change == 'wrong_app': value['checks'][0]['app_id'] = 42
            if change == 'boolean_app': value['checks'][0]['app_id'] = True
            if change == 'duplicate': value['checks'][0] = deepcopy(value['checks'][1])
            if change == 'different_name': value['checks'][0]['context'] = 'lookalike'
            self.invoke()

    def test_managed_codeql_config_is_semantically_pinned(self):
        for key, bad in [('state', 'not-configured'), ('query_suite', 'default'), ('threat_model', 'local'),
                         ('runner_type', 'self-hosted'), ('runner_label', MARKER), ('schedule', 'monthly'),
                         ('languages', ['python']), ('new_policy', True)]:
            self.values = fixture(); self.values[api.SETUP][key] = bad; self.invoke()
        self.values = fixture(); self.values[api.SETUP]['languages'].append('python'); self.invoke()
        self.values = fixture(); self.values[api.SETUP]['languages'].reverse()
        self.values[api.SETUP]['updated_at'] = 'metadata-only timestamp'
        self.invoke(success=True)

    def test_exact_sha_source_must_match_separately_trusted_local_contract(self):
        route = BASE + '/contents/' + api.BASELINE_PATH + '?ref=' + SHA
        for field, bad in [('type', 'symlink'), ('path', '../' + api.BASELINE_PATH), ('encoding', 'none'),
                           ('content', base64.b64encode(MARKER.encode()).decode()), ('sha', 'b' * 40),
                           ('size', True), ('content', '!!!!')]:
            self.values = fixture(); self.values[route][field] = bad; self.invoke()
        source, _ = api.local_contract()
        for changed in [source + b'\n      if: false\n', source + b'\n      continue-on-error: true\n', b'not a baseline']:
            with patch.object(tree, 'read_external', return_value=changed): self.invoke()

    def test_single_canonical_run_per_workflow_no_best_historical_success_selection(self):
        route = BASE + '/actions/workflows/345834976/runs?head_sha=' + SHA + '&per_page=100&page=1'
        self.values[route] = collection('workflow_runs', []); self.invoke()
        self.values = fixture()
        other = deepcopy(self.values[route]['workflow_runs'][0]); other['id'] += 1; other['status'] = 'queued'
        self.values[route]['workflow_runs'].append(other); self.values[route]['total_count'] = 2
        self.assertEqual(self.invoke(), 'ambiguous_workflow_runs')

    def test_run_event_repo_workflow_status_and_attempt_bindings(self):
        route = BASE + '/actions/runs/202'
        cases = [('event', 'push'), ('workflow_id', 345834976), ('path', '.github/workflows/untrusted.yml'),
                 ('head_sha', 'b' * 40), ('head_branch', 'other'), ('run_attempt', 2), ('run_attempt', True),
                 ('status', 'in_progress'), ('conclusion', 'skipped'), ('conclusion', 'failure'), ('check_suite_id', 1001)]
        for key, bad in cases:
            self.values = fixture(); self.values[route][key] = bad; self.invoke()
        for key in ['repository', 'head_repository']:
            self.values = fixture(); self.values[route][key]['id'] = 42; self.invoke()
        self.values = fixture()
        self.values[BASE + '/actions/runs/101']['event'] = 'pull_request'; self.invoke()
        self.values = fixture()
        self.values[BASE + '/actions/runs/101/attempts/1']['run_attempt'] = 2; self.invoke()

    def test_workflow_record_and_suite_provenance_are_not_inferred_from_names(self):
        for route, field, bad in [(BASE + '/actions/workflows/345943682', 'state', 'disabled_manually'),
                                 (BASE + '/actions/workflows/345943682', 'id', True),
                                 (BASE + '/actions/workflows/345943682', 'path', 'other'),
                                 (BASE + '/check-suites/2002', 'head_sha', 'b' * 40),
                                 (BASE + '/check-suites/2002', 'conclusion', 'failure')]:
            self.values = fixture(); self.values[route][field] = bad; self.invoke()
        self.values = fixture(); self.values[BASE + '/check-suites/2002']['app']['id'] = 42; self.invoke()
        self.values = fixture(); self.values[BASE + '/check-suites/2002']['repository']['id'] = 42; self.invoke()

    def test_current_attempt_needs_every_matrix_job_without_duplicates(self):
        route = BASE + '/actions/runs/202/attempts/1/jobs?per_page=100&page=1'
        self.values[route]['jobs'].pop(); self.values[route]['total_count'] = 2
        self.assertEqual(self.invoke(), 'partial_attempt')
        self.values = fixture()
        self.values[route]['jobs'][1]['name'] = self.values[route]['jobs'][0]['name']; self.invoke()
        self.values = fixture()
        self.values[route]['jobs'][1]['id'] = self.values[route]['jobs'][0]['id']; self.invoke()
        for key, bad in [('run_id', 101), ('run_attempt', 2), ('run_attempt', True), ('head_sha', 'b' * 40),
                         ('status', 'queued'), ('conclusion', 'neutral'), ('check_run_url', 'https://example.invalid/' + MARKER),
                         ('check_run_url', api.API + BASE + '/check-runs/01')]:
            self.values = fixture(); self.values[route]['jobs'][0][key] = bad; self.invoke()

    def test_all_baseline_steps_are_present_completed_successful_and_unconditional(self):
        route = BASE + '/actions/runs/101/attempts/1/jobs?per_page=100&page=1'
        for kind in ['skipped', 'missing', 'duplicate_number', 'boolean_number', 'no_steps']:
            self.values = fixture()
            steps = self.values[route]['jobs'][0]['steps']
            if kind == 'skipped': steps[1]['conclusion'] = 'skipped'
            if kind == 'missing': steps.pop(1)
            if kind == 'duplicate_number': steps[1]['number'] = steps[0]['number']
            if kind == 'boolean_number': steps[0]['number'] = True
            if kind == 'no_steps': steps.clear()
            self.invoke()

    def test_check_app_suite_head_and_job_url_cross_links_are_exact(self):
        for location in ['listing', 'individual']:
            for change in ['app', 'owner', 'name', 'sha', 'suite', 'conclusion']:
                self.values = fixture()
                value = self.values[self.checks_route]['check_runs'][0] if location == 'listing' else self.values[BASE + '/check-runs/10101']
                if change == 'app': value['app']['id'] = 42
                if change == 'owner': value['app']['owner']['login'] = 'other'
                if change == 'name': value['name'] = 'Analyze (python)'
                if change == 'sha': value['head_sha'] = 'b' * 40
                if change == 'suite': value['check_suite']['id'] = 2002
                if change == 'conclusion': value['conclusion'] = 'cancelled'
                self.invoke()
        self.values = fixture()
        value = deepcopy(self.values[self.checks_route]['check_runs'][0]); value['id'] = 99999; value['app']['id'] = 42
        self.values[self.checks_route]['check_runs'].append(value); self.values[self.checks_route]['total_count'] += 1
        self.assertEqual(self.invoke(), 'competing_check')

    def full_rerun(self):
        route = BASE + '/actions/runs/202'
        list_route = BASE + '/actions/workflows/345943682/runs?head_sha=' + SHA + '&per_page=100&page=1'
        self.values[route]['run_attempt'] = 2
        self.values[list_route]['workflow_runs'][0]['run_attempt'] = 2
        self.values[route + '/attempts/2'] = deepcopy(self.values[route])
        old = deepcopy(self.values[route + '/attempts/1/jobs?per_page=100&page=1']['jobs'])
        new = deepcopy(old)
        for job in new:
            old_id = int(job['check_run_url'].rsplit('/', 1)[-1]); new_id = old_id + 100000
            job['id'] += 100000; job['run_attempt'] = 2
            job['check_run_url'] = api.API + BASE + '/check-runs/' + str(new_id)
            check = deepcopy(self.values[BASE + '/check-runs/' + str(old_id)]); check['id'] = new_id
            self.values[BASE + '/check-runs/' + str(new_id)] = check
            self.values[self.checks_route]['check_runs'].append(deepcopy(check))
        self.values[self.checks_route]['total_count'] += len(new)
        self.values[route + '/attempts/2/jobs?per_page=100&page=1'] = collection('jobs', new)
        self.values[route + '/jobs?filter=all&per_page=100&page=1'] = collection('jobs', old + deepcopy(new))

    def test_full_rerun_has_bound_history_but_no_mixing_partial_current_attempt(self):
        self.full_rerun()
        value = self.invoke(success=True)
        self.assertEqual({row['run_attempt'] for row in value['required_checks'] if row['event'] == 'dynamic'}, {2})
        self.output.unlink()
        route = BASE + '/actions/runs/202/attempts/2/jobs?per_page=100&page=1'
        self.values[route]['jobs'].pop(); self.values[route]['total_count'] -= 1
        self.assertEqual(self.invoke(), 'partial_attempt')

    def test_old_results_need_real_old_jobs_in_same_run(self):
        self.full_rerun()
        route = BASE + '/actions/runs/202/jobs?filter=all&per_page=100&page=1'
        self.values[route]['jobs'][0]['run_id'] = 999
        self.invoke()
        self.values = fixture(); self.full_rerun()
        self.values[route]['jobs'].pop(0); self.values[route]['total_count'] -= 1
        self.assertEqual(self.invoke(), 'competing_check')

    def test_complete_bounded_pages_never_accept_a_truncated_listing(self):
        routes = [path for path, value in self.values.items() if type(value) is dict and 'total_count' in value]
        for route in routes:
            for count in [True, 1.0, -1, 101, 99]:
                self.values = fixture(); self.values[route]['total_count'] = count; self.invoke()
        self.values = fixture(); del self.values[self.checks_route]['total_count']; self.invoke()

    def test_pre_post_policy_main_attempt_and_new_run_drift_reject(self):
        cases = [(api.MAIN, lambda value: value['commit'].update(sha='b' * 40)),
                 (api.PROTECTION, lambda value: value['enforce_admins'].update(enabled=False)),
                 (api.SETUP, lambda value: value.update(query_suite='default')),
                 (BASE + '/actions/runs/202', lambda value: value.update(run_attempt=2)),
                 (self.checks_route, lambda value: value['check_runs'][0].update(conclusion='failure'))]
        for changed_route, change in cases:
            counts = {}
            def fetch(route, env):
                result = self.fetch(route, env)
                counts[route] = counts.get(route, 0) + 1
                if route == changed_route and counts[route] == 2: change(result)
                return result
            self.invoke(fetch=fetch)

    def test_exclusive_output_and_symlink_parent_leave_existing_data(self):
        self.output.write_text(MARKER)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr): code = api.cli(self.args(), ENV, self.fetch)
        self.assertEqual(code, 1); self.assertEqual(self.output.read_text(), MARKER)
        self.assertNotIn(MARKER, stdout.getvalue() + stderr.getvalue())
        self.output.unlink()
        target = self.root / 'target'; target.write_text(MARKER)
        self.output.symlink_to(target)
        with redirect_stdout(stdout), redirect_stderr(stderr): code = api.cli(self.args(), ENV, self.fetch)
        self.assertEqual(code, 1); self.assertEqual(target.read_text(), MARKER)
        self.output.unlink()
        alias = self.root / 'alias'; alias.symlink_to(self.root, target_is_directory=True)
        args = self.args(); args[-1] = str(alias / 'new.json')
        self.invoke(args=args); self.assertFalse((self.root / 'new.json').exists())

    def test_no_redirect_fixed_get_body_bound_and_permission_failure(self):
        response = Response(b'{"name":"main"}')
        opener = unittest.mock.Mock(); opener.open.return_value = response
        with patch.object(api.urllib.request, 'build_opener', return_value=opener) as built:
            api.github_get(api.MAIN, ENV)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('X-github-api-version'), '2026-03-10')
        self.assertEqual(request.get_method(), 'GET'); self.assertEqual(request.full_url, api.API + api.MAIN)
        self.assertEqual(opener.open.call_args.kwargs, {'timeout': 10})
        self.assertEqual(response.reads, [api.MAX_JSON + 1])
        self.assertIsNone(built.call_args.args[0].redirect_request(request, None, 302, '', {}, 'https://example.invalid'))
        for route in [api.MAIN + '?x=' + MARKER, BASE + '/actions/runs/01', BASE + '/actions/workflows/42',
                      BASE + '/commits/main/check-runs?filter=all&per_page=100&page=1', 'https://example.invalid/']:
            with patch.object(api.urllib.request, 'build_opener') as built:
                with self.assertRaises(tree.ArtifactError): api.github_get(route, ENV)
                built.assert_not_called()
        for status in [301, 403, 404, 429, 500]:
            opener = unittest.mock.Mock(); opener.open.side_effect = urllib.error.HTTPError(api.API, status, MARKER, {}, None)
            with patch.object(api.urllib.request, 'build_opener', return_value=opener): self.invoke(fetch=api.github_get)
            self.assertEqual(opener.open.call_count, 1)

    def test_json_and_error_output_are_content_free(self):
        for raw in [b'{"x":1,"x":2}', b'{"x":NaN}', b' ' * (api.MAX_JSON + 1), b'\xff',
                    b'[' * 2000 + b'0' + b']' * 2000]:
            with self.assertRaises(tree.ArtifactError): api.decode(raw)
        self.assertEqual(api.decode(b'[' * 32 + b'0' + b']' * 32), json.loads(b'[' * 32 + b'0' + b']' * 32))
        with self.assertRaises(tree.ArtifactError): api.decode(b'[' * 33 + b'0' + b']' * 33)
        def noisy(_route, _env):
            print(MARKER); print(MARKER, file=sys.stderr)
            raise tree.ArtifactError(MARKER)
        self.assertEqual(self.invoke(fetch=noisy), 'check_verification_failed')
        for env in [{}, {'GH_TOKEN': MARKER + '\n'}]:
            with patch.object(api.urllib.request, 'build_opener') as built:
                with self.assertRaises(tree.ArtifactError): api.github_get(api.MAIN, env)
                built.assert_not_called()

    def test_optimized_python_keeps_all_gates_and_zero_authority(self):
        fixtures = self.root / 'fixtures.json'; fixtures.write_text(json.dumps(self.values))
        code = '''import importlib.util,json,sys
s=importlib.util.spec_from_file_location('checks',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
v=json.load(open(sys.argv[2]));raise SystemExit(m.cli(sys.argv[3:],{},lambda route,env:v[route]))'''
        env = {**os.environ, 'PYTHONPATH': str(Path(tree.__file__).parent), 'PYTHONDONTWRITEBYTECODE': '1'}
        result = subprocess.run([sys.executable, '-O', '-c', code, str(CLI), str(fixtures), *self.args()],
                                capture_output=True, text=True, timeout=20, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(json.loads(result.stdout)['deployment_authorized'], False)
        self.output.unlink()
        self.values[api.PROTECTION]['enforce_admins']['enabled'] = False
        fixtures.write_text(json.dumps(self.values))
        result = subprocess.run([sys.executable, '-O', '-c', code, str(CLI), str(fixtures), *self.args()],
                                capture_output=True, text=True, timeout=20, env=env)
        self.assertEqual(result.returncode, 1); self.assertEqual(result.stdout, '')
        self.assertFalse(self.output.exists()); self.assertNotIn(MARKER, result.stderr)


if __name__ == '__main__': unittest.main()
