#!/usr/bin/env python3
"""Read a fixed required-check policy and provenance; never authorize deployment."""
from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import urllib.request

import site_artifact as tree
from site_artifact import ArtifactError, require

spec = importlib.util.spec_from_file_location('checks_rehearsal', Path(tree.__file__).with_name('release-rehearsal.py'))
rehearsal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rehearsal)
REPOSITORY = 'oss-singularity/website'
REPOSITORY_ID = 1351274990
APP_ID = 15368
API = 'https://api.github.com'
BASE = '/repos/' + REPOSITORY
MAIN = BASE + '/branches/main'
PROTECTION = MAIN + '/protection'
RULES = BASE + '/rules/branches/main'
SETUP = BASE + '/code-scanning/default-setup'
BASELINE_PATH = '.github/workflows/repository-checks.yml'
WORKFLOWS = {
    345834976: {'path': BASELINE_PATH, 'event': 'push', 'contexts': ['repository-baseline']},
    345943682: {'path': 'dynamic/github-code-scanning/codeql', 'event': 'dynamic',
                'contexts': ['Analyze (actions)', 'Analyze (python)', 'Analyze (javascript-typescript)']},
}
CONTEXTS = sorted(name for profile in WORKFLOWS.values() for name in profile['contexts'])
MAX_JSON = 1024 * 1024
MAX_SOURCE = 65536
LIMIT = 100
PENDING_GATES = ['trusted-candidate-consumption', 'fresh-protected-head-at-promotion', 'serialized-promotion',
                 'api-compatibility', 'provider-authorization', 'preserved-overlays', 'live-verification', 'rollback']
ERROR_CODES = {'invalid_arguments', 'invalid_commit', 'invalid_json', 'invalid_identity', 'invalid_api_route',
               'missing_api_authentication', 'github_read_failed', 'policy_mismatch', 'source_contract_mismatch',
               'untrusted_local_contract', 'incomplete_or_excessive_list', 'ambiguous_workflow_runs', 'stale_main',
               'workflow_mismatch', 'run_mismatch', 'unsuccessful_run', 'job_mismatch', 'unsuccessful_job',
               'check_mismatch', 'unsuccessful_check', 'suite_mismatch', 'competing_check', 'partial_attempt',
               'state_changed', 'unsafe_file', 'size_limit', 'tree_changed', 'unsupported_filesystem'}


def same(left, right, code):
    require(json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True), code)


def positive(value, code='invalid_identity'):
    require(type(value) is int and 0 < value < 10 ** 19, code)
    return value


def decode(raw):
    require(type(raw) is bytes and len(raw) <= MAX_JSON, 'invalid_json')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=rehearsal.unique_object,
                           parse_constant=rehearsal.reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise ArtifactError('invalid_json') from None
    # Bound nested input independently of a caller's process-wide recursion limit.
    pending = [(value, 0)]
    visited = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        require(depth <= 32 and visited <= 100000, 'invalid_json')
        if type(item) is dict:
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
    return value


def allowed_route(route):
    if route in {MAIN, PROTECTION, RULES, SETUP}: return True
    number, sha = r'[1-9][0-9]{0,18}', r'[a-f0-9]{40}'
    patterns = [r'/actions/workflows/(?:345834976|345943682)',
                r'/actions/workflows/(?:345834976|345943682)/runs\?head_sha=' + sha + r'&per_page=100&page=1',
                r'/actions/runs/' + number,
                r'/actions/runs/' + number + r'/attempts/' + number,
                r'/actions/runs/' + number + r'/attempts/' + number + r'/jobs\?per_page=100&page=1',
                r'/actions/runs/' + number + r'/jobs\?filter=all&per_page=100&page=1',
                r'/check-runs/' + number, r'/check-suites/' + number,
                r'/commits/' + sha + r'/check-runs\?filter=all&per_page=100&page=1',
                r'/contents/\.github/workflows/repository-checks\.yml\?ref=' + sha]
    return any(re.fullmatch(re.escape(BASE) + pattern, route) for pattern in patterns)


