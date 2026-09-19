"""Offline promotion engine tests against a synthetic provider and records."""
import hashlib
import json
import sqlite3
import unittest
from pathlib import Path

import commons_artifact as artifact
import commons_plan as planner
import commons_promotion as promotion
import release_deployment as deployments
import release_source as source
from site_artifact import ArtifactError

VERSION_A = 'aaaa-version-a'
LIVE_DEPLOYMENT = 'dep-live-version-a'
MODULES = ['worker.mjs', 'util.mjs']
BINDINGS = [
    {'name': 'DB', 'type': 'd1', 'id': 'd1-uuid'},
    {'name': 'ADMIN_TOKEN', 'type': 'secret_text'},
    {'name': 'GITHUB_READ_TOKEN', 'type': 'secret_text'},
    {'name': 'PUBLIC_ORIGIN', 'type': 'plain_text', 'text': 'https://oss-singularity.io'},
]
OLD = '1' * 40
NEW = '2' * 40
VERSION = '11111111-1111-4111-8111-111111111111'
DEPLOYMENT = '22222222-2222-4222-8222-222222222222'
PENDING_VERSION = '33333333-3333-4333-8333-333333333333'
DATABASE = '44444444-4444-4444-8444-444444444444'


def plan(**changes):
    value = {
        'predecessor_version': VERSION_A, 'predecessor_deployment_id': LIVE_DEPLOYMENT,
        'predecessor_generation': 3, 'release_sha': '1' * 40,
        'message': 'Promote verified Commons candidate 2' * 40 + '2', 'tag': 'commons-candidate-tag',
        'bindings': [{'name': b['name'], 'type': 'inherit'} for b in BINDINGS],
        'installed_bindings': BINDINGS, 'main_module': 'worker.mjs',
        'compatibility_date': '2026-09-04', 'plan_sha256': 'a' * 64,
    }
    value.update(changes)
    return value


def candidate(**changes):
    value = {'commit': '2' * 40, 'modules': MODULES, 'content': b'packet-bytes'}
    value.update(changes)
    return value


class FakeResponse:
    def __init__(self, value, url, status=200):
        self.raw, self.url, self.status = source.encode(value) if not isinstance(value, bytes) else value, url, status
    def geturl(self):
        return self.url
    def read(self, limit=-1):
        return self.raw[:limit] if limit > 0 else self.raw
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False


class FakeAdapter:
    """Synthetic provider: version listing, details, and single mutations."""
    def __init__(self, *, fail_stage=False, fail_activate=False, drop_binding=False,
                 foreign_activation_after_stage=False, foreign_upload_after_stage=False):
        self.active = VERSION_A
        self.latest = VERSION_A
        self.deployment_id = LIVE_DEPLOYMENT
        self.versions = {VERSION_A: {'number': 3, 'annotations': {'workers/message': 'installed', 'workers/tag': 'installed'}}}
        self.calls = []
        self.fail_stage, self.fail_activate, self.drop_binding = fail_stage, fail_activate, drop_binding
        self.staged_identity = None
        self.staged = None
        self.foreign_activation_after_stage = foreign_activation_after_stage
        self.foreign_upload_after_stage = foreign_upload_after_stage
        self._foreign_applied = False

    def _apply_foreign_change(self):
        """A privileged operator mutates the target once, after this call staged."""
        if self._foreign_applied or self.staged is None:
            return
        self._foreign_applied = True
        if self.foreign_activation_after_stage:
            self.active = 'intervening-operator-version'
            self.deployment_id = 'intervening-deployment'
            self.latest = 'intervening-operator-version'
            self.versions['intervening-operator-version'] = {'number': 9, 'annotations': {}}
        elif self.foreign_upload_after_stage:
            self.latest = 'foreign-pending-upload'
            self.versions['foreign-pending-upload'] = {'number': 9, 'annotations': {}}

    def observe(self):
        self._apply_foreign_change()
        return {'active_version': self.active, 'latest_version_id': self.latest,
                'versions': self.versions,
                'deployments': [{'id': self.deployment_id, 'strategy': 'percentage',
                                 'versions': [{'version_id': self.active, 'percentage': 100}]}]}

    def stage_version(self, content, commit, message, tag, bindings=None,
                      main_module='worker.mjs', compatibility_date=None):
        self.calls.append('stage')
        version = 'stag-' + commit[:4]
        self.versions[version] = {'number': 4,
                                  'annotations': {'workers/message': message, 'workers/tag': tag}}
        self.staged = version
        self.latest = version
        self.staged_identity = {'workers/message': message, 'workers/tag': tag}
        if self.fail_stage:
            self.fail_stage = False
            raise ArtifactError('provider_request_failed')
        return version

    def version_detail(self, version_id):
        self.calls.append('detail')
        if version_id == VERSION_A:
            return {'resources': {'bindings': BINDINGS,
                    'script': {'etag': 'e' * 64, 'handlers': promotion.WORKER_HANDLERS['handlers'],
                               'named_handlers': [{'name': name} for name in
                                                  promotion.WORKER_HANDLERS['named_handlers']]},
                    'script_runtime': {'compatibility_date': '2026-09-04'}}}
        bindings = [b for b in BINDINGS if not (self.drop_binding and b['type'] == 'd1')]
        return {'annotations': dict(self.staged_identity or {}),
                'resources': {'bindings': bindings,
                'script': {'etag': 'e' * 64, 'handlers': promotion.WORKER_HANDLERS['handlers'],
                           'named_handlers': [{'name': name} for name in
                                              promotion.WORKER_HANDLERS['named_handlers']]},
                'script_runtime': {'compatibility_date': '2026-09-04'}}}

    def activate_version(self, version_id, message):
        self.calls.append('activate:' + version_id)
        # A lost response mutates first and only then loses the reply.
        self.active = version_id
        if self.fail_activate and version_id != VERSION_A:
            self.fail_activate = False
            raise ArtifactError('provider_request_failed')


