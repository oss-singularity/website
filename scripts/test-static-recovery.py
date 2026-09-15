"""Blocked-record recovery against installed processes and offline records."""
from contextlib import redirect_stdout
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import release_deployment as deployments
import release_source as source
import release_transport as transport
import static_recovery as recovery
from site_artifact import ArtifactError, MANIFEST

fixture = source.module('recovery_remote_fixture', 'test-static-remote.py')
tested = source.module('recovery_publication_fixture', 'test-static-publication.py')
OLD, NEW = fixture.OLD, fixture.NEW


class RecoveryRemote(tested.InstalledRemote):
    """Installed endpoint that reports the server's stale-attempt refusals."""

    def call(self, request):
        self.trace.append(request['operation'])
        result = subprocess.run([sys.executable, '-I', '-S', str(self.installed.runtime / 'static-remote-release.py'),
            '--config', str(self.installed.config)], input=source.encode(request), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env={**os.environ, 'SSH_ORIGINAL_COMMAND': 'oss-static-release-v1'}, timeout=10)
        if result.returncode != 0:
            value = source.checks.decode(result.stdout) if result.stdout else {}
            code = value.get('error') if type(value) is dict else None
            raise transport.RemoteFailure('remote_stale_attempt' if code == 'stale_attempt'
                                          else 'remote_outcome_unconfirmed')
        value = source.checks.decode(result.stdout)
        transport.validate_report(value, self.runtime)
        return value


class RecoveryHTTP(tested.ObservedHTTP):
    def predecessor(self, baseline, _access):
        self.trace.append('predecessor')
        files = {name: raw for name, raw in self.installed.original.items()
                 if name != '.well-known/provider-marker'}
        if source.digest(files[MANIFEST]) != baseline['baseline_manifest_sha256']:
            raise ArtifactError('baseline_mismatch')
        return files

    def api(self, expected_sha=None):
        self.trace.append('api')
        return expected_sha


class HistoryGitHub:
    def __init__(self, access):
        self.access = access

    def get(self, route):
        assert '/contents/site/.htaccess?ref=' in route
        return {'type': 'file', 'path': 'site/.htaccess', 'encoding': 'base64',
                'size': len(self.access), 'content': base64.b64encode(self.access).decode()}


class ServingRecords(deployments.Deployments):
    def __init__(self, payload, states):
        super().__init__({})
        self.item = {'id': 42, 'environment': deployments.ENVIRONMENT, 'task': deployments.TASK,
                     'production_environment': True, 'payload': payload}
        self.states, self.closed = list(states), None

    def request(self, method, route, body=None):
        if method == 'GET' and route == deployments.LIST:
            return [self.item]
        if method == 'GET' and route.endswith('/statuses?per_page=1&page=1'):
            return self.states
        if method == 'POST' and route.endswith('/statuses'):
            self.closed = body['state']
            self.states = [{'state': body['state'], 'description': body['description']}]
            return {}
        raise AssertionError('unexpected deployment route: ' + route)


def unavailable_loader(commit):
    raise AssertionError('rollback recovery must not consume a candidate: ' + commit)


def installed_case():
    values = {'.htaccess': tested.ACCESS, 'index.html': b'old page', '404.html': b'missing',
              '.well-known/security.txt': tested.SECURITY, 'assets/styles/site.css': b'body {}'}
    old = fixture.payload(values)
    new = fixture.payload({**values, 'index.html': b'new page', 'assets/scripts/new.js': b'new script'})
    return fixture.installation(new, predecessor=old)


def recorded_payload(installed, remote, baseline, identity, commit=NEW):
    return {'schema_version': 1, 'kind': 'static-publication-intent',
            'workflow_run_id': 3, 'workflow_run_attempt': 1, 'identity': identity,
            'commit': commit,
            'descriptor_sha256': source.digest(fixture.descriptor(installed.candidate, commit)),
            'predecessor': {name: baseline[name] for name in
                            ('baseline_commit', 'baseline_manifest_sha256', 'generation')},
            'runtime_sha256': remote.runtime,
            'artifacts': {'candidate': {'id': 71, 'digest_sha256': 'a' * 64},
                          'receipt': {'id': 72, 'digest_sha256': 'b' * 64}},
            'candidate_run_id': 13, 'candidate_run_attempt': 1}


