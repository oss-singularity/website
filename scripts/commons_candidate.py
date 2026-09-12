"""Bind captured Commons archives to completed GitHub runs, source and checks.

Only fixed GitHub reads and a new local receipt are permitted by this command.
Downloaded code never becomes executable input; schema SQL remains hash-pinned.
"""
import hashlib
import io
from pathlib import Path
import re
import stat
import zipfile

import commons_artifact as artifact
import commons_rehearsal as rehearsal
from release_source import GitHub, candidate, checks
import site_artifact as tree
from site_artifact import ArtifactError, require

BASE = checks.BASE
WORKFLOW_ID = 356455115
WORKFLOW_PATH = '.github/workflows/commons-release-rehearsal.yml'
WORKFLOW_ROUTE = BASE + '/actions/workflows/' + str(WORKFLOW_ID)
MAX_RECEIPT = 16384
MAX_TREE_ENTRIES = 4096
PENDING = ['fresh-protected-head-at-promotion', 'fresh-installed-schema', 'scoped-provider-access',
           'serialized-promotion', 'durable-recovery', 'live-verification']
ERROR_CODES = candidate.ERROR_CODES | checks.ERROR_CODES | {
    'missing_credential', 'source_identity_mismatch', 'source_module_allowlist_mismatch',
    'schema_profile_changed', 'rebuild_mismatch', 'descriptor_mismatch', 'invalid_packet',
    'duplicate_packet_key', 'module_allowlist_mismatch', 'invalid_module', 'packet_size_limit'}


class CommonsGitHub(GitHub):
    """Reuse credential separation and bounded GETs without widening static routes."""
    def read_route(self, route):
        if type(route) is not str:
            return False
        if checks.allowed_route(route):
            return True
        number, sha = r'[1-9][0-9]{0,18}', r'[a-f0-9]{40}'
        patterns = [r'/actions/workflows/356455115',
                    r'/actions/workflows/356455115/runs\?head_sha=' + sha + r'&per_page=100&page=1',
                    r'/actions/runs/' + number + r'/artifacts\?per_page=100&page=1',
                    r'/actions/artifacts/' + number,
                    r'/git/commits/' + sha, r'/git/trees/' + sha + r'\?recursive=1']
        return any(re.fullmatch(re.escape(BASE) + pattern, route) for pattern in patterns)


def blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def archive_member(raw, filename, limit):
    require(type(raw) is bytes and len(raw) <= limit + candidate.MAX_CENTRAL + 1024, 'archive_limit')
    count, directory = candidate.zip_directory(raw)
    require(count == 1, 'archive_layout')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            require(len(entries) == 1, 'archive_layout')
            info = entries[0]
            require(info.filename == info.orig_filename == filename and not info.is_dir(), 'archive_layout')
            require(info.flag_bits & ~(0x800 | 0x8 | 0x6) == 0
                    and info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    and stat.S_IFMT(info.external_attr >> 16) in {0, stat.S_IFREG}
                    and not info.external_attr & 0x10, 'invalid_archive')
            bounds = candidate.local_records(raw, entries, directory)
            return candidate.member_bytes(raw, info, bounds[info.header_offset], limit)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, UnicodeError, EOFError):
        raise ArtifactError('invalid_archive') from None


def source_contract(source):
    files, migrations = artifact.source_inputs(source)
    schema = artifact.expected_schema(migrations)
    require(schema == rehearsal.SCHEMA_SHA256, 'schema_profile_changed')
    workflow = tree.read_external(Path(__file__).parent.parent / WORKFLOW_PATH, checks.MAX_SOURCE)
    try:
        names = re.findall(r'^      - name: (.+)$', workflow.decode('utf-8'), re.MULTILINE)
    except UnicodeError:
        raise ArtifactError('untrusted_local_contract') from None
    require(0 < len(names) <= 32 and len(set(names)) == len(names), 'untrusted_local_contract')
    inputs = {'services/commons/' + name: raw for name, raw in files.items()}
    inputs.update({'services/commons/migrations/' + name: raw for name, raw in migrations.items()})
    inputs[WORKFLOW_PATH] = workflow
    return files, schema, {name: blob(raw) for name, raw in sorted(inputs.items())}, names


def source_identity(sha, inputs, environ, fetch):
    commit = fetch(BASE + '/git/commits/' + sha, environ)
    require(type(commit) is dict and commit.get('sha') == sha and type(commit.get('tree')) is dict,
            'source_identity_mismatch')
    tree_sha = artifact.commit(commit['tree'].get('sha'))
    result = fetch(BASE + '/git/trees/' + tree_sha + '?recursive=1', environ)
    require(type(result) is dict and result.get('sha') == tree_sha and result.get('truncated') is False
            and type(result.get('tree')) is list and 0 < len(result['tree']) <= MAX_TREE_ENTRIES,
            'source_identity_mismatch')
    entries = {}
    for entry in result['tree']:
        require(type(entry) is dict and type(entry.get('path')) is str and 0 < len(entry['path']) <= 1024
                and entry['path'] not in entries, 'source_identity_mismatch')
        entries[entry['path']] = entry
    root_modules = {name.removeprefix('services/commons/') for name in entries
                    if name.startswith('services/commons/') and name.count('/') == 2 and name.endswith('.mjs')}
    migrations = {name for name in entries if name.startswith('services/commons/migrations/')}
    require(root_modules == artifact.MODULES | artifact.LOCAL_MODULES, 'source_module_allowlist_mismatch')
    require(migrations == {'services/commons/migrations/' + name for name in artifact.MIGRATIONS}, 'schema_profile_changed')
    for name, expected_blob in inputs.items():
        entry = entries.get(name)
        require(type(entry) is dict and entry.get('type') == 'blob' and entry.get('mode') == '100644'
                and entry.get('sha') == expected_blob, 'source_identity_mismatch')
    return {'git_tree': tree_sha, 'input_blobs': inputs}