class ScriptedOpener:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []
    def open(self, request, timeout):
        self.calls.append(request.get_method() + ' ' + request.full_url)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def intent_outcomes(final):
    """Record transport script: empty history, create, observe, then both
    status posts (201) each followed by its confirmation read (200)."""
    item = {'id': 7, 'sha': '2' * 40, 'environment': promotion.ENVIRONMENT, 'task': promotion.TASK,
            'production_environment': True, 'payload': {'kind': 'commons-promotion-intent',
            'schema_version': 1, 'commit': '2' * 40, 'predecessor_version': VERSION_A,
            'predecessor_deployment_id': LIVE_DEPLOYMENT, 'predecessor_generation': 3,
            'plan_sha256': 'a' * 64, 'module_count': len(MODULES)}}
    statuses_url = deployments.API + deployments.BASE + '/deployments/7/statuses?per_page=1&page=1'
    state, description = {'promoted': ('success', promotion.PROMOTED),
                          'rolled_back': ('failure', promotion.ROLLED_BACK),
                          'unresolved': ('error', promotion.UNRESOLVED)}[final]
    return [
        FakeResponse([], deployments.API + promotion.LIST),
        FakeResponse({'id': 7}, deployments.API + deployments.BASE + '/deployments', status=201),
        FakeResponse(item, deployments.API + deployments.BASE + '/deployments/7'),
        FakeResponse({'id': 71}, deployments.API + deployments.BASE + '/deployments/7/statuses', status=201),
        FakeResponse([{'state': 'in_progress', 'description': promotion.IN_PROGRESS}], statuses_url),
        FakeResponse({'id': 72}, deployments.API + deployments.BASE + '/deployments/7/statuses', status=201),
        FakeResponse([{'state': state, 'description': description}], statuses_url),
    ]