def blocked(payload):
    return ServingRecords(payload, [{'state': 'error', 'description': deployments.UNRESOLVED}])


class RecoveryTests(unittest.TestCase):
    def observe(self, trace):
        return tested.FixedEdge(trace, {'security_sha256': source.digest(tested.SECURITY),
                                        'configuration_sha256': 'c' * 64})

    def test_prepared_attempt_is_reconciled_rolled_back_verified_and_closed(self):
        with installed_case() as installed:
            trace = []
            remote = RecoveryRemote(installed, trace)
            baseline = remote.status()
            prepared = remote.call(dict(installed.request))
            self.assertEqual(prepared['phase'], 'prepared')
            payload = recorded_payload(installed, remote, baseline, installed.request['identity'])
            records = blocked(payload)
            result = recovery.recover(payload, 42, HistoryGitHub(fixture.ACCESS), remote,
                RecoveryHTTP(installed, trace), self.observe(trace), records, unavailable_loader, OLD)
            self.assertEqual(result['outcome'], 'rolled_back')
            self.assertEqual(result['attempt'], 'rolled_back')
            self.assertTrue(result['recovery_verified'])
            self.assertEqual(records.closed, 'failure')
            for entry in ('reconcile', 'rollback', 'purge', 'http'):
                self.assertIn(entry, trace)
            self.assertEqual(fixture.file_contents(installed.target), installed.original)
            self.assertEqual((installed.peer / 'sentinel').read_bytes(), b'unrelated data\n')

    def test_absent_attempt_with_unchanged_baseline_is_verified_and_closed(self):
        with installed_case() as installed:
            trace = []
            remote = RecoveryRemote(installed, trace)
            baseline = remote.status()
            payload = recorded_payload(installed, remote, baseline, 'f' * 32)
            records = ServingRecords(payload, [{'state': 'in_progress', 'description': deployments.IN_PROGRESS}])
            result = recovery.recover(payload, 42, HistoryGitHub(fixture.ACCESS), remote,
                RecoveryHTTP(installed, trace), self.observe(trace), records, unavailable_loader, OLD)
            self.assertEqual(result['outcome'], 'rolled_back')
            self.assertEqual(result['attempt'], 'absent')
            self.assertEqual(records.closed, 'failure')
            self.assertIn('purge', trace)
            self.assertIn('http', trace)
            self.assertNotIn('reconcile', trace)
            self.assertNotIn('rollback', trace)
            self.assertEqual(fixture.file_contents(installed.target), installed.original)

    def test_verified_site_with_interrupted_bookkeeping_is_verified_and_closed_as_success(self):
        values = {'.htaccess': tested.ACCESS, 'index.html': b'old page', '404.html': b'missing',
                  '.well-known/security.txt': tested.SECURITY, 'assets/styles/site.css': b'body {}'}
        candidate = fixture.payload({**values, 'index.html': b'new page', 'assets/scripts/new.js': b'new script'})
        with tested.prepared_case() as (installed, execute, recorded, remote, trace):
            original = remote.operation
            def operation(name, ticket):
                if name == 'maintain':
                    raise transport.RemoteFailure('remote_outcome_unconfirmed')
                return original(name, ticket)
            remote.operation = operation
            with self.assertRaises(ArtifactError):
                execute()
            self.assertEqual(recorded.outcome, 'unresolved')
            self.assertEqual((installed.target / 'index.html').read_bytes(), b'new page')
            payload = recorded.intent
            records = blocked(payload)
            product = source.Candidate(NEW, 13, 1, fixture.descriptor(candidate, NEW), candidate,
                {'descriptor': json.loads(fixture.descriptor(candidate, NEW)),
                 'artifacts': {'candidate': {'id': 71, 'digest_sha256': 'a' * 64},
                               'receipt': {'id': 72, 'digest_sha256': 'b' * 64}}})
            result = recovery.recover(payload, 42, HistoryGitHub(fixture.ACCESS), remote,
                RecoveryHTTP(installed, trace), self.observe(trace), records, lambda commit: product, OLD)
            self.assertEqual(result['outcome'], 'success')
            self.assertEqual(result['attempt'], 'verified')
            self.assertEqual(records.closed, 'success')
            self.assertNotIn('rollback', trace)
            self.assertEqual((installed.target / 'index.html').read_bytes(), b'new page')

    def test_verified_recovery_binds_the_candidate_to_the_recorded_descriptor(self):
        values = {'.htaccess': tested.ACCESS, 'index.html': b'old page', '404.html': b'missing',
                  '.well-known/security.txt': tested.SECURITY, 'assets/styles/site.css': b'body {}'}
        candidate = fixture.payload({**values, 'index.html': b'other page'})
        with tested.prepared_case() as (installed, execute, recorded, remote, trace):
            original = remote.operation
            def operation(name, ticket):
                if name == 'maintain':
                    raise transport.RemoteFailure('remote_outcome_unconfirmed')
                return original(name, ticket)
            remote.operation = operation
            with self.assertRaises(ArtifactError):
                execute()
            payload = recorded.intent
            records = blocked(payload)
            product = source.Candidate(NEW, 13, 1, fixture.descriptor(candidate, NEW), candidate,
                {'descriptor': json.loads(fixture.descriptor(candidate, NEW)), 'artifacts': {}})
            with self.assertRaisesRegex(ArtifactError, 'artifact_identity_mismatch'):
                recovery.recover(payload, 42, HistoryGitHub(fixture.ACCESS), remote,
                    RecoveryHTTP(installed, trace), self.observe(trace), records, lambda commit: product, OLD)
            self.assertIsNone(records.closed)
            self.assertEqual((installed.target / 'index.html').read_bytes(), b'new page')

    def test_newer_operator_bytes_block_recovery_and_keep_the_record_open(self):
        with tested.prepared_case(fail=True, interfere=True) as (installed, execute, recorded, remote, trace):
            with self.assertRaisesRegex(ArtifactError, 'publication_requires_reconciliation'):
                execute()
            self.assertEqual(recorded.outcome, 'unresolved')
            payload = recorded.intent
            records = blocked(payload)
            with self.assertRaises(ArtifactError):
                recovery.recover(payload, 42, HistoryGitHub(fixture.ACCESS), remote,
                    RecoveryHTTP(installed, trace), self.observe(trace), records, unavailable_loader, OLD)
            self.assertIsNone(records.closed)
            self.assertEqual((installed.target / 'index.html').read_bytes(), b'newer operator content')

    def test_changed_endpoint_runtime_is_refused_before_any_endpoint_call(self):
        with installed_case() as installed:
            trace = []
            remote = RecoveryRemote(installed, trace)
            baseline = remote.status()
            payload = recorded_payload(installed, remote, baseline, installed.request['identity'])
            payload['runtime_sha256'] = '0' * 64
            records = blocked(payload)
            with self.assertRaisesRegex(ArtifactError, 'runtime_changed'):
                recovery.recover(payload, 42, HistoryGitHub(fixture.ACCESS), remote,
                    RecoveryHTTP(installed, trace), self.observe(trace), records, unavailable_loader, OLD)
            self.assertEqual(trace, ['status'])


