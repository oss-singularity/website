"""Trusted canonical source, candidate transport and release-policy reads.

This module does not write to GitHub, a provider, or a public document root.
The additional policy credential is sent only to the two administration reads.
"""
from dataclasses import dataclass
import base64
import binascii
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import site_artifact as tree
from site_artifact import ArtifactError, require


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


checks = module('publication_checks', 'release-checks.py')
candidate = module('publication_candidate', 'release-candidate.py')
artifact = candidate.artifact
BASE = checks.BASE
API = checks.API
POLICY_ROUTES = {checks.PROTECTION, checks.SETUP}
SHA = r'[a-f0-9]{40}'
NUMBER = r'[1-9][0-9]{0,18}'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def token(environ, name):
    value = environ.get(name)
    require(type(value) is str and re.fullmatch(r'[\x21-\x7e]{1,8192}', value), 'missing_credential')
    return value


def write_private(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


class GitHub:
    def __init__(self, environ, opener=None):
        self.environ = environ
        self.opener = opener or urllib.request.build_opener(candidate.rehearsal.NoRedirect())

    def read_route(self, route):
        if checks.allowed_route(route):
            return True
        extra = [r'/actions/workflows/351107990',
                 r'/actions/workflows/351107990/runs\?head_sha=' + SHA + r'&per_page=100&page=1',
                 r'/actions/runs/' + NUMBER + r'(?:/attempts/' + NUMBER + r')?',
                 r'/actions/runs/' + NUMBER + r'/artifacts\?per_page=100&page=1',
                 r'/actions/artifacts/' + NUMBER,
                 r'/git/trees/' + SHA + r'\?recursive=1',
                 r'/contents/site/\.htaccess\?ref=' + SHA]
        return any(re.fullmatch(re.escape(BASE) + suffix, route) for suffix in extra)

    def request(self, route):
        credential = 'GH_POLICY_TOKEN' if route in POLICY_ROUTES else 'GH_TOKEN'
        return urllib.request.Request(API + route, method='GET', headers={
            'Authorization': 'Bearer ' + token(self.environ, credential),
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10',
            'Cache-Control': 'no-cache', 'User-Agent': 'oss-singularity-release-client'})

    def get(self, route, _environ=None):
        require(type(route) is str and self.read_route(route), 'invalid_api_route')
        try:
            with self.opener.open(self.request(route), timeout=15) as response:
                require(response.status == 200 and response.geturl() == API + route, 'github_read_failed')
                raw = response.read(checks.MAX_JSON + 1)
        except ArtifactError:
            raise
        except Exception:
            raise ArtifactError('github_read_failed') from None
        return checks.decode(raw)

    def download(self, number, limit):
        checks.positive(number)
        route = BASE + '/actions/artifacts/' + str(number) + '/zip'
        try:
            try:
                with self.opener.open(self.request(route), timeout=15):
                    raise ArtifactError('artifact_transport_failed')
            except urllib.error.HTTPError as redirect:
                require(redirect.code == 302, 'artifact_transport_failed')
                location = redirect.headers.get('Location', '')
                redirect.close()
            parsed = urllib.parse.urlsplit(location)
            require(parsed.scheme == 'https' and parsed.username is None and parsed.password is None
                    and parsed.port in {None, 443} and not parsed.fragment
                    and re.fullmatch(r'[a-z0-9-]+\.blob\.core\.windows\.net', parsed.hostname or '')
                    and len(location) <= 8192, 'artifact_transport_failed')
            # The signed storage URL receives no GitHub credential or cookies.
            request = urllib.request.Request(location, method='GET', headers={'User-Agent': 'oss-singularity-release-client'})
            with self.opener.open(request, timeout=45) as response:
                require(response.status == 200 and response.geturl() == location, 'artifact_transport_failed')
                raw = response.read(limit + 1)
            require(0 < len(raw) <= limit, 'artifact_transport_failed')
            return raw
        except ArtifactError:
            raise
        except Exception:
            raise ArtifactError('artifact_transport_failed') from None


@dataclass(frozen=True)
class Candidate:
    sha: str
    run_id: int
    attempt: int
    descriptor: bytes
    files: dict
    report: dict


def historical_access(github, sha):
    """Read one immutable historical server block; never execute old source.

    The HTTP predecessor capture must also bind these bytes to the independently
    observed manifest before using them for acceptance or rollback.
    """
    artifact.commit(sha)
    value = github.get(BASE + '/contents/site/.htaccess?ref=' + sha)
    require(type(value) is dict and value.get('type') == 'file'
            and value.get('path') == 'site/.htaccess' and value.get('encoding') == 'base64'
            and type(value.get('size')) is int and 0 < value['size'] <= 8192
            and type(value.get('content')) is str and len(value['content']) <= 12288,
            'baseline_mismatch')
    try:
        raw = base64.b64decode(value['content'].replace('\n', ''), validate=True)
    except (ValueError, binascii.Error):
        raise ArtifactError('baseline_mismatch') from None
    require(len(raw) == value['size'], 'baseline_mismatch')
    return raw


def selected_run(github, sha):
    artifact.commit(sha)
    route = BASE + '/actions/workflows/351107990/runs?head_sha=' + sha + '&per_page=100&page=1'
    runs = checks.complete_list(github.get(route), 'workflow_runs')
    require(len(runs) == 1, 'ambiguous_rehearsal')
    run = runs[0]
    run_id, attempt = checks.positive(run.get('id')), checks.positive(run.get('run_attempt'))
    candidate.check_run(run, sha, run_id, attempt)
    return run_id, attempt


def wait_for_sources(github, sha, pause=time.sleep, clock=time.monotonic):
    """Allow independent CI queues to finish; never waive their later checks."""
    artifact.commit(sha)
    deadline = clock() + 360
    profiles = {**checks.WORKFLOWS, 351107990: {'event': 'push'}}
    while True:
        main = github.get(checks.MAIN)
        require(type(main) is dict and main.get('protected') is True
                and type(main.get('commit')) is dict and main['commit'].get('sha') == sha, 'stale_main')
        pending = False
        for number, profile in profiles.items():
            listed = checks.complete_list(github.get(BASE + '/actions/workflows/' + str(number)
                + '/runs?head_sha=' + sha + '&per_page=100&page=1'), 'workflow_runs')
            require(len(listed) <= 1, 'ambiguous_workflow_runs')
            if not listed:
                pending = True
                continue
            run = listed[0]
            require(type(run) is dict and run.get('head_sha') == sha and run.get('head_branch') == 'main'
                    and run.get('event') == profile['event'] and run.get('workflow_id') == number
                    and type(run.get('head_repository')) is dict
                    and run['head_repository'].get('id') == checks.REPOSITORY_ID, 'run_mismatch')
            state = run.get('status')
            require(state in {'queued', 'in_progress', 'requested', 'waiting', 'pending', 'completed'}, 'run_mismatch')
            if state == 'completed':
                require(run.get('conclusion') == 'success', 'unsuccessful_run')
            else:
                pending = True
        if not pending:
            return
        remaining = deadline - clock()
        require(remaining > 0, 'source_wait_expired')
        pause(min(20, remaining))


def consume(github, sha, folder):
    """Select one exact canonical run, retain raw ZIPs and use the real verifier."""
    run_id, attempt = selected_run(github, sha)
    route = BASE + '/actions/runs/' + str(run_id) + '/artifacts?per_page=100&page=1'
    uploaded = checks.complete_list(github.get(route), 'artifacts')
    require(len(uploaded) == 2, 'ambiguous_artifacts')
    names = {role: prefix + '-' + sha + '-' + str(run_id) + '-' + str(attempt)
             for role, prefix in [('candidate', 'static-candidate'), ('receipt', 'static-rehearsal-receipt')]}
    selected = {}
    for role, name in names.items():
        matches = [value for value in uploaded if value.get('name') == name]
        require(len(matches) == 1, 'ambiguous_artifacts')
        selected[role] = checks.positive(matches[0].get('id'))
    raw = {
        'candidate': github.download(selected['candidate'], candidate.MAX_ARCHIVE),
        'receipt': github.download(selected['receipt'], candidate.MAX_RECEIPT + candidate.MAX_CENTRAL + 1024)}
    for role in names:
        write_private(folder / (role + '.zip'), raw[role])
    report = candidate.verify(sha, str(run_id), str(attempt), str(selected['candidate']), folder / 'candidate.zip',
        str(selected['receipt']), folder / 'receipt.zip', folder / 'candidate-verification.json', {}, github.get)
    # Verification consumed immutable snapshots. Reuse these same captured bytes,
    # not an extracted download or a path that can be substituted afterward.
    files = candidate.archive_files(raw['candidate'], 'candidate')
    require(digest(raw['candidate']) == report['artifacts']['candidate']['digest_sha256'],
            'artifact_identity_mismatch')
    require(digest(files['release.json']) == report['descriptor_sha256'], 'descriptor_mismatch')
    payload = {path.removeprefix('payload/'): content for path, content in files.items() if path.startswith('payload/')}
    return Candidate(sha, run_id, attempt, files['release.json'], payload, report)


def checked_source(sha, root):
    artifact.commit(sha)
    def git(*args):
        result = subprocess.run(['git', *args], cwd=root, check=False, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=20)
        require(result.returncode == 0 and len(result.stdout) <= 8192, 'untrusted_checkout')
        return result.stdout.decode('ascii').strip()
    require(git('rev-parse', 'HEAD') == sha and git('status', '--porcelain', '--untracked-files=normal') == '',
            'untrusted_checkout')
    return artifact.commit(git('rev-parse', 'HEAD:services/commons'))


def compatibility(github, installed_sha, expected_tree):
    """A static-only release requires the complete Commons tree to be unchanged."""
    artifact.commit(installed_sha)
    artifact.commit(expected_tree)
    value = github.get(BASE + '/git/trees/' + installed_sha + '?recursive=1')
    require(type(value) is dict and value.get('truncated') is False and type(value.get('tree')) is list
            and len(value['tree']) <= 4096, 'api_compatibility_unverified')
    matches = [entry for entry in value['tree'] if entry.get('path') == 'services/commons']
    require(len(matches) == 1 and matches[0].get('type') == 'tree'
            and matches[0].get('mode') == '040000' and matches[0].get('sha') == expected_tree,
            'api_compatibility_unverified')
    return {'release_sha': installed_sha, 'source_tree': expected_tree, 'source_unchanged': True}