class PromotionTests(unittest.TestCase):
    def test_happy_path_stages_verifies_activates_and_closes_promoted(self):
        adapter = FakeAdapter()
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('promoted')),
                                          )
        result = promotion.promote(adapter, records, plan(), candidate(), lambda sha: True)
        self.assertTrue(result['promoted'])
        self.assertEqual(adapter.active, result['staged_version'])
        self.assertNotIn('activate:' + VERSION_A, adapter.calls)
        self.assertEqual(records.opener.calls.count('POST ' + deployments.API
                         + deployments.BASE + '/deployments/7/statuses'), 2)

    def test_lost_stage_response_is_observed_by_annotation_and_promotes(self):
        adapter = FakeAdapter(fail_stage=True)
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('promoted')),
                                          )
        result = promotion.promote(adapter, records, plan(), candidate(), lambda sha: True)
        self.assertTrue(result['promoted'])
        self.assertEqual(adapter.calls.count('stage'), 1)

    def test_changed_staged_bindings_abort_before_activation(self):
        adapter = FakeAdapter(drop_binding=True)
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('rolled_back')),
                                          )
        accepted = []
        def accept(sha):
            accepted.append(sha)
            return True
        result = promotion.promote(adapter, records, plan(), candidate(), accept, pause=0)
        self.assertFalse(result['promoted'])
        self.assertEqual(result['error'], 'staged_bindings_changed')
        self.assertNotIn('activate:', ' '.join(adapter.calls))
        self.assertEqual(adapter.active, VERSION_A)
        # The rolled-back close carries its own evidence: the unchanged
        # predecessor passed the same bounded live acceptance.
        self.assertEqual(accepted, [plan()['release_sha']])

    def test_pre_activation_refusal_without_predecessor_health_stays_unresolved(self):
        adapter = FakeAdapter(drop_binding=True)
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('unresolved')),
                                          )
        accepted = []
        def accept(sha):
            accepted.append(sha)
            return False
        with self.assertRaisesRegex(ArtifactError, 'staged_bindings_changed'):
            promotion.promote(adapter, records, plan(), candidate(), accept, pause=0)
        self.assertNotIn('activate:', ' '.join(adapter.calls))
        self.assertEqual(adapter.active, VERSION_A)
        # An unchanged predecessor that fails its own health check may not be
        # claimed as restored and verified.
        self.assertEqual(accepted, [plan()['release_sha']] * 3)

    def test_acceptance_retries_survive_edge_propagation(self):
        adapter = FakeAdapter()
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('promoted')),
                                          )
        accepted = []
        def accept(sha):
            accepted.append(sha)
            return len(accepted) > 1
        result = promotion.promote(adapter, records, plan(), candidate(), accept, pause=0)
        self.assertTrue(result['promoted'])
        self.assertEqual(accepted, [candidate()['commit'], candidate()['commit']])
        self.assertEqual(adapter.active, result['staged_version'])

    def test_permanent_acceptance_failure_restores_predecessor_once(self):
        adapter = FakeAdapter()
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('rolled_back')),
                                          )
        accepted = []
        def accept(sha):
            accepted.append(sha)
            return sha != candidate()['commit']
        result = promotion.promote(adapter, records, plan(), candidate(), accept, pause=0)
        self.assertFalse(result['promoted'])
        self.assertEqual(adapter.active, VERSION_A)
        self.assertEqual(adapter.calls.count('activate:' + VERSION_A), 1)
        self.assertEqual(accepted.count(candidate()['commit']), 3)
        self.assertEqual(accepted.count(plan()['release_sha']), 1)

    def test_failed_rollback_acceptance_stays_unresolved_and_blocks(self):
        adapter = FakeAdapter()
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('unresolved')),
                                          )
        accepted = []
        def accept(sha):
            accepted.append(sha)
            return False
        with self.assertRaisesRegex(ArtifactError, 'rollback_acceptance_failed'):
            promotion.promote(adapter, records, plan(), candidate(), accept, pause=0)
        self.assertEqual(adapter.active, VERSION_A)
        self.assertEqual(adapter.calls.count('activate:' + VERSION_A), 1)
        self.assertEqual(accepted, [candidate()['commit']] * 3 + [plan()['release_sha']] * 3)
        # The durable journal keeps the unresolved close, and the next
        # promotion intent is refused until an operator reconciles the record.
        item = {'id': 7, 'environment': promotion.ENVIRONMENT, 'task': promotion.TASK,
                'production_environment': True, 'payload': {'kind': 'commons-promotion-intent'}}
        listed = FakeResponse([item], deployments.API + promotion.LIST)
        statuses_url = deployments.API + deployments.BASE + '/deployments/7/statuses?per_page=1&page=1'
        unresolved = FakeResponse([{'state': 'error', 'description': promotion.UNRESOLVED}], statuses_url)
        journal = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                            ScriptedOpener([listed, unresolved]))
        self.assertEqual(promotion.open_intent(journal), 7)
        with self.assertRaisesRegex(ArtifactError, 'unfinished_promotion'):
            promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                      ScriptedOpener([listed, unresolved])).start('4' * 40, {})

    def test_lost_activation_is_observed_then_promoted(self):
        adapter = FakeAdapter(fail_activate=True)
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('promoted')),
                                          )
        result = promotion.promote(adapter, records, plan(), candidate(), lambda sha: True)
        self.assertTrue(result['promoted'])
        self.assertEqual(adapter.calls.count('activate:' + result['staged_version']), 1)

    def test_intervening_active_version_refuses_without_staging_or_overrunning(self):
        adapter = FakeAdapter()
        adapter.active = 'intervening-operator-version'
        adapter.latest = 'intervening-operator-version'
        adapter.deployment_id = 'intervening-deployment'
        adapter.versions['intervening-operator-version'] = {'number': 9, 'annotations': {}}
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('unresolved')),
                                          )
        with self.assertRaisesRegex(ArtifactError, 'stale_preconditions'):
            promotion.promote(adapter, records, plan(), candidate(), lambda sha: True, pause=0)
        # The foreign version was neither staged over nor activated over, and
        # the journal keeps the unresolved close that blocks the next intent.
        self.assertNotIn('stage', adapter.calls)
        self.assertNotIn('activate:', ' '.join(adapter.calls))
        self.assertEqual(adapter.active, 'intervening-operator-version')

    def test_changed_deployment_before_stage_refuses_and_rolls_back(self):
        adapter = FakeAdapter()
        adapter.deployment_id = 'operator-redeployed-the-same-version'
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('rolled_back')),
                                          )
        accepted = []
        def accept(sha):
            accepted.append(sha)
            return True
        result = promotion.promote(adapter, records, plan(), candidate(), accept, pause=0)
        self.assertFalse(result['promoted'])
        self.assertEqual(result['error'], 'stale_preconditions')
        self.assertNotIn('stage', adapter.calls)
        self.assertEqual(adapter.active, VERSION_A)
        # The unchanged predecessor passed its own bounded acceptance, so the
        # pre-staging refusal may close as rolled back.
        self.assertEqual(accepted, [plan()['release_sha']])

    def test_foreign_upload_while_staging_refuses_activation(self):
        adapter = FakeAdapter(foreign_upload_after_stage=True)
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('rolled_back')),
                                          )
        accepted = []
        def accept(sha):
            accepted.append(sha)
            return True
        result = promotion.promote(adapter, records, plan(), candidate(), accept, pause=0)
        # This call's own staged upload is the only permitted change; the
        # foreign pending version refuses the activation before it happens.
        self.assertFalse(result['promoted'])
        self.assertEqual(result['error'], 'stale_preconditions')
        self.assertIn('stage', adapter.calls)
        self.assertNotIn('activate:', ' '.join(adapter.calls))
        self.assertEqual(adapter.active, VERSION_A)
        self.assertEqual(accepted, [plan()['release_sha']])

    def test_foreign_activation_while_staging_stays_unresolved(self):
        adapter = FakeAdapter(foreign_activation_after_stage=True)
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('unresolved')),
                                          )
        with self.assertRaisesRegex(ArtifactError, 'stale_preconditions'):
            promotion.promote(adapter, records, plan(), candidate(), lambda sha: True, pause=0)
        self.assertIn('stage', adapter.calls)
        self.assertNotIn('activate:', ' '.join(adapter.calls))
        # The intervening operator version stayed active; nothing overran it.
        self.assertEqual(adapter.active, 'intervening-operator-version')

    def test_an_open_intent_blocks_and_closed_records_free_the_next_promotion(self):
        item = {'id': 7, 'environment': promotion.ENVIRONMENT, 'task': promotion.TASK,
                'production_environment': True, 'payload': {'kind': 'commons-promotion-intent'}}
        listed = FakeResponse([item], deployments.API + promotion.LIST)
        statuses_url = deployments.API + deployments.BASE + '/deployments/7/statuses?per_page=1&page=1'
        cases = [
            ([{'state': 'in_progress', 'description': promotion.IN_PROGRESS}], 7),
            ([{'state': 'error', 'description': promotion.UNRESOLVED}], 7),
            ([{'state': 'success', 'description': promotion.PROMOTED}], None),
            ([{'state': 'failure', 'description': promotion.ROLLED_BACK}], None),
            ([{'state': 'success', 'description': 'unrelated'}], 'promotion_record_closed'),
            ([{'state': 'failure', 'description': promotion.UNRESOLVED}], 'promotion_record_closed'),
        ]
        for statuses, expected in cases:
            records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                                ScriptedOpener([listed, FakeResponse(statuses, statuses_url)]))
            if expected == 'promotion_record_closed':
                with self.assertRaisesRegex(ArtifactError, 'promotion_record_closed'):
                    promotion.open_intent(records)
            else:
                self.assertEqual(promotion.open_intent(records), expected)
        for state, description in [('in_progress', 'unrelated'), ('error', promotion.PROMOTED),
                                   ('queued', promotion.IN_PROGRESS)]:
            records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                                ScriptedOpener([listed, FakeResponse(
                                                    [{'state': state, 'description': description}], statuses_url)]))
            with self.assertRaisesRegex(ArtifactError, 'promotion_record_closed'):
                promotion.open_intent(records)

    def test_foreign_records_are_never_adopted(self):
        item = {'id': 7, 'environment': promotion.ENVIRONMENT, 'task': 'other',
                'production_environment': True, 'payload': {'kind': 'commons-promotion-intent'}}
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener([FakeResponse([item], deployments.API + promotion.LIST)]),
                                          )
        with self.assertRaisesRegex(ArtifactError, 'invalid_promotion_record'):
            promotion.open_intent(records)