class RecordTests(unittest.TestCase):
    def records(self, states, **changes):
        payload = {'kind': 'static-publication-intent'}
        item = {'id': 42, 'environment': deployments.ENVIRONMENT, 'task': deployments.TASK,
                'production_environment': True, 'payload': payload}
        item.update(changes)
        records = ServingRecords.__new__(ServingRecords)
        records.environ, records.item, records.states, records.closed = {}, item, states, None
        return records

    def test_only_blocked_records_are_recoverable(self):
        for states in [[], [{'state': 'in_progress', 'description': deployments.IN_PROGRESS}],
                       [{'state': 'error', 'description': deployments.UNRESOLVED}]]:
            records = self.records(states)
            self.assertEqual(records.unresolved(), (42, records.item['payload']))
        for state, description in [('success', deployments.SUCCESS), ('failure', deployments.ROLLED_BACK),
                                   ('error', 'other'), ('in_progress', 'other')]:
            with self.assertRaisesRegex(ArtifactError, 'no_unresolved_publication'):
                self.records([{'state': state, 'description': description}]).unresolved()

    def test_foreign_records_are_never_adopted(self):
        for changes in [{'task': 'other'}, {'production_environment': False},
                        {'environment': 'other'}]:
            with self.assertRaisesRegex(ArtifactError, 'invalid_recovery_record'):
                self.records([], **changes).unresolved()
        with self.assertRaisesRegex(ArtifactError, 'invalid_recovery_record'):
            self.records([], payload={'kind': 'other'}).unresolved()