def github_get(route, environ):
    require(type(route) is str and allowed_route(route), 'invalid_api_route')
    authorization = environ.get('GH_TOKEN')
    require(type(authorization) is str and re.fullmatch(r'[\x21-\x7e]{1,8192}', authorization), 'missing_api_authentication')
    url = API + route
    try:
        request = urllib.request.Request(url, method='GET', headers={
            'Authorization': 'Bearer ' + authorization, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2026-03-10', 'Cache-Control': 'no-cache',
            'User-Agent': 'oss-singularity-required-check-verifier',
        })
        opener = urllib.request.build_opener(rehearsal.NoRedirect())
        with opener.open(request, timeout=10) as response:
            require(response.status == 200 and response.geturl() == url, 'github_read_failed')
            raw = response.read(MAX_JSON + 1)
    except ArtifactError:
        raise
    except Exception:
        raise ArtifactError('github_read_failed') from None
    return decode(raw)


def without_urls(value):
    if type(value) is dict:
        return {key: without_urls(item) for key, item in value.items() if key != 'url' and not key.endswith('_url')}
    if type(value) is list: return [without_urls(item) for item in value]
    return value


def expected_protection():
    value = {'required_status_checks': {'strict': True, 'contexts': CONTEXTS,
                                       'checks': [{'context': name, 'app_id': APP_ID} for name in CONTEXTS]},
             'required_pull_request_reviews': {'dismiss_stale_reviews': True, 'require_code_owner_reviews': False,
                                              'require_last_push_approval': False, 'required_approving_review_count': 0}}
    for name, enabled in [('required_signatures', False), ('enforce_admins', True), ('required_linear_history', True),
                          ('allow_force_pushes', False), ('allow_deletions', False), ('block_creations', False),
                          ('required_conversation_resolution', True), ('lock_branch', False), ('allow_fork_syncing', False)]:
        value[name] = {'enabled': enabled}
    return value


def policy(protection, rules, setup):
    require(type(protection) is dict and type(rules) is list and not rules and type(setup) is dict, 'policy_mismatch')
    clean = without_urls(protection)
    checks = clean.get('required_status_checks')
    require(type(checks) is dict and type(checks.get('contexts')) is list and type(checks.get('checks')) is list, 'policy_mismatch')
    names, pairs = checks['contexts'], checks['checks']
    require(len(names) == len(CONTEXTS) and all(type(name) is str for name in names), 'policy_mismatch')
    require(len(pairs) == len(CONTEXTS) and all(type(pair) is dict and type(pair.get('context')) is str for pair in pairs), 'policy_mismatch')
    checks['contexts'] = sorted(names)
    checks['checks'] = sorted(pairs, key=lambda pair: pair['context'])
    same(clean, expected_protection(), 'policy_mismatch')
    config = dict(setup)
    config.pop('updated_at', None)  # The source timestamp is not a security setting.
    languages = config.get('languages')
    require(type(languages) is list and all(type(item) is str for item in languages), 'policy_mismatch')
    config['languages'] = sorted(languages)
    expected = {'state': 'configured', 'languages': ['actions', 'javascript-typescript', 'python'],
                'query_suite': 'extended', 'threat_model': 'remote', 'schedule': 'weekly',
                'runner_type': 'standard', 'runner_label': ''}
    same(config, expected, 'policy_mismatch')
    return {'branch_protection': clean, 'effective_rules': [], 'managed_codeql': config}


def local_contract():
    path = Path(tree.__file__).parent.parent / BASELINE_PATH
    raw = tree.read_external(path, MAX_SOURCE)
    try: text = raw.decode('utf-8')
    except UnicodeError: raise ArtifactError('untrusted_local_contract') from None
    names = re.findall(r'^      - name: (.+)$', text, re.MULTILINE)
    require(0 < len(names) <= 32 and len(set(names)) == len(names), 'untrusted_local_contract')
    require('Validate repository baseline' in names and 'Validate Commons service with real SQLite transactions' in names,
            'untrusted_local_contract')
    # This fixed-format source is trusted independently, not supplied by an artifact.
    # Any future conditional-step contract requires review instead of silent skipping.
    require(not re.search(r'^\s*(?:if|continue-on-error):', text, re.MULTILINE), 'untrusted_local_contract')
    return raw, names