def run_value(value, sha, run_id, attempt):
    require(type(value) is dict, 'run_identity_mismatch')
    for name, expected in [('id', run_id), ('run_attempt', attempt), ('workflow_id', WORKFLOW_ID)]:
        require(type(value.get(name)) is int and value[name] == expected, 'run_identity_mismatch')
    require(value.get('path') in {WORKFLOW_PATH, WORKFLOW_PATH + '@main'} and value.get('event') == 'push'
            and value.get('head_branch') == 'main' and value.get('head_sha') == sha, 'run_identity_mismatch')
    checks.repository(value.get('repository'), 'run_identity_mismatch')
    checks.repository(value.get('head_repository'), 'run_identity_mismatch')
    require(value.get('status') == 'completed' and value.get('conclusion') == 'success', 'run_not_successful')
    return {'run_id': run_id, 'run_attempt': attempt, 'check_suite_id': checks.positive(value.get('check_suite_id'))}


def observe(sha, run_id, attempt, archives, inputs, steps, environ, fetch):
    main = fetch(checks.MAIN, environ)
    require(type(main) is dict and main.get('name') == 'main' and main.get('protected') is True
            and type(main.get('commit')) is dict and main['commit'].get('sha') == sha, 'stale_main')
    workflow = fetch(WORKFLOW_ROUTE, environ)
    require(type(workflow) is dict and type(workflow.get('id')) is int and workflow['id'] == WORKFLOW_ID
            and workflow.get('path') == WORKFLOW_PATH and workflow.get('state') == 'active', 'workflow_identity_mismatch')
    listed = checks.complete_list(fetch(WORKFLOW_ROUTE + '/runs?head_sha=' + sha + '&per_page=100&page=1', environ), 'workflow_runs')
    require(len(listed) == 1, 'ambiguous_workflow_runs')
    run = run_value(listed[0], sha, run_id, attempt)
    route = candidate.run_route(run_id)
    for selected in [route, candidate.run_route(run_id, attempt)]:
        candidate.exact(run_value(fetch(selected, environ), sha, run_id, attempt), run, 'state_changed')
    suite_id = run['check_suite_id']
    checks.suite_value(fetch(BASE + '/check-suites/' + str(suite_id), environ), sha, suite_id)
    jobs = checks.complete_list(fetch(candidate.run_route(run_id, attempt) + '/jobs?per_page=100&page=1', environ), 'jobs')
    require(len(jobs) == 1, 'partial_attempt')
    job = checks.job_value(jobs[0], run, sha, ['candidate'], steps, True)
    check = fetch(BASE + '/check-runs/' + str(job['check_run_id']), environ)
    require(type(check) is dict and type(check.get('id')) is int and check['id'] == job['check_run_id']
            and check.get('name') == 'candidate' and check.get('head_sha') == sha
            and type(check.get('check_suite')) is dict and type(check['check_suite'].get('id')) is int
            and check['check_suite']['id'] == suite_id, 'check_mismatch')
    checks.app(check.get('app'), 'check_mismatch')
    require(check.get('status') == 'completed' and check.get('conclusion') == 'success', 'unsuccessful_check')
    uploaded = checks.complete_list(fetch(route + '/artifacts?per_page=100&page=1', environ), 'artifacts')
    selected_ids = {number for number, _raw in archives.values()}
    require(selected_ids <= {item['id'] for item in uploaded}, 'artifact_identity_mismatch')
    history = []
    for item in uploaded:
        if item['id'] in selected_ids:
            continue
        # Retained uploads from earlier attempts are observed but never consumed.
        name = item.get('name')
        match = re.fullmatch(r'commons-(candidate|rehearsal-receipt)-' + sha + '-' + str(run_id)
                             + r'-([1-9][0-9]{0,18})', name) if type(name) is str else None
        require(match is not None and int(match[2]) < attempt, 'artifact_identity_mismatch')
        origin = item.get('workflow_run')
        require(type(origin) is dict and origin.get('head_branch') == 'main' and origin.get('head_sha') == sha,
                'artifact_identity_mismatch')
        for key, expected in [('id', run_id), ('repository_id', rehearsal.REPOSITORY_ID),
                              ('head_repository_id', rehearsal.REPOSITORY_ID)]:
            require(type(origin.get(key)) is int and origin[key] == expected, 'artifact_identity_mismatch')
        history.append({'id': item['id'], 'named_attempt': int(match[2]), 'role': match[1]})
    observed = {}
    for role, (number, raw) in archives.items():
        checksum = artifact.digest(raw)
        profile = {'commit': sha, 'run_id': run_id, 'run_attempt': attempt}
        for value in [next(item for item in uploaded if item['id'] == number), fetch(candidate.artifact_route(number), environ)]:
            rehearsal.uploaded(value, number, checksum, profile, 'candidate' if role == 'candidate' else 'rehearsal-receipt')
        observed[role] = {'id': number, 'digest_sha256': checksum}
    return {'run': run, 'job': job, 'artifacts': observed, 'unconsumed_earlier_artifacts': sorted(history, key=lambda item: item['id']),
            'source': source_identity(sha, inputs, environ, fetch)}