class GitObjects:
    """Synthetic Git object transport: one commit, one recursive tree read."""

    def __init__(self, commit, tree_sha, entries, truncated=False, refuse_commit=False):
        self.commit, self.tree_sha, self.entries = commit, tree_sha, entries
        self.truncated, self.refuse_commit = truncated, refuse_commit
        self.calls = []

    def get(self, route):
        self.calls.append(route)
        if self.refuse_commit and route.endswith('/git/commits/' + self.commit):
            raise ArtifactError('github_read_failed')
        if route.endswith('/git/commits/' + self.commit):
            return {'sha': self.commit, 'tree': {'sha': self.tree_sha}}
        if route.endswith('/git/trees/' + self.tree_sha + '?recursive=1'):
            return {'sha': self.tree_sha, 'truncated': self.truncated,
                    'tree': [dict(entry) for entry in self.entries]}
        raise ArtifactError('github_read_failed')


def git_evidence(names, commit, *, drifted=None, wrong_mode=False, truncated=False,
                 refuse_commit=False, entry_type='blob'):
    """Build the tree entries and transport for a commit carrying `names`."""
    entries = []
    for name in sorted(names):
        raw = f'console.log("{name}");'.encode()
        sha = 'f' * 40 if drifted == name else hashlib.sha1(
            b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        entries.append({'path': 'services/commons/' + name,
                        'mode': '100644' if not wrong_mode else '100755',
                        'type': entry_type, 'sha': sha})
    return GitObjects(commit, '5' * 40, entries, truncated=truncated, refuse_commit=refuse_commit)


class PredecessorTests(unittest.TestCase):
    def multipart(self, names, boundary='----Px'):
        parts = ''.join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{n}"; filename="{n}"'
            f'\r\nContent-Type: application/javascript+module\r\n\r\nconsole.log("{n}");\r\n'
            for n in names)
        return parts.encode() + f'--{boundary}--'.encode()

    def test_predecessor_packet_is_rebuilt_from_git_bound_live_content(self):
        import commons_artifact as artifact
        import commons_rehearsal as rehearsal
        content = self.multipart(sorted(artifact.MODULES))
        github = git_evidence(artifact.MODULES, '3' * 40)
        packet = promotion.reconstruct_predecessor(content, '3' * 40, github)
        restored, descriptor = artifact.unpack(packet, '3' * 40, rehearsal.SCHEMA_SHA256, modules=None)
        self.assertEqual(set(restored), set(artifact.MODULES))
        self.assertTrue(all(v.startswith(b'console.log(') for v in restored.values()))
        self.assertEqual(descriptor['commit'], '3' * 40)
        # Exactly the two bounded evidence reads happened, commit then tree.
        self.assertEqual(len(github.calls), 2)
        self.assertTrue(github.calls[0].endswith('/git/commits/' + '3' * 40))
        self.assertIn('/git/trees/' + '5' * 40 + '?recursive=1', github.calls[1])

    def test_older_predecessor_module_profile_binds_to_its_own_commit(self):
        import commons_artifact as artifact
        import commons_rehearsal as rehearsal
        older = {'worker.mjs', 'security.mjs', 'identity.mjs', 'participations.mjs',
                 'activity.mjs', 'work-items.mjs'}
        content = self.multipart(sorted(older))
        packet = promotion.reconstruct_predecessor(content, '3' * 40,
                                                   git_evidence(older, '3' * 40))
        restored, descriptor = artifact.unpack(packet, '3' * 40, rehearsal.SCHEMA_SHA256, modules=None)
        self.assertEqual(set(restored), older)
        self.assertEqual(set(descriptor['modules']), older)

    def test_live_bytes_drifted_under_the_claimed_commit_refuse(self):
        import commons_artifact as artifact
        content = self.multipart(sorted(artifact.MODULES))
        # The provider serves changed bytes while RELEASE_SHA still claims the
        # old commit; the tree's blob for worker.mjs does not match them.
        github = git_evidence(artifact.MODULES, '3' * 40, drifted='worker.mjs')
        with self.assertRaisesRegex(ArtifactError, 'predecessor_source_mismatch'):
            promotion.reconstruct_predecessor(content, '3' * 40, github)

    def test_installed_profile_must_be_exactly_the_commit_s_modules(self):
        import commons_artifact as artifact
        full = self.multipart(sorted(artifact.MODULES))
        # The live install carries a module the claimed commit never had.
        with self.assertRaisesRegex(ArtifactError, 'predecessor_source_mismatch'):
            promotion.reconstruct_predecessor(
                full, '3' * 40, git_evidence(artifact.MODULES - {'receipts.mjs'}, '3' * 40))
        # The claimed commit has a production module the live install dropped.
        partial = self.multipart(sorted(artifact.MODULES - {'receipts.mjs'}))
        with self.assertRaisesRegex(ArtifactError, 'predecessor_source_mismatch'):
            promotion.reconstruct_predecessor(partial, '3' * 40, git_evidence(artifact.MODULES, '3' * 40))

    def test_missing_or_malformed_git_evidence_fails_closed(self):
        import commons_artifact as artifact
        content = self.multipart(sorted(artifact.MODULES))
        # The evidence commit is not readable (a shallow CI checkout has no
        # local objects either); the reconstruction refuses, never self-certifies.
        with self.assertRaisesRegex(ArtifactError, 'github_read_failed'):
            promotion.reconstruct_predecessor(
                content, '3' * 40, git_evidence(artifact.MODULES, '3' * 40, refuse_commit=True))
        # A truncated tree, a non-blob entry and a wrong entry mode all refuse.
        with self.assertRaisesRegex(ArtifactError, 'predecessor_source_unverified'):
            promotion.reconstruct_predecessor(
                content, '3' * 40, git_evidence(artifact.MODULES, '3' * 40, truncated=True))
        with self.assertRaisesRegex(ArtifactError, 'predecessor_source_mismatch'):
            promotion.reconstruct_predecessor(
                content, '3' * 40, git_evidence(artifact.MODULES, '3' * 40, entry_type='tree'))
        with self.assertRaisesRegex(ArtifactError, 'predecessor_source_mismatch'):
            promotion.reconstruct_predecessor(
                content, '3' * 40, git_evidence(artifact.MODULES, '3' * 40, wrong_mode=True))

    def test_predecessor_reconstruction_refuses_damaged_content(self):
        import commons_artifact as artifact
        content = self.multipart(sorted(artifact.MODULES))
        github = git_evidence(artifact.MODULES, '3' * 40)
        truncated = content[:content.rfind(b'--' + b'----Px')]
        for broken in [b'', b'no-multipart', truncated + b'trailing',
                       content.replace(b'console.log', b'console\x00log', 1)]:
            with self.assertRaises(ArtifactError):
                promotion.reconstruct_predecessor(broken, '3' * 40, github)


