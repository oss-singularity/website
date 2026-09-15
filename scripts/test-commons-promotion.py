"""Offline promotion engine tests against a synthetic provider and records."""
import json
import unittest

import commons_promotion as promotion
import release_deployment as deployments
import release_source as source
from site_artifact import ArtifactError

VERSION_A = 'aaaa-version-a'
MODULES = ['worker.mjs', 'util.mjs']
BINDINGS = [
    {'name': 'DB', 'type': 'd1', 'id': 'd1-uuid'},
    {'name': 'ADMIN_TOKEN', 'type': 'secret_text'},
    {'name': 'PUBLIC_ORIGIN', 'type': 'plain_text', 'text': 'https://oss-singularity.io'},
]


def plan(**changes):
    value = {
        'predecessor_version': VERSION_A, 'release_sha': '1' * 40,
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
    def __init__(self, *, fail_stage=False, fail_activate=False, drop_binding=False):
        self.active = VERSION_A
        self.versions = {VERSION_A: {'annotations': {'workers/message': 'installed', 'workers/tag': 'installed'}}}
        self.calls = []
        self.fail_stage, self.fail_activate, self.drop_binding = fail_stage, fail_activate, drop_binding

    def observe(self):
        return {'active_version': self.active, 'versions': self.versions}

    def stage_version(self, content, commit, message, tag, bindings=None,
                      main_module='worker.mjs', compatibility_date=None):
        self.calls.append('stage')
        version = 'stag-' + commit[:4]
        self.versions[version] = {'annotations': {'workers/message': message, 'workers/tag': tag}}
        if self.fail_stage:
            self.fail_stage = False
            raise ArtifactError('provider_request_failed')
        return version

    def version_detail(self, version_id):
        self.calls.append('detail')
        if version_id == VERSION_A:
            return {'resources': {'bindings': BINDINGS,
                    'script': {'modules': [{'name': 'installed.mjs'}]},
                    'script_runtime': {'compatibility_date': '2026-09-04'}}}
        bindings = [b for b in BINDINGS if not (self.drop_binding and b['type'] == 'd1')]
        return {'resources': {'bindings': bindings,
                'script': {'modules': [{'name': name} for name in MODULES]},
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
            'plan_sha256': 'a' * 64, 'module_count': len(MODULES)}}
    statuses_url = deployments.API + deployments.BASE + '/deployments/7/statuses?per_page=1&page=1'
    state, description = {'promoted': ('success', promotion.PROMOTED),
                          'rolled_back': ('failure', promotion.ROLLED_BACK)}[final]
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
        result = promotion.promote(adapter, records, plan(), candidate(), lambda sha: True)
        self.assertFalse(result['promoted'])
        self.assertNotIn('activate:', ' '.join(adapter.calls))
        self.assertEqual(adapter.active, VERSION_A)

    def test_failed_live_acceptance_restores_predecessor_once(self):
        adapter = FakeAdapter()
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('rolled_back')),
                                          )
        accepted = []
        def accept(sha):
            accepted.append(sha)
            return len(accepted) > 1
        result = promotion.promote(adapter, records, plan(), candidate(), accept)
        self.assertFalse(result['promoted'])
        self.assertEqual(adapter.active, VERSION_A)
        self.assertEqual(adapter.calls.count('activate:' + VERSION_A), 1)
        self.assertEqual(accepted[0], candidate()['commit'])
        self.assertEqual(accepted[-1], plan()['release_sha'])

    def test_lost_activation_is_observed_then_promoted(self):
        adapter = FakeAdapter(fail_activate=True)
        records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                          ScriptedOpener(intent_outcomes('promoted')),
                                          )
        result = promotion.promote(adapter, records, plan(), candidate(), lambda sha: True)
        self.assertTrue(result['promoted'])
        self.assertEqual(adapter.calls.count('activate:' + result['staged_version']), 1)

    def test_an_open_intent_blocks_and_start_never_runs_twice(self):
        item = {'id': 7, 'environment': promotion.ENVIRONMENT, 'task': promotion.TASK,
                'production_environment': True, 'payload': {'kind': 'commons-promotion-intent'}}
        listed = FakeResponse([item], deployments.API + promotion.LIST)
        in_progress = FakeResponse([{'state': 'in_progress', 'description': promotion.IN_PROGRESS}],
                                   deployments.API + deployments.BASE + '/deployments/7/statuses?per_page=1&page=1')
        unresolved = FakeResponse([{'state': 'error', 'description': promotion.UNRESOLVED}],
                                  deployments.API + deployments.BASE + '/deployments/7/statuses?per_page=1&page=1')
        for statuses in [in_progress, unresolved]:
            records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                              ScriptedOpener([listed, statuses]),
                                              )
            with self.assertRaisesRegex(ArtifactError, 'unfinished_promotion'):
                records.start(candidate()['commit'], {'kind': 'commons-promotion-intent'})
        for state, description in [('success', promotion.PROMOTED), ('failure', promotion.ROLLED_BACK)]:
            closed = FakeResponse([{'state': state, 'description': description}],
                                  deployments.API + deployments.BASE + '/deployments/7/statuses?per_page=1&page=1')
            records = promotion.PromotionIntent({'GH_TOKEN': 'synthetic-public-fixture'},
                                              ScriptedOpener([listed, closed]),
                                              )
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


if __name__ == '__main__':
    unittest.main()