def source_matches(value, raw):
    require(type(value) is dict and value.get('type') == 'file' and value.get('path') == BASELINE_PATH
            and value.get('name') == 'repository-checks.yml' and value.get('encoding') == 'base64', 'source_contract_mismatch')
    require(type(value.get('size')) is int and value['size'] == len(raw), 'source_contract_mismatch')
    content = value.get('content')
    require(type(content) is str and len(content) <= MAX_SOURCE * 2, 'source_contract_mismatch')
    try: remote = base64.b64decode(''.join(content.splitlines()), validate=True)
    except (ValueError, UnicodeError): raise ArtifactError('source_contract_mismatch') from None
    require(remote == raw, 'source_contract_mismatch')
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    require(value.get('sha') == blob, 'source_contract_mismatch')


def complete_list(value, key):
    require(type(value) is dict and type(value.get('total_count')) is int and type(value.get(key)) is list,
            'incomplete_or_excessive_list')
    require(0 <= value['total_count'] <= LIMIT and len(value[key]) == value['total_count'], 'incomplete_or_excessive_list')
    require(all(type(item) is dict for item in value[key]), 'incomplete_or_excessive_list')
    ids = [positive(item.get('id'), 'incomplete_or_excessive_list') for item in value[key]]
    require(len(ids) == len(set(ids)), 'incomplete_or_excessive_list')
    return value[key]


def app(value, code):
    require(type(value) is dict and type(value.get('id')) is int and value['id'] == APP_ID
            and value.get('slug') == 'github-actions' and type(value.get('owner')) is dict
            and value['owner'].get('login') == 'github', code)


def repository(value, code):
    require(type(value) is dict and type(value.get('id')) is int and value['id'] == REPOSITORY_ID
            and value.get('full_name') == REPOSITORY and value.get('fork') is False, code)


def run_value(value, workflow_id, sha):
    profile = WORKFLOWS[workflow_id]
    require(type(value) is dict and type(value.get('workflow_id')) is int and value['workflow_id'] == workflow_id, 'run_mismatch')
    run_id, attempt, suite_id = [positive(value.get(key), 'run_mismatch') for key in ['id', 'run_attempt', 'check_suite_id']]
    require(value.get('path') in {profile['path'], profile['path'] + '@main'} and value.get('event') == profile['event']
            and value.get('head_branch') == 'main' and value.get('head_sha') == sha, 'run_mismatch')
    repository(value.get('repository'), 'run_mismatch')
    repository(value.get('head_repository'), 'run_mismatch')
    require(value.get('status') == 'completed' and value.get('conclusion') == 'success', 'unsuccessful_run')
    return {'run_id': run_id, 'run_attempt': attempt, 'check_suite_id': suite_id, 'workflow_id': workflow_id,
            'workflow_path': profile['path'], 'event': profile['event']}


def check_value(value, sha, suite_id, require_success=True):
    require(type(value) is dict and value.get('name') in CONTEXTS and value.get('head_sha') == sha, 'check_mismatch')
    check_id = positive(value.get('id'), 'check_mismatch')
    app(value.get('app'), 'check_mismatch')
    require(type(value.get('check_suite')) is dict and type(value['check_suite'].get('id')) is int
            and value['check_suite']['id'] == suite_id, 'check_mismatch')
    require(value.get('status') == 'completed', 'unsuccessful_check')
    if require_success: require(value.get('conclusion') == 'success', 'unsuccessful_check')
    else: require(value.get('conclusion') in {'success', 'failure', 'cancelled', 'timed_out', 'neutral', 'skipped', 'stale', 'action_required'}, 'check_mismatch')
    return {'check_run_id': check_id, 'context': value['name'], 'conclusion': value['conclusion']}


def suite_value(value, sha, suite_id):
    require(type(value) is dict and type(value.get('id')) is int and value['id'] == suite_id
            and value.get('head_sha') == sha and value.get('head_branch') == 'main', 'suite_mismatch')
    app(value.get('app'), 'suite_mismatch')
    repository(value.get('repository'), 'suite_mismatch')
    require(value.get('status') == 'completed' and value.get('conclusion') == 'success', 'suite_mismatch')


