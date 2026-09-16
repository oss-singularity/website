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
import re
import time

import commons_artifact as artifact
import commons_plan as planner
import commons_rehearsal as rehearsal
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

# The worker's export surface as the provider reports it for the main module.
# Pinned like the module and migration contracts: a candidate that changes it
# refuses here, and widening it is a reviewed contract change, not a silent one.
WORKER_HANDLERS = {'handlers': ['fetch', 'scheduled'],
                   'named_handlers': ['cleanup', 'safeUrl']}


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
    if not statuses:
        return number
    latest = (statuses[0].get('state'), statuses[0].get('description'))
    require(latest in {('success', PROMOTED), ('failure', ROLLED_BACK),
                       ('in_progress', IN_PROGRESS), ('error', UNRESOLVED)},
            'promotion_record_closed')
    if latest in {('success', PROMOTED), ('failure', ROLLED_BACK)}:
        # A closed record keeps its evidence and never blocks the next
        # promotion; only an open or unresolved one does.
        return None
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


def verify_staged(detail, installed_bindings, compatibility_date, annotations):
    """Verify the staged version server-side before any activation.

    The provider's version detail carries no module list, so the staged state
    is verified against the fields it does report: the upload identity
    (annotations matching this call's message and tag), the desired bindings
    (same names, types, D1 ids and secret presence), the installed
    compatibility date, and the worker's pinned export surface.
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
    require(type(script) is dict
            and script.get('handlers') == WORKER_HANDLERS['handlers'], 'staged_modules_changed')
    require(sorted(item.get('name') for item in script.get('named_handlers', []) if type(item) is dict)
            == WORKER_HANDLERS['named_handlers'], 'staged_modules_changed')
    runtime = resources.get('script_runtime')
    require(type(runtime) is dict and runtime.get('compatibility_date') == compatibility_date,
            'staged_settings_changed')
    detail_annotations = detail.get('annotations')
    require(type(detail_annotations) is dict and type(annotations) is dict
            and detail_annotations.get('workers/message') == annotations.get('workers/message')
            and detail_annotations.get('workers/tag') == annotations.get('workers/tag'),
            'staged_version_unverified')
    return True


def rehearsal_artifacts(github, sha, run_id, run_attempt):
    """Locate the canonical rehearsal's packet and receipt by their exact names.

    The rehearsal publishes `commons-candidate-{sha}-{run_id}-{run_attempt}`
    and `commons-rehearsal-receipt-{sha}-{run_id}-{run_attempt}`; anything
    else on the run is ignored, and anything missing or duplicated refuses.
    """
    checks.positive(run_id)
    checks.positive(run_attempt)
    route = BASE + '/actions/runs/' + str(run_id) + '/artifacts?per_page=100&page=1'
    uploaded = github.get(route)
    require(type(uploaded) is dict and type(uploaded.get('artifacts')) is list,
            'invalid_rehearsal_artifacts')
    wanted = {'candidate': f'commons-candidate-{sha}-{run_id}-{run_attempt}',
              'receipt': f'commons-rehearsal-receipt-{sha}-{run_id}-{run_attempt}'}
    found = {}
    for role, name in wanted.items():
        matches = [item for item in uploaded['artifacts']
                   if type(item) is dict and item.get('name') == name]
        require(len(matches) == 1, 'invalid_rehearsal_artifacts')
        found[role] = checks.positive(matches[0].get('id'))
    return found


def provider_generation(observation):
    """Map the provider's own monotonic version number to the plan generation.

    The live target has no journal; the version `number` the API assigns to
    every upload is the equivalent counter. Reading it from the same snapshot
    that produced the observation digest binds the generation into the
    baseline, so a concurrent upload changes the next observation and is
    refused like a changed predecessor.
    """
    require(type(observation) is dict, 'provider_state_unverified')
    active = observation.get('active_version')
    versions = observation.get('versions')
    require(type(active) is str and type(versions) is dict and active in versions,
            'provider_state_unverified')
    number = versions[active].get('number')
    require(type(number) is int and 1 <= number <= 2**63 - 1, 'provider_state_unverified')
    return number


def reconstruct_predecessor(content, commit):
    """Rebuild the predecessor packet from the live predecessor version's content.

    The provider's multipart form is parsed into its modules, the packet is
    rebuilt against the recorded predecessor commit, and unpack binds the
    result: live bytes that do not belong to that commit refuse here. The
    packet is never taken from the candidate.
    """
    require(type(content) is bytes and content.startswith(b'--') and b'\r\n' in content,
            'invalid_candidate')
    boundary = content.split(b'\r\n', 1)[0][2:]
    require(2 <= len(boundary) <= 128, 'invalid_candidate')
    files = {}
    for part in content.split(b'--' + boundary)[1:-1]:
        head, _, body = part.partition(b'\r\n\r\n')
        head_text = head.decode('utf-8', 'replace')
        if 'name="metadata"' in head_text:
            continue
        name = head_text.split('name="', 1)[1].split('"', 1)[0] if 'name="' in head_text else None
        require(name is not None and body.endswith(b'\r\n'), 'invalid_candidate')
        files[name] = body[:-2]
    require(0 < len(files) <= 32 and len(set(files)) == len(files), 'invalid_candidate')
    # A well-formed provider form terminates with its closing boundary; a
    # truncated upload or trailing junk after it refuses here.
    require(content.rstrip(b'\r\n').endswith(b'--' + boundary + b'--'), 'invalid_candidate')
    # The live predecessor carries the previously installed module profile: a
    # subset of the current contract that every module digest binds to this
    # commit. The candidate side keeps the strict full-module allowlist.
    profile = frozenset(files)
    require(profile <= artifact.MODULES, 'module_allowlist_mismatch')
    packet = artifact.packet(files, commit, rehearsal.SCHEMA_SHA256, modules=profile)
    restored, _descriptor = artifact.unpack(packet, commit, rehearsal.SCHEMA_SHA256, modules=profile)
    require(restored == files, 'invalid_candidate')
    return packet


BINDING_FIELDS = {'d1': frozenset({'name', 'type', 'id', 'database_id'}),
                  'plain_text': frozenset({'name', 'type', 'text'}),
                  'secret_text': frozenset({'name', 'type'})}
SETTINGS_READ_FIELDS = frozenset({'annotations', 'bindings', 'compatibility_date',
                                  'compatibility_flags', 'logpush', 'placement', 'tags',
                                  'tail_consumers', 'usage_model'})


def normalized_bindings(raw):
    """Key the provider's binding list by name with profile-exact fields.

    The d1 entry's redundant database_id mirror is dropped after checking it
    matches the id, so the result compares equal to the planner's binding
    model. Unknown binding types or unexpected extra fields refuse.
    """
    require(type(raw) is list and 0 < len(raw) <= 32, 'provider_state_unverified')
    result = {}
    for item in raw:
        require(type(item) is dict, 'provider_state_unverified')
        name, kind = item.get('name'), item.get('type')
        require(type(name) is str and 0 < len(name) <= 64 and name not in result
                and kind in BINDING_FIELDS and set(item) == BINDING_FIELDS[kind],
                'provider_state_unverified')
        if kind == 'd1':
            require(item['database_id'] == item['id'], 'provider_state_unverified')
        result[name] = {key: item[key] for key in sorted(set(item) - {'name', 'database_id'})}
    return result


def capture_observation(adapter, predecessor_descriptor, schema_query):
    """Compose the planner's normalized observation from one live read set.

    Bounded normalization rules (verified against the live API on 16 September
    2026): the settings read's annotations part and the d1 bindings' redundant
    database_id mirror are provider noise and dropped, every other settings
    field must be one of the profile's, an absent observability key means the
    disabled default, the version etag comes from the staged resources' script
    block, and workers.dev exposure is read from the script-scoped subdomain
    endpoint. The installed modules come from the reconstructed predecessor
    descriptor, which `reconstruct_predecessor` already bound to the live
    script bytes and commit.
    """
    require(type(predecessor_descriptor) is dict
            and type(predecessor_descriptor.get('modules')) is dict, 'invalid_candidate')
    live = adapter.observe()
    require(type(live) is dict and type(live.get('active_version')) is str, 'provider_state_unverified')
    active = live['active_version']
    deployments = live.get('deployments')
    require(type(deployments) is list and 0 < len(deployments) <= 8, 'provider_state_unverified')
    deployment = deployments[0]
    require(type(deployment) is dict and type(deployment.get('id')) is str
            and deployment.get('strategy') == 'percentage', 'provider_state_unverified')
    detail = adapter.version_detail(active)
    resources = detail.get('resources') if type(detail) is dict else None
    require(type(resources) is dict, 'provider_state_unverified')
    script = resources.get('script')
    require(type(script) is dict and type(script.get('etag')) is str and len(script['etag']) == 64,
            'provider_state_unverified')
    runtime = resources.get('script_runtime')
    require(type(runtime) is dict and type(runtime.get('compatibility_date')) is str
            and type(runtime.get('usage_model')) is str, 'provider_state_unverified')
    raw_settings = adapter.script_settings()
    needed_settings = SETTINGS_READ_FIELDS - {'annotations'}
    require(type(raw_settings) is dict and 'observability' not in raw_settings
            and set(raw_settings) <= SETTINGS_READ_FIELDS and needed_settings <= set(raw_settings),
            'provider_state_unverified')
    subdomain = adapter.script_subdomain()
    require(type(subdomain) is dict and set(subdomain) == {'enabled', 'previews_enabled'}
            and all(type(subdomain[key]) is bool for key in subdomain), 'provider_state_unverified')
    result_sets = adapter.schema_rows(schema_query)
    require(type(result_sets) is list and len(result_sets) == 1 and type(result_sets[0]) is dict
            and result_sets[0].get('success') is True, 'provider_state_unverified')
    rows = result_sets[0].get('results')
    require(type(rows) is list and 0 < len(rows) <= 256, 'provider_state_unverified')
    routes = live.get('routes')
    require(type(routes) is list and len(routes) <= 32, 'provider_state_unverified')
    normalized_routes = []
    for route in routes:
        require(type(route) is dict and set(route) == {'id', 'pattern', 'script',
                                                       'request_limit_fail_open'},
                'provider_state_unverified')
        normalized_routes.append(dict(route))
    schedules = live.get('schedules')
    require(type(schedules) is list and len(schedules) <= 32, 'provider_state_unverified')
    normalized_schedules = []
    for schedule in schedules:
        require(type(schedule) is dict and type(schedule.get('cron')) is str, 'provider_state_unverified')
        normalized_schedules.append(schedule['cron'])
    flags = runtime.get('compatibility_flags', raw_settings['compatibility_flags'])
    observation = {
        'schema_version': 1, 'target': planner.TARGET,
        'account_id': live.get('account_id'), 'zone_id': live.get('zone_id'),
        'script_name': live.get('script_name'),
        'deployment': {'id': deployment['id'], 'strategy': deployment['strategy'],
                       'versions': deployment.get('versions')},
        'version': {'id': active, 'etag': script['etag'],
                    'bindings': normalized_bindings(resources.get('bindings')),
                    'runtime': {'compatibility_date': runtime['compatibility_date'],
                                'compatibility_flags': flags, 'usage_model': runtime['usage_model']}},
        'latest_version_id': live.get('latest_version_id'),
        'settings': {
            'bindings': normalized_bindings(raw_settings['bindings']),
            'compatibility_date': raw_settings['compatibility_date'],
            'compatibility_flags': raw_settings['compatibility_flags'],
            'usage_model': raw_settings['usage_model'], 'logpush': raw_settings['logpush'],
            'observability': {'enabled': False}, 'placement': raw_settings['placement'],
            'tags': raw_settings['tags'], 'tail_consumers': raw_settings['tail_consumers'],
        },
        'routes': normalized_routes, 'schedules': normalized_schedules,
        'subdomain': {'enabled': subdomain['enabled'], 'previews_enabled': subdomain['previews_enabled']},
        'modules': predecessor_descriptor['modules'], 'schema': rows,
    }
    return {'observation': observation, 'generation': provider_generation(live)}


def plan_baseline(generation, predecessor_commit, predecessor_packet, predecessor_descriptor,
                  policy, observation):
    """Build the planner's baseline from one verified live capture.

    The observation digest covers the normalized state (the exact shape the
    planner re-derives from the same observation), so any drift between
    capture and planning is refused by build_plan exactly like a changed
    predecessor. A capture against a provider with unowned pending versions
    fails here, before any intent exists.
    """
    state = planner.observed_state(observation, policy, predecessor_descriptor)
    baseline = {'schema_version': 1, 'target': planner.TARGET, 'generation': generation,
                'commit': artifact.commit(predecessor_commit),
                'packet_sha256': artifact.digest(predecessor_packet),
                'policy_sha256': artifact.digest(artifact.encode(policy)),
                'observation_sha256': artifact.digest(artifact.encode(state)),
                'version_id': state['version']['id'], 'deployment_id': state['deployment']['id']}
    planner.validate_baseline(baseline)
    return baseline


def engine_plan(candidate_commit, planned, message, tag):
    """Map the planner's authoritative plan onto the engine plan contract.

    The staged upload inherits every desired binding except RELEASE_SHA, which
    is re-entered as plain text with the candidate commit: the promoted
    version must serve its own release identity for live acceptance, and the
    planner's desired bindings are exactly the expected staged state. Nothing
    else in the mapping is decided here.
    """
    require(type(candidate_commit) is str and len(candidate_commit) == 40, 'invalid_plan')
    desired = planned.get('desired_version') if type(planned) is dict else None
    desired = desired.get('bindings') if type(desired) is dict else None
    require(type(desired) is dict and 0 < len(desired) <= 32, 'invalid_plan')
    bindings = []
    release = None
    for name, value in sorted(desired.items()):
        require(type(name) is str and type(value) is dict and type(value.get('type')) is str,
                'invalid_plan')
        if name == 'RELEASE_SHA':
            require(value.get('text') == candidate_commit, 'invalid_plan')
            release = {'name': name, 'type': value['type'], 'text': value['text']}
            bindings.append(release)
        else:
            bindings.append({'name': name, 'type': 'inherit'})
    require(release is not None, 'invalid_plan')
    runtime = planned['desired_version'].get('runtime')
    require(type(runtime) is dict and type(runtime.get('compatibility_date')) is str, 'invalid_plan')
    predecessor = planned.get('predecessor')
    require(type(predecessor) is dict and type(predecessor.get('version_id')) is str
            and predecessor['version_id'], 'invalid_plan')
    change = planned.get('release_sha_change')
    require(type(change) is dict and change.get('before') != candidate_commit, 'invalid_plan')
    require(type(planned.get('plan_sha256')) is str and len(planned['plan_sha256']) == 64,
            'invalid_plan')
    return {'predecessor_version': predecessor['version_id'],
            'release_sha': change['before'], 'message': message, 'tag': tag,
            'bindings': bindings,
            'installed_bindings': [{'name': name, **value} for name, value in sorted(desired.items())],
            'main_module': artifact.RUNTIME['entrypoint'],
            'compatibility_date': runtime['compatibility_date'],
            'plan_sha256': planned['plan_sha256']}


def find_staged(adapter, message, tag):
    """Observe the provider's version listing for this call's annotated upload."""
    versions = adapter.observe().get('versions', {})
    matches = [version for version, detail in versions.items()
               if detail.get('annotations', {}).get('workers/message') == message
               and detail.get('annotations', {}).get('workers/tag') == tag]
    require(len(matches) <= 1, 'ambiguous_staged_version')
    return matches[0] if matches else None


def accept_bounded(expected, accept, attempts=3, pause=2.0):
    """Retry one bounded live-acceptance identity check.

    The edge needs a short propagation window after an activation; a mismatch
    inside that window is retried, and a persistently wrong identity still
    fails after the attempts.
    """
    last = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(pause)
        try:
            if accept(expected):
                return True
            last = ArtifactError('live_acceptance_failed')
        except ArtifactError as error:
            last = error
    raise last


def promote(adapter, intent, plan, candidate, accept, pause=2.0):
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
        verify_staged(adapter.version_detail(staged), plan['installed_bindings'],
                      plan['compatibility_date'],
                      {'workers/message': plan['message'], 'workers/tag': plan['tag']})
        try:
            adapter.activate_version(staged, plan['message'])
        except ArtifactError:
            # One activation attempt only: resolve the outcome by observation.
            require(adapter.observe()['active_version'] == staged, 'promotion_unresolved')
        try:
            accept_bounded(candidate['commit'], accept, pause=pause)
        except ArtifactError:
            # Rollback restores the predecessor exactly once, then re-accepts
            # the previous release identity.
            require(adapter.observe()['active_version'] == staged, 'promotion_unresolved')
            try:
                adapter.activate_version(predecessor, plan['message'] + ' (rollback)')
            except ArtifactError:
                require(adapter.observe()['active_version'] == predecessor, 'promotion_unresolved')
            accept_bounded(plan['release_sha'], accept, pause=pause)
            intent.finish(number, 'rolled_back')
            return {'promoted': False, 'staged_version': staged, 'deployment': number}
        intent.finish(number, 'promoted')
        return {'promoted': True, 'staged_version': staged, 'deployment': number}
    except ArtifactError as error:
        # Nothing further is attempted: close as rolled back when the
        # predecessor is the live version, otherwise the intent stays
        # unresolved and blocks the next promotion. The refusal code
        # accompanies the sanitized outcome so an operator can tell a
        # pre-staging refusal apart from a live-acceptance rollback.
        if adapter.observe()['active_version'] == predecessor:
            intent.finish(number, 'rolled_back')
            return {'promoted': False, 'staged_version': None, 'deployment': number,
                    'error': error.code}
        intent.finish(number, 'unresolved')
        raise
    except Exception:
        intent.finish(number, 'unresolved')
        raise