class WiringPrimitiveTests(unittest.TestCase):
    def test_rehearsal_artifacts_locate_by_exact_names_and_refuse_ambiguity(self):
        sha, run_id = '2' * 40, 555
        class GitHub:
            def __init__(self, artifacts):
                self.artifacts = artifacts
            def get(self, route):
                assert f'/actions/runs/{run_id}/artifacts' in route
                return {'artifacts': self.artifacts}
        good = [
            {'name': f'commons-candidate-{sha}-{run_id}-1', 'id': 71},
            {'name': f'commons-rehearsal-receipt-{sha}-{run_id}-1', 'id': 72},
            {'name': 'unrelated-artifact', 'id': 73},
        ]
        self.assertEqual(promotion.rehearsal_artifacts(GitHub(good), sha, run_id, 1),
                         {'candidate': 71, 'receipt': 72})
        for broken in [[], good[:1], good + [good[0]]]:
            with self.assertRaisesRegex(ArtifactError, 'invalid_rehearsal_artifacts'):
                promotion.rehearsal_artifacts(GitHub(broken), sha, run_id, 1)

    def test_provider_generation_maps_the_active_version_number(self):
        self.assertEqual(promotion.provider_generation(
            {'active_version': 'v-b', 'versions': {'v-a': {'number': 4}, 'v-b': {'number': 5}}}), 5)
        for broken in [{}, {'active_version': 'v-x', 'versions': {}},
                       {'active_version': 'v-b', 'versions': {'v-b': {}}},
                       {'active_version': 'v-b', 'versions': {'v-b': {'number': 0}}},
                       {'active_version': 'v-b', 'versions': {'v-b': {'number': '5'}}}]:
            with self.assertRaisesRegex(ArtifactError, 'provider_state_unverified'):
                promotion.provider_generation(broken)


