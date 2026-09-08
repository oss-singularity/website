"""Durable, sanitized intent and outcome records in the repository deployment API."""
import re
import urllib.request

from release_source import API, BASE, checks, encode, token
from site_artifact import ArtifactError, require

ENVIRONMENT = 'production-static'
TASK = 'deploy:oss-static'
LIST = BASE + '/deployments?environment=' + ENVIRONMENT + '&task=deploy%3Aoss-static&per_page=1&page=1'
SUCCESS = 'Static bytes, origin, edge, API and rollback material verified.'
ROLLED_BACK = 'Publication failed; original static bytes and HTTP rollback verified.'
UNRESOLVED = 'Outcome requires reconciliation before another publication.'


class Deployments:
    def __init__(self, environ, opener=None):
        self.environ = environ
        self.opener = opener or urllib.request.build_opener(checks.rehearsal.NoRedirect())

    def request(self, method, route, body=None):
        number = r'[1-9][0-9]{0,18}'
        permitted = ((method == 'GET' and (route == LIST or re.fullmatch(re.escape(BASE) +
                      r'/deployments/' + number + r'(?:/statuses\?per_page=1&page=1)?', route)))
                     or (method == 'POST' and (route == BASE + '/deployments' or re.fullmatch(re.escape(BASE) +
                          r'/deployments/' + number + '/statuses', route))))
        require(permitted, 'invalid_deployment_route')
        request = urllib.request.Request(API + route, method=method, data=None if body is None else encode(body), headers={
            'Authorization': 'Bearer ' + token(self.environ, 'GH_TOKEN'),
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10',
            'Content-Type': 'application/json', 'Cache-Control': 'no-cache'})
        try:
            with self.opener.open(request, timeout=15) as response:
                require(response.status == (201 if method == 'POST' else 200)
                        and response.geturl() == API + route, 'deployment_record_unconfirmed')
                return checks.decode(response.read(checks.MAX_JSON + 1))
        except ArtifactError:
            raise
        except Exception:
            # Never repeat a mutation after an unknown network outcome.
            raise ArtifactError('deployment_record_unconfirmed') from None

    def previous(self):
        items = self.request('GET', LIST)
        require(type(items) is list and len(items) <= 1, 'deployment_history_unverified')
        if not items:
            return None
        item = items[0]
        number = checks.positive(item.get('id'))
        require(item.get('environment') == ENVIRONMENT and item.get('task') == TASK
                and item.get('production_environment') is True and type(item.get('payload')) is dict
                and item['payload'].get('kind') == 'static-publication-intent', 'deployment_history_unverified')
        statuses = self.request('GET', BASE + '/deployments/' + str(number) + '/statuses?per_page=1&page=1')
        require(type(statuses) is list and len(statuses) == 1, 'unfinished_publication')
        status = statuses[0]
        require((status.get('state'), status.get('description')) in
                {('success', SUCCESS), ('failure', ROLLED_BACK)}, 'unfinished_publication')
        return number

    def start(self, sha, payload, previous):
        # Re-read immediately before recording intent. The workflow concurrency
        # group serializes cooperating jobs; the server also has its own lock.
        require(self.previous() == previous, 'deployment_history_changed')
        result = self.request('POST', BASE + '/deployments', {
            'ref': sha, 'task': TASK, 'auto_merge': False, 'required_contexts': [], 'payload': payload,
            'environment': ENVIRONMENT, 'description': 'Publish the verified canonical static candidate',
            'transient_environment': False, 'production_environment': True})
        number = checks.positive(result.get('id'))
        observed = self.request('GET', BASE + '/deployments/' + str(number))
        require(observed.get('sha') == sha and observed.get('ref') == sha
                and observed.get('environment') == ENVIRONMENT and observed.get('task') == TASK
                and observed.get('payload') == payload, 'deployment_record_unconfirmed')
        self.finish(number, 'in_progress')
        return number

    def finish(self, number, outcome):
        checks.positive(number)
        states = {'in_progress': ('in_progress', 'Verified candidate; publication is in progress.'),
                  'success': ('success', SUCCESS), 'rolled_back': ('failure', ROLLED_BACK),
                  'unresolved': ('error', UNRESOLVED)}
        require(outcome in states, 'invalid_deployment_outcome')
        state, description = states[outcome]
        route = BASE + '/deployments/' + str(number) + '/statuses'
        self.request('POST', route, {'state': state, 'description': description,
            'environment': ENVIRONMENT, 'environment_url': 'https://oss-singularity.io/', 'auto_inactive': False})
        latest = self.request('GET', route + '?per_page=1&page=1')
        require(type(latest) is list and len(latest) == 1 and latest[0].get('state') == state
                and latest[0].get('description') == description, 'deployment_record_unconfirmed')