def receipt_matches(raw, packet, descriptor, sha, run_id, attempt, uploaded):
    value = rehearsal.shared.decode_object(raw, MAX_RECEIPT)
    expected = {'schema_version': 1, 'kind': 'commons-release-rehearsal', 'repository': rehearsal.REPOSITORY,
                'repository_id': rehearsal.REPOSITORY_ID, 'event': 'push', 'ref': 'refs/heads/main', 'commit': sha,
                'workflow_ref': rehearsal.WORKFLOW_REF, 'workflow_sha': sha, 'run_id': run_id, 'run_attempt': attempt,
                'observed_main_sha': sha, 'observed_main_protected': True, 'deployment_authorized': False,
                'artifact': {**uploaded, 'metadata_verified': True}, 'packet_sha256': artifact.digest(packet),
                'descriptor': descriptor, 'checks': {'artifact_verified': True, 'rebuild_matched': True, 'transport_roundtrip': True},
                'pending_gates': list(rehearsal.PENDING)}
    candidate.exact(value, expected, 'invalid_receipt')


def verify(sha, run_id, attempt, candidate_id, candidate_path, receipt_id, receipt_path, source, output,
           environ, fetch=None):
    sha = artifact.commit(sha)
    run_id, attempt, candidate_id, receipt_id = map(rehearsal.shared.decimal, [run_id, attempt, candidate_id, receipt_id])
    require(candidate_id != receipt_id, 'invalid_identity')
    fetch = CommonsGitHub(environ).get if fetch is None else fetch
    archives = {'candidate': (candidate_id, tree.read_external(candidate_path, artifact.MAX_PACKET + candidate.MAX_CENTRAL + 1024)),
                'receipt': (receipt_id, tree.read_external(receipt_path, MAX_RECEIPT + candidate.MAX_CENTRAL + 1024))}
    files, schema, inputs, steps = source_contract(source)
    baseline, baseline_steps = checks.local_contract()
    first = observe(sha, run_id, attempt, archives, inputs, steps, environ, fetch)
    required = checks.observe(sha, baseline, baseline_steps, environ, fetch)
    packet = archive_member(archives['candidate'][1], 'commons.json', artifact.MAX_PACKET)
    receipt = archive_member(archives['receipt'][1], 'receipt.json', MAX_RECEIPT)
    restored, descriptor = artifact.unpack(packet, sha, rehearsal.SCHEMA_SHA256)
    require(restored == files and packet == artifact.packet(files, sha, schema), 'rebuild_mismatch')
    receipt_matches(receipt, packet, descriptor, sha, run_id, attempt, first['artifacts']['candidate'])
    candidate.exact(checks.observe(sha, baseline, baseline_steps, environ, fetch), required, 'state_changed')
    candidate.exact(observe(sha, run_id, attempt, archives, inputs, steps, environ, fetch), first, 'state_changed')
    result = {'schema_version': 1, 'kind': 'commons-candidate-verification',
              'repository': rehearsal.REPOSITORY, 'repository_id': rehearsal.REPOSITORY_ID,
              'workflow_id': WORKFLOW_ID, 'workflow_path': WORKFLOW_PATH, 'commit': sha,
              **first['run'], 'job': first['job'], 'artifacts': first['artifacts'],
              'unconsumed_earlier_artifacts': first['unconsumed_earlier_artifacts'],
              'source': first['source'], 'descriptor': descriptor, 'packet_sha256': artifact.digest(packet),
              'policy_sha256': artifact.digest(artifact.encode(required['policy'])),
              'baseline_source_sha256': required['baseline_source_sha256'],
              'required_checks': [{**item, 'app_id': checks.APP_ID} for item in required['required_checks']],
              'required_approving_review_count': 0, 'required_checks_verified': True,
              'commons_candidate_verified': True, 'deployment_authorized': False,
              'checks': {'completed_rehearsal': True, 'job_provenance': True, 'archive_digests': True,
                         'receipt_matches': True, 'consumer_rebuild': 'matched', 'canonical_source': True,
                         'observed_current_protected_main': True}, 'pending_gates': list(PENDING)}
    rehearsal.shared.write_receipt(output, result)
    return result