class LiveShapeAdapter:
    """Synthetic provider whose reads carry the live API's exact response shapes."""
    def __init__(self, case):
        self.case = case
        self.calls = []

    def observe(self):
        self.calls.append('observe')
        return {**self.case['live']}

    def version_detail(self, version_id):
        self.calls.append('detail:' + version_id)
        assert version_id == self.case['live']['active_version']
        return {**self.case['detail'], 'resources': {**self.case['detail']['resources'],
                'bindings': [dict(item) for item in self.case['raw_bindings']]}}

    def script_settings(self):
        self.calls.append('settings')
        return {**self.case['settings_raw'], 'bindings': [dict(item) for item in self.case['raw_bindings']]}

    def script_subdomain(self):
        self.calls.append('subdomain')
        return dict(self.case['subdomain'])

    def schema_rows(self, query):
        self.calls.append('schema')
        assert query == artifact.SCHEMA_QUERY
        return [dict(item) for item in self.case['result_sets']]


class CaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / 'services/commons'
        files, migrations = artifact.source_inputs(root)
        schema = artifact.expected_schema(migrations)
        with sqlite3.connect(':memory:') as database:
            database.row_factory = sqlite3.Row
            for name in sorted(migrations):
                database.executescript(migrations[name].decode())
            rows = [dict(row) for row in database.execute(artifact.SCHEMA_QUERY)]
        packet = artifact.packet(files, OLD, schema)
        _files, descriptor = artifact.unpack(packet, OLD, planner.SCHEMA_SHA256)
        policy = {'schema_version': 1, 'target': planner.TARGET, 'account_id': 'a' * 32,
                  'zone_id': 'b' * 32, 'script_name': planner.SCRIPT,
                  'database_id': DATABASE, 'route_id': 'c' * 32}
        raw_bindings = [
            {'name': 'ADMIN_TOKEN', 'type': 'secret_text'},
            {'name': 'GITHUB_READ_TOKEN', 'type': 'secret_text'},
            {'database_id': DATABASE, 'id': DATABASE, 'name': 'DB', 'type': 'd1'},
            {'name': 'IP_HMAC_SECRET', 'type': 'secret_text'},
            {'name': 'PUBLIC_ORIGIN', 'text': 'https://oss-singularity.io', 'type': 'plain_text'},
            {'name': 'RELEASE_SHA', 'text': OLD, 'type': 'plain_text'},
        ]
        cls.case = {
            'packet': packet, 'descriptor': descriptor, 'rows': rows, 'policy': policy,
            'raw_bindings': raw_bindings,
            'live': {
                'schema_version': 1, 'target': planner.SCRIPT, 'account_id': 'a' * 32,
                'zone_id': 'b' * 32, 'script_name': planner.SCRIPT,
                'active_version': VERSION, 'latest_version_id': VERSION,
                'versions': {VERSION: {'number': 4, 'metadata': {}, 'annotations': {}}},
                'deployments': [{'id': DEPLOYMENT, 'strategy': 'percentage',
                                 'versions': [{'version_id': VERSION, 'percentage': 100}]}],
                'routes': [{'id': 'c' * 32, 'pattern': 'oss-singularity.io/api/*',
                            'script': planner.SCRIPT, 'request_limit_fail_open': False}],
                'schedules': [{'cron': '17 * * * *', 'created_on': '2026-09-05T08:07:13.971361Z'}],
                'subdomain': 'mail-85f', 'd1_schema_fingerprint': 'fp',
            },
            'detail': {'resources': {
                'bindings': raw_bindings,
                'script': {'etag': 'd' * 64, 'handlers': ['fetch', 'scheduled'],
                           'last_deployed_from': 'api'},
                'script_runtime': {'compatibility_date': '2026-09-04', 'usage_model': 'standard'}}},
            'settings_raw': {
                'annotations': {'workers/message': 'installed', 'workers/triggered_by': 'version_upload'},
                'compatibility_date': '2026-09-04', 'compatibility_flags': [], 'logpush': False,
                'placement': {}, 'tags': ['commons', 'oss-singularity'], 'tail_consumers': [],
                'usage_model': 'standard'},
            'subdomain': {'enabled': False, 'previews_enabled': False},
            'result_sets': [{'meta': {}, 'success': True, 'results': rows}],
        }

    def adapter(self, **live_changes):
        case = {**self.case, 'live': {**self.case['live'], **live_changes}}
        return LiveShapeAdapter(case)

    def capture(self, adapter):
        return promotion.capture_observation(adapter, self.case['descriptor'], artifact.SCHEMA_QUERY)

    def test_capture_matches_the_planner_model_exactly(self):
        adapter = self.adapter()
        captured = self.capture(adapter)
        self.assertEqual(adapter.calls, ['observe', 'detail:' + VERSION, 'settings', 'subdomain', 'schema'])
        self.assertEqual(captured['generation'], 4)
        observation = captured['observation']
        expected_settings = planner.settings(self.case['policy'], OLD)
        self.assertEqual(observation['settings'], expected_settings)
        self.assertEqual(observation['version']['bindings'], expected_settings['bindings'])
        self.assertEqual(observation['version']['etag'], 'd' * 64)
        self.assertEqual(observation['version']['runtime'],
                         {'compatibility_date': '2026-09-04', 'compatibility_flags': [],
                          'usage_model': 'standard'})
        self.assertEqual(observation['deployment'],
                         {'id': DEPLOYMENT, 'strategy': 'percentage',
                          'versions': [{'version_id': VERSION, 'percentage': 100}]})
        self.assertEqual(observation['routes'], self.case['live']['routes'])
        self.assertEqual(observation['schedules'], ['17 * * * *'])
        self.assertEqual(observation['subdomain'], {'enabled': False, 'previews_enabled': False})
        self.assertEqual(observation['modules'], self.case['descriptor']['modules'])
        self.assertEqual(observation['schema'], self.case['rows'])
        self.assertEqual(observation['latest_version_id'], VERSION)

    def test_baseline_binds_generation_packets_and_normalized_state(self):
        captured = self.capture(self.adapter())
        baseline = promotion.plan_baseline(captured['generation'], OLD, self.case['packet'],
                                           self.case['descriptor'], self.case['policy'],
                                           captured['observation'])
        state = planner.observed_state(captured['observation'], self.case['policy'],
                                       self.case['descriptor'])
        self.assertEqual(baseline, {
            'schema_version': 1, 'target': planner.TARGET, 'generation': 4, 'commit': OLD,
            'packet_sha256': artifact.digest(self.case['packet']),
            'policy_sha256': artifact.digest(artifact.encode(self.case['policy'])),
            'observation_sha256': artifact.digest(artifact.encode(state)),
            'version_id': VERSION, 'deployment_id': DEPLOYMENT})

    def test_unowned_pending_versions_refuse_before_any_intent_exists(self):
        captured = self.capture(self.adapter(latest_version_id=PENDING_VERSION))
        with self.assertRaisesRegex(ArtifactError, 'unowned_pending_version'):
            promotion.plan_baseline(captured['generation'], OLD, self.case['packet'],
                                    self.case['descriptor'], self.case['policy'],
                                    captured['observation'])

    def test_capture_refuses_unknown_provider_noise(self):
        settings_variants = [
            {**self.case['settings_raw'], 'observability': {'enabled': True}},
            {**self.case['settings_raw'], 'unknown_field': 1},
            {key: value for key, value in self.case['settings_raw'].items() if key != 'logpush'},
        ]
        for settings in settings_variants:
            case = {**self.case, 'settings_raw': settings}
            with self.assertRaisesRegex(ArtifactError, 'provider_state_unverified'):
                promotion.capture_observation(LiveShapeAdapter(case), self.case['descriptor'],
                                              artifact.SCHEMA_QUERY)
        for bindings in [[{'name': 'DB', 'type': 'kv', 'id': 'x'}],
                         [{'name': 'RELEASE_SHA', 'type': 'plain_text', 'text': OLD, 'id': 'x'}],
                         [{'name': 'DB', 'type': 'd1', 'id': DATABASE, 'database_id': 'other'}],
                         []]:
            case = {**self.case, 'raw_bindings': bindings}
            with self.assertRaisesRegex(ArtifactError, 'provider_state_unverified'):
                promotion.capture_observation(LiveShapeAdapter(case), self.case['descriptor'],
                                              artifact.SCHEMA_QUERY)
        for detail in [{'resources': {'bindings': self.case['raw_bindings'],
                                      'script': {'etag': 'short'},
                                      'script_runtime': {'compatibility_date': '2026-09-04',
                                                         'usage_model': 'standard'}}},
                       {'resources': {'bindings': self.case['raw_bindings'],
                                      'script': {'etag': 'd' * 64}}}
                       ]:
            case = {**self.case, 'detail': detail}
            with self.assertRaisesRegex(ArtifactError, 'provider_state_unverified'):
                promotion.capture_observation(LiveShapeAdapter(case), self.case['descriptor'],
                                              artifact.SCHEMA_QUERY)
        for subdomain in [{'enabled': False}, {'enabled': False, 'previews_enabled': False, 'name': 'x'},
                          {'enabled': 'no', 'previews_enabled': False}]:
            case = {**self.case, 'subdomain': subdomain}
            with self.assertRaisesRegex(ArtifactError, 'provider_state_unverified'):
                promotion.capture_observation(LiveShapeAdapter(case), self.case['descriptor'],
                                              artifact.SCHEMA_QUERY)
        for sets in [[], [{'success': False, 'results': []}], [{'success': True, 'results': []}]]:
            case = {**self.case, 'result_sets': sets}
            with self.assertRaisesRegex(ArtifactError, 'provider_state_unverified'):
                promotion.capture_observation(LiveShapeAdapter(case), self.case['descriptor'],
                                              artifact.SCHEMA_QUERY)
        case = {**self.case, 'live': {**self.case['live'], 'deployments': []}}
        with self.assertRaisesRegex(ArtifactError, 'provider_state_unverified'):
            promotion.capture_observation(LiveShapeAdapter(case), self.case['descriptor'],
                                          artifact.SCHEMA_QUERY)


