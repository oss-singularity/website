"""Durable Commons worker promotion: intent, staged verification, single mutations.

Implements the promotion procedure's engine (docs/release-commons-promotion.md,
steps 3-7) against one target Worker: a durable deployment-API intent that stays
open until the promotion closes, one staged upload whose server-side detail is
verified before activation, one activation attempt, bounded live acceptance and
one rollback through predecessor-version restore. Candidate consumption and
planning (procedure steps 1-2) are performed by the implemented contracts and
are expected here as their already-verified results. Mutations are never
repeated; a lost response is resolved by observing which version the deployment
serves, and any step that cannot be resolved closes the intent as unresolved.
"""
import time

from release_deployment import Deployments
from release_source import BASE, checks, encode
from site_artifact import ArtifactError, require

TASK = 'promote:oss-commons'
ENVIRONMENT = 'production-commons'
LIST = BASE + '/deployments?environment=' + ENVIRONMENT + '&task=promote%3Aoss-commons&per_page=1&page=1'
IN_PROGRESS = 'Promotion of a verified candidate is in progress.'
PROMOTED = 'Staged version verified server-side; live acceptance passed.'
ROLLED_BACK = 'Promotion did not pass; predecessor version restored and verified.'
UNRESOLVED = 'Outcome requires reconciliation before another promotion.'

EXPECTED_SECRET_TYPES = {'secret_text'}


def open_intent(deployments):
    """Return the latest intent number when only an open promotion blocks, else None.

    The one-writer discipline is the same as the static path: any existing
    record for this task without a closed status blocks the next promotion,
    and closed records keep their evidence.
    """
    items = deployments.request('GET', deployments.list)
    require(type(items) is list and len(items) <= 1, 'promotion_history_unverified')
    if not items:
        return None
    item = items[0]
    number = checks.positive(item.get('id'))
    require(item.get('environment') == ENVIRONMENT and item.get('task') == TASK
            and item.get('production_environment') is True, 'invalid_promotion_record')
    statuses = deployments.request('GET', BASE + '/deployments/' + str(number) + '/statuses?per_page=1&page=1')
    require(type(statuses) is list and len(statuses) <= 1, 'promotion_history_unverified')
    require(not statuses or (statuses[0].get('state'), statuses[0].get('description')) in
            {('in_progress', IN_PROGRESS), ('error', UNRESOLVED)}, 'promotion_record_closed')
    return number


class PromotionIntent(Deployments):
    """Deployment records under the promotion task and environment."""

    def __init__(self, environ, opener=None, pause=time.sleep):
        super().__init__(environ, opener, pause, environment=ENVIRONMENT, task=TASK)

    def start(self, sha, payload):
        """Record the promotion intent after re-reading the task's open state."""
        require(open_intent(self) is None, 'unfinished_promotion')
        result = self.request('POST', BASE + '/deployments', {
            'ref': sha, 'task': TASK, 'auto_merge': False, 'required_contexts': [], 'payload': payload,
            'environment': ENVIRONMENT, 'description': 'Promote the verified Commons candidate',
            'transient_environment': False, 'production_environment': True})
        number = checks.positive(result.get('id'))
        observed = self.request('GET', BASE + '/deployments/' + str(number))
        require(observed.get('sha') == sha and observed.get('task') == TASK
                and observed.get('payload') == payload, 'promotion_record_unconfirmed')
        self.finish(number, 'in_progress')
        return number

    def finish(self, number, outcome):
        checks.positive(number)
        states = {'in_progress': ('in_progress', IN_PROGRESS), 'promoted': ('success', PROMOTED),
                  'rolled_back': ('failure', ROLLED_BACK), 'unresolved': ('error', UNRESOLVED)}
        require(outcome in states, 'invalid_promotion_outcome')
        state, description = states[outcome]
        route = BASE + '/deployments/' + str(number) + '/statuses'
        self.request('POST', route, {'state': state, 'description': description,
            'environment': ENVIRONMENT, 'environment_url': 'https://oss-singularity.io/api/v1',
            'auto_inactive': False})
        latest = self.request('GET', route + '?per_page=1&page=1')
        require(type(latest) is list and len(latest) == 1 and latest[0].get('state') == state
                and latest[0].get('description') == description, 'promotion_record_unconfirmed')


def binding_fingerprint(bindings):
    """Reduce a binding list to the comparable (name, type, id) triples."""
    require(type(bindings) is list and 0 < len(bindings) <= 32, 'invalid_bindings')
    result = []
    for item in bindings:
        require(type(item) is dict and type(item.get('name')) is str and type(item.get('type')) is str,
                'invalid_bindings')
        result.append((item['name'], item['type'], item.get('id'), item.get('text')))
    return sorted(result)