def job_value(value, run, sha, contexts, required_steps, current):
    require(type(value) is dict and value.get('name') in contexts and value.get('head_sha') == sha, 'job_mismatch')
    job_id, attempt = positive(value.get('id'), 'job_mismatch'), positive(value.get('run_attempt'), 'job_mismatch')
    require(type(value.get('run_id')) is int and value['run_id'] == run['run_id']
            and (attempt == run['run_attempt'] if current else attempt < run['run_attempt']), 'job_mismatch')
    link = value.get('check_run_url')
    match = re.fullmatch(re.escape(API + BASE + '/check-runs/') + r'([1-9][0-9]{0,18})', link) if type(link) is str else None
    require(match is not None, 'job_mismatch')
    require(value.get('status') == 'completed', 'unsuccessful_job')
    if current:
        require(value.get('conclusion') == 'success', 'unsuccessful_job')
        steps = value.get('steps')
        require(type(steps) is list and 0 < len(steps) <= LIMIT, 'job_mismatch')
        numbers, names = [], []
        for step in steps:
            require(type(step) is dict and type(step.get('name')) is str and 0 < len(step['name']) <= 256, 'job_mismatch')
            numbers.append(positive(step.get('number'), 'job_mismatch'))
            names.append(step['name'])
            require(step.get('status') == 'completed' and step.get('conclusion') == 'success', 'unsuccessful_job')
        require(len(numbers) == len(set(numbers)) and all(names.count(name) == 1 for name in required_steps), 'job_mismatch')
    else:
        require(value.get('conclusion') in {'success', 'failure', 'cancelled', 'timed_out', 'neutral', 'skipped', 'stale', 'action_required'}, 'job_mismatch')
    return {'job_id': job_id, 'check_run_id': int(match[1]), 'run_attempt': attempt,
            'context': value['name'], 'conclusion': value['conclusion']}