class IntentTests(unittest.TestCase):
    def payload(self):
        return {'schema_version': 1, 'kind': 'static-publication-intent', 'workflow_run_id': 3,
                'workflow_run_attempt': 1, 'identity': 'a' * 32, 'commit': NEW,
                'descriptor_sha256': 'b' * 64,
                'predecessor': {'baseline_commit': OLD, 'baseline_manifest_sha256': 'c' * 64, 'generation': 7},
                'runtime_sha256': 'd' * 64, 'artifacts': {'candidate': {'id': 71}},
                'candidate_run_id': 13, 'candidate_run_attempt': 1}

    def test_intent_requires_the_exact_recorded_fields(self):
        self.assertEqual(recovery.intent(self.payload()), self.payload())
        cases = [lambda value: value.pop('identity'), lambda value: value.update(extra=True),
                 lambda value: value.update(identity='A' * 32), lambda value: value.update(schema_version=2),
                 lambda value: value.update(kind='other'), lambda value: value.update(commit='2' * 39),
                 lambda value: value.update(runtime_sha256=None),
                 lambda value: value.update(predecessor={'baseline_commit': OLD}),
                 lambda value: value['predecessor'].update(generation=0),
                 lambda value: value.update(candidate_run_id='13'),
                 lambda value: value.update(artifacts=['candidate'])]
        for mutate in cases:
            value = self.payload()
            mutate(value)
            with self.assertRaisesRegex(ArtifactError, 'invalid_recovery_record'):
                recovery.intent(value)


class ContextTests(unittest.TestCase):
    def env(self, directory, **changes):
        event = Path(directory) / 'event.json'
        event.write_text(json.dumps({'inputs': {'mode': 'recover'}}))
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_SERVER_URL': 'https://github.com',
               'GITHUB_API_URL': source.checks.API, 'GITHUB_REPOSITORY': source.checks.REPOSITORY,
               'GITHUB_REPOSITORY_ID': str(source.checks.REPOSITORY_ID), 'GITHUB_REF': 'refs/heads/main',
               'GITHUB_REF_PROTECTED': 'true', 'GITHUB_SHA': NEW, 'GITHUB_WORKFLOW_SHA': NEW,
               'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_EVENT_PATH': str(event),
               'GITHUB_WORKFLOW_REF': source.checks.REPOSITORY + '/' + recovery.WORKFLOW + '@refs/heads/main',
               'GITHUB_RUN_ID': '43', 'GITHUB_RUN_ATTEMPT': '1'}
        env.update(changes)
        return env

    def test_recovery_context_binds_main_dispatch_mode_and_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.env(directory)
            self.assertEqual(recovery.context(NEW, env), {'workflow_run_id': 43, 'workflow_run_attempt': 1})
            for key, value in [('GITHUB_REF_PROTECTED', 'false'), ('GITHUB_EVENT_NAME', 'workflow_run'),
                               ('GITHUB_WORKFLOW_SHA', OLD), ('GITHUB_RUN_ID', 'x')]:
                with self.assertRaises(ArtifactError):
                    recovery.context(NEW, self.env(directory, **{key: value}))
            event = Path(directory) / 'event.json'
            event.write_text(json.dumps({'inputs': {'mode': 'publish'}}))
            with self.assertRaisesRegex(ArtifactError, 'untrusted_workflow'):
                recovery.context(NEW, env)


class OutputTests(unittest.TestCase):
    def test_public_failure_output_excludes_unknown_exception_text(self):
        cli = source.module('recovery_cli_fixture', 'static-recovery.py')
        private = 'private fixture path and credential contents'
        for error in [RuntimeError(private), ArtifactError(private)]:
            output = io.StringIO()
            with patch.object(cli, 'main', side_effect=error), redirect_stdout(output):
                self.assertEqual(cli.cli([]), 1)
            result = json.loads(output.getvalue())
            self.assertNotIn(private, output.getvalue())
            self.assertEqual(result, {'error': 'recovery_failed', 'recovery_verified': False})


if __name__ == '__main__':
    unittest.main()