def verify_staged(detail, candidate_modules, installed_bindings, compatibility_date):
    """Verify the staged version server-side before any activation.

    The staged version must carry exactly the candidate's module set, the
    installed bindings inherited unchanged (same names, types, D1 ids and
    secret presence), the installed compatibility date, and the same handlers
    the live script exposes.
    """
    require(type(detail) is dict, 'staged_version_unverified')
    resources = detail.get('resources')
    require(type(resources) is dict, 'staged_version_unverified')
    staged_bindings = binding_fingerprint(resources.get('bindings'))
    expected = binding_fingerprint(installed_bindings)
    for (name, kind, identifier, _text), (_ename, _ekind, _eid, _etext) in zip(staged_bindings, expected):
        require((name, kind) == (_ename, _ekind), 'staged_bindings_changed')
        if kind == 'd1':
            require(identifier == _eid, 'staged_bindings_changed')
        if kind in EXPECTED_SECRET_TYPES:
            require(identifier is None and _text is None, 'staged_bindings_changed')
    require(staged_bindings == expected, 'staged_bindings_changed')
    script = resources.get('script')
    require(type(script) is dict, 'staged_version_unverified')
    staged_modules = sorted(item.get('name') for item in script.get('modules', []) if type(item) is dict)
    require(staged_modules == sorted(candidate_modules), 'staged_modules_changed')
    runtime = detail.get('resources', {}).get('script_runtime')
    require(type(runtime) is dict and runtime.get('compatibility_date') == compatibility_date,
            'staged_settings_changed')
    return True


def find_staged(adapter, message, tag):
    """Observe the provider's version listing for this call's annotated upload."""
    versions = adapter.observe().get('versions', {})
    matches = [version for version, detail in versions.items()
               if detail.get('annotations', {}).get('workers/message') == message
               and detail.get('annotations', {}).get('workers/tag') == tag]
    require(len(matches) <= 1, 'ambiguous_staged_version')
    return matches[0] if matches else None


def promote(adapter, intent, plan, candidate, accept):
    """Run one promotion against an open target; close the intent whatever happens.

    `plan` supplies the predecessor version, the staged content and settings,
    the current live release sha and the expectation fingerprints; `candidate`
    supplies the commit, module names and packet content; `accept(sha)` performs
    the bounded live acceptance for an expected release identity and must raise
    on failure. Every mutation is attempted at most once per call; a lost
    response is resolved by observing the provider's state under this call's
    own identity annotations.
    """
    predecessor = plan['predecessor_version']
    require(type(predecessor) is str and len(predecessor) > 0, 'invalid_plan')
    require(type(plan.get('release_sha')) is str, 'invalid_plan')
    number = intent.start(candidate['commit'], {
        'kind': 'commons-promotion-intent', 'schema_version': 1, 'commit': candidate['commit'],
        'predecessor_version': predecessor, 'plan_sha256': plan.get('plan_sha256'),
        'module_count': len(candidate['modules'])})
    try:
        try:
            staged = adapter.stage_version(candidate['content'], candidate['commit'],
                                           plan['message'], plan['tag'], bindings=plan['bindings'],
                                           main_module=plan['main_module'],
                                           compatibility_date=plan['compatibility_date'])
        except ArtifactError:
            # A lost stage response is observed under this call's annotations;
            # an absent or ambiguous observation never retries the upload.
            staged = find_staged(adapter, plan['message'], plan['tag'])
        require(staged, 'staged_version_unverified')
        verify_staged(adapter.version_detail(staged), candidate['modules'],
                      plan['installed_bindings'], plan['compatibility_date'])
        try:
            adapter.activate_version(staged, plan['message'])
        except ArtifactError:
            # One activation attempt only: resolve the outcome by observation.
            require(adapter.observe()['active_version'] == staged, 'promotion_unresolved')
        try:
            require(accept(candidate['commit']), 'live_acceptance_failed')
        except ArtifactError:
            # Rollback restores the predecessor exactly once, then re-accepts
            # the previous release identity.
            require(adapter.observe()['active_version'] == staged, 'promotion_unresolved')
            try:
                adapter.activate_version(predecessor, plan['message'] + ' (rollback)')
            except ArtifactError:
                require(adapter.observe()['active_version'] == predecessor, 'promotion_unresolved')
            require(accept(plan['release_sha']), 'live_acceptance_failed')
            intent.finish(number, 'rolled_back')
            return {'promoted': False, 'staged_version': staged, 'deployment': number}
        intent.finish(number, 'promoted')
        return {'promoted': True, 'staged_version': staged, 'deployment': number}
    except ArtifactError:
        # Nothing further is attempted: close as rolled back when the
        # predecessor is the live version, otherwise the intent stays
        # unresolved and blocks the next promotion.
        if adapter.observe()['active_version'] == predecessor:
            intent.finish(number, 'rolled_back')
            return {'promoted': False, 'staged_version': None, 'deployment': number}
        intent.finish(number, 'unresolved')
        raise
    except Exception:
        intent.finish(number, 'unresolved')
        raise