def observe(sha, source, required_steps, environ, fetch):
    main = fetch(MAIN, environ)
    require(type(main) is dict and main.get('name') == 'main' and main.get('protected') is True
            and type(main.get('commit')) is dict and main['commit'].get('sha') == sha, 'stale_main')
    contract = policy(fetch(PROTECTION, environ), fetch(RULES, environ), fetch(SETUP, environ))
    source_matches(fetch(BASE + '/contents/' + BASELINE_PATH + '?ref=' + sha, environ), source)
    runs, expected, history, suite_ids = [], {}, {}, set()
    for workflow_id, profile in WORKFLOWS.items():
        workflow_route = BASE + '/actions/workflows/' + str(workflow_id)
        workflow = fetch(workflow_route, environ)
        require(type(workflow) is dict and type(workflow.get('id')) is int and workflow['id'] == workflow_id
                and workflow.get('path') == profile['path'] and workflow.get('state') == 'active', 'workflow_mismatch')
        listed = complete_list(fetch(workflow_route + '/runs?head_sha=' + sha + '&per_page=100&page=1', environ), 'workflow_runs')
        require(len(listed) == 1, 'ambiguous_workflow_runs')
        run = run_value(listed[0], workflow_id, sha)
        route = BASE + '/actions/runs/' + str(run['run_id'])
        same(run_value(fetch(route, environ), workflow_id, sha), run, 'state_changed')
        attempt_route = route + '/attempts/' + str(run['run_attempt'])
        same(run_value(fetch(attempt_route, environ), workflow_id, sha), run, 'state_changed')
        require(run['check_suite_id'] not in suite_ids, 'suite_mismatch')
        suite_ids.add(run['check_suite_id'])
        suite_value(fetch(BASE + '/check-suites/' + str(run['check_suite_id']), environ), sha, run['check_suite_id'])
        jobs = complete_list(fetch(attempt_route + '/jobs?per_page=100&page=1', environ), 'jobs')
        require(len(jobs) == len(profile['contexts']), 'partial_attempt')
        selected = [job_value(job, run, sha, profile['contexts'], required_steps if workflow_id == 345834976 else [], True) for job in jobs]
        require(sorted(job['context'] for job in selected) == sorted(profile['contexts']), 'partial_attempt')
        all_jobs = complete_list(fetch(route + '/jobs?filter=all&per_page=100&page=1', environ), 'jobs')
        selected_by_id = {job['job_id']: job for job in selected}
        seen_current = set()
        for job in all_jobs:
            current = type(job.get('run_attempt')) is int and job['run_attempt'] == run['run_attempt']
            normalized = job_value(job, run, sha, profile['contexts'], required_steps if workflow_id == 345834976 else [], current)
            target = expected if current else history
            check_id = normalized['check_run_id']
            require(check_id not in expected and check_id not in history, 'job_mismatch')
            if current:
                require(normalized['job_id'] in selected_by_id, 'job_mismatch')
                same(normalized, selected_by_id[normalized['job_id']], 'job_mismatch')
                seen_current.add(normalized['job_id'])
            target[check_id] = {**normalized, **run}
            target[check_id]['run_attempt'] = normalized['run_attempt']
        require(seen_current == set(selected_by_id), 'partial_attempt')
        runs.append(run)
    listed_checks = complete_list(fetch(BASE + '/commits/' + sha + '/check-runs?filter=all&per_page=100&page=1', environ), 'check_runs')
    relevant = {value['id']: value for value in listed_checks if value.get('name') in CONTEXTS}
    require(set(relevant) == set(expected) | set(history), 'competing_check')
    for check_id, job in {**history, **expected}.items():
        result = check_value(relevant[check_id], sha, job['check_suite_id'], check_id in expected)
        require(result['context'] == job['context'] and result['conclusion'] == job['conclusion'], 'check_mismatch')
        if check_id in expected:
            same(check_value(fetch(BASE + '/check-runs/' + str(check_id), environ), sha, job['check_suite_id']), result, 'check_mismatch')
    return {'policy': contract, 'baseline_source_sha256': hashlib.sha256(source).hexdigest(),
            'runs': runs, 'required_checks': [expected[key] for key in sorted(expected)],
            'historical_checks': [history[key] for key in sorted(history)]}


def verify(sha, output, environ, fetch=github_get):
    sha = rehearsal.artifact.commit(sha)
    source, required_steps = local_contract()
    first = observe(sha, source, required_steps, environ, fetch)
    second = observe(sha, source, required_steps, environ, fetch)
    same(first, second, 'state_changed')
    policy_hash = hashlib.sha256(json.dumps(first['policy'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    checks = [{**value, 'app_id': APP_ID} for value in first['required_checks']]
    value = {'schema_version': 1, 'kind': 'required-release-checks', 'repository': REPOSITORY,
             'repository_id': REPOSITORY_ID, 'commit': sha, 'policy_sha256': policy_hash,
             'baseline_source_sha256': first['baseline_source_sha256'], 'required_checks': checks,
             'required_approving_review_count': 0, 'required_checks_verified': True,
             'deployment_authorized': False, 'pending_gates': list(PENDING_GATES)}
    rehearsal.write_receipt(output, value)
    return value


class Parser(argparse.ArgumentParser):
    def error(self, _message): raise ArtifactError('invalid_arguments')


def main(argv=None, environ=None, fetch=github_get):
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
    command = commands.add_parser('verify', help='Read fixed branch policy and exact required-check provenance')
    command.add_argument('--expected-commit', required=True)
    command.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    return verify(args.expected_commit, args.out, os.environ if environ is None else environ, fetch)


def cli(argv=None, environ=None, fetch=github_get):
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(io.StringIO()): result = main(argv, environ, fetch)
    except SystemExit as error:
        if error.code == 0:
            print(buffer.getvalue(), end='')
            return 0
        print(json.dumps({'error': 'invalid_arguments'}), file=sys.stderr)
        return 1
    except ArtifactError as error:
        print(json.dumps({'error': error.code if error.code in ERROR_CODES else 'check_verification_failed'}), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({'error': 'unsafe_or_unavailable_path'}), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({'error': 'check_verification_failed'}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(cli())