class EnginePlanTests(unittest.TestCase):
    def planned(self, **changes):
        value = {
            'predecessor': {'version_id': VERSION, 'deployment_id': DEPLOYMENT},
            'expected_generation': 4,
            'release_sha_change': {'before': OLD, 'after': NEW},
            'desired_version': {
                'bindings': {'DB': {'type': 'd1', 'id': DATABASE},
                             'PUBLIC_ORIGIN': {'type': 'plain_text', 'text': 'https://oss-singularity.io'},
                             'RELEASE_SHA': {'type': 'plain_text', 'text': NEW},
                             'ADMIN_TOKEN': {'type': 'secret_text'}, 'GITHUB_READ_TOKEN': {'type': 'secret_text'},
                             'IP_HMAC_SECRET': {'type': 'secret_text'}},
                'runtime': {'compatibility_date': '2026-09-04'}},
            'plan_sha256': 'a' * 64,
        }
        value.update(changes)
        return value

    def test_release_sha_is_reentered_and_everything_else_inherits(self):
        plan = promotion.engine_plan(NEW, self.planned(), 'Promote the candidate', 'the-tag')
        self.assertEqual(plan['predecessor_version'], VERSION)
        self.assertEqual(plan['predecessor_deployment_id'], DEPLOYMENT)
        self.assertEqual(plan['predecessor_generation'], 4)
        self.assertEqual(plan['release_sha'], OLD)
        self.assertEqual(plan['message'], 'Promote the candidate')
        self.assertEqual(plan['tag'], 'the-tag')
        self.assertEqual(plan['bindings'], [
            {'name': 'ADMIN_TOKEN', 'type': 'inherit'},
            {'name': 'DB', 'type': 'inherit'}, {'name': 'GITHUB_READ_TOKEN', 'type': 'inherit'},
            {'name': 'IP_HMAC_SECRET', 'type': 'inherit'},
            {'name': 'PUBLIC_ORIGIN', 'type': 'inherit'},
            {'name': 'RELEASE_SHA', 'type': 'plain_text', 'text': NEW},
        ])
        self.assertEqual(plan['installed_bindings'], [
            {'name': 'ADMIN_TOKEN', 'type': 'secret_text'},
            {'name': 'DB', 'type': 'd1', 'id': DATABASE},
            {'name': 'GITHUB_READ_TOKEN', 'type': 'secret_text'},
            {'name': 'IP_HMAC_SECRET', 'type': 'secret_text'},
            {'name': 'PUBLIC_ORIGIN', 'type': 'plain_text', 'text': 'https://oss-singularity.io'},
            {'name': 'RELEASE_SHA', 'type': 'plain_text', 'text': NEW},
        ])
        self.assertEqual(plan['main_module'], 'worker.mjs')
        self.assertEqual(plan['compatibility_date'], '2026-09-04')
        self.assertEqual(plan['plan_sha256'], 'a' * 64)

    def test_refuses_mismatched_release_identity(self):
        with self.assertRaisesRegex(ArtifactError, 'invalid_plan'):
            promotion.engine_plan(OLD, self.planned(), 'm', 't')
        stale = self.planned()
        stale['desired_version']['bindings']['RELEASE_SHA'] = {'type': 'plain_text', 'text': OLD}
        with self.assertRaisesRegex(ArtifactError, 'invalid_plan'):
            promotion.engine_plan(NEW, stale, 'm', 't')
        missing = self.planned()
        del missing['desired_version']['bindings']['RELEASE_SHA']
        with self.assertRaisesRegex(ArtifactError, 'invalid_plan'):
            promotion.engine_plan(NEW, missing, 'm', 't')

    def test_refuses_plans_without_the_fresh_precondition_pins(self):
        without_deployment = self.planned()
        del without_deployment['predecessor']['deployment_id']
        with self.assertRaisesRegex(ArtifactError, 'invalid_plan'):
            promotion.engine_plan(NEW, without_deployment, 'm', 't')
        without_generation = self.planned()
        del without_generation['expected_generation']
        with self.assertRaisesRegex(ArtifactError, 'invalid_plan'):
            promotion.engine_plan(NEW, without_generation, 'm', 't')
        for broken in [0, '4', 2**63, None]:
            bad_generation = self.planned()
            bad_generation['expected_generation'] = broken
            with self.assertRaisesRegex(ArtifactError, 'invalid_plan'):
                promotion.engine_plan(NEW, bad_generation, 'm', 't')


if __name__ == '__main__':
    unittest.main()
