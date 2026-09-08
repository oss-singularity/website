"""Offline filesystem transactions, conflicts and real fresh-process crash recovery."""
from contextlib import contextmanager
import copy
import json
import multiprocessing
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import static_fixture as fs
import static_plan as plan
import static_transition as transition
from site_artifact import ArtifactError, MANIFEST, MAX_ENTRIES

ROOT = Path(__file__).resolve().parent.parent
OLD, NEW = '1' * 40, '2' * 40
PREFIX, SUFFIX = b'# unmanaged prefix\n', b'# unmanaged suffix\n'


def payload(values):
    result = dict(values)
    result[MANIFEST] = b''.join((plan.digest(data) + '  ' + name + '\n').encode()
                               for name, data in sorted(values.items()))
    return result


def descriptor(files, commit):
    return plan.canonical({'schema_version': 1, 'repository': plan.REPOSITORY,
                           'kind': 'static-site', 'commit': commit, **plan.summary(files)})


def inputs():
    old = payload({'.htaccess': b'# old block\n', 'index.html': b'old page',
                   'assets/retired.js': b'retired', 'assets/same.css': b'stable'})
    new = payload({'.htaccess': b'# new block\n', 'index.html': b'new page',
                   'assets/new.js': b'new', 'assets/same.css': b'stable',
                   'data/new/current.json': b'{}'})
    return {'candidate': new, 'candidate_descriptor': descriptor(new, NEW),
            'expected_candidate_commit': NEW, 'predecessor': old,
            'predecessor_descriptor': descriptor(old, OLD)}


def trusted_installation(original):
    """Fixed test-runner setup, separate from inputs submitted to prepare()."""
    files = {**original['predecessor'], '.htaccess': PREFIX + original['predecessor']['.htaccess'] + SUFFIX,
             '.well-known/foreign': b'non-target data'}
    meta = {'mode': 0o644, 'uid': os.getuid(), 'gid': os.getgid()}
    directory = {**meta, 'mode': 0o755}
    owned = {}
    for name in original['predecessor']:
        owned[name] = plan.file_record(files[name], meta)
        for parent in plan.parents(name):
            owned[parent] = {'kind': 'directory', **directory}
    baseline = {'schema_version': 1, 'target': plan.TARGET, 'generation': 7,
                'commit': OLD, 'descriptor_sha256': plan.digest(original['predecessor_descriptor']),
                'manifest_sha256': plan.digest(original['predecessor'][MANIFEST]),
                'owned_inventory_sha256': plan.digest(plan.canonical({'root': directory, 'entries': owned})),
                'overlay': {'prefix_bytes': len(PREFIX), 'prefix_sha256': plan.digest(PREFIX),
                            'suffix_bytes': len(SUFFIX), 'suffix_sha256': plan.digest(SUFFIX)}}
    return files, baseline


@contextmanager
def fixture(original=None):
    original = inputs() if original is None else original
    files, baseline = trusted_installation(original)
    with fs.create_fixture(files, baseline) as handle:
        yield handle, copy.deepcopy(original), files


def child_run(handle, submitted, action, stop, expected, connection):
    """Only trusted test code is spawned; candidates are passed as inert bytes."""
    def checkpoint(event):
        if event == stop:
            os._exit(73)
    try:
        with transition.session(handle, checkpoint) as session:
            if action == 'start':
                session._prepare_captured(**submitted)
                session.apply(session.ticket())
            elif action == 'rollback':
                session.rollback(expected)
            elif action == 'recover':
                phase = session.report()['phase']
                if phase not in {'verified', 'rolled_back', 'aborted'}:
                    session.reconcile(expected)
                    if session.report()['phase'] not in {'aborted', 'verified'}:
                        session.apply(expected)
            elif action == 'recover-rollback':
                if session.report()['phase'] != 'rolled_back':
                    session.reconcile(expected)
                    session.rollback(expected)
            elif action == 'lock':
                pass
            connection.send({'report': session.report()})
    except ArtifactError as error:
        connection.send({'error': error.code})
    finally:
        connection.close()


class TransitionTests(unittest.TestCase):
    def process(self, handle, submitted, action, stop=None):
        context = multiprocessing.get_context('spawn')
        receive, send = context.Pipe(duplex=False)
        expected = None
        if action not in {'lock', 'start'}:
            # The trusted harness retains the current attempt before dispatch.
            # Recovery never silently adopts a different attempt in the child.
            with transition.session(handle) as session:
                expected = session.ticket()
        process = context.Process(target=child_run, args=(handle, submitted, action, stop, expected, send))
        process.start()
        send.close()
        process.join(15)
        if process.is_alive():
            process.kill()
            process.join(5)
            self.fail('fixture process exceeded its bounded time')
        if stop is not None:
            self.assertEqual(process.exitcode, 73, stop)
            result = None
        else:
            self.assertEqual(process.exitcode, 0)
            self.assertTrue(receive.poll(1), 'missing child result')
            result = receive.recv()
        receive.close()
        return result

    def contents(self, handle):
        with fs.locked(handle) as fds:
            return fs.snapshot(fds['target'])[2]

    def ready(self, handle, submitted):
        with transition.session(handle) as session:
            session._prepare_captured(**submitted)

    def applied(self, handle, submitted):
        self.ready(handle, submitted)
        with transition.session(handle) as session:
            return session.apply(session.ticket())

    def expect_error(self, operation, code=None):
        with self.assertRaises(ArtifactError) as failure:
            operation()
        if code is not None:
            self.assertEqual(failure.exception.code, code)
        self.assertRegex(failure.exception.code, r'^[a-z_]+$')

    def test_complete_transition_preserves_overlay_retired_data_metadata_and_peer(self):
        with fixture() as (handle, submitted, original):
            before = copy.deepcopy(submitted)
            report = self.applied(handle, submitted)
            self.assertEqual(submitted, before)
            installed = self.contents(handle)
            self.assertEqual(installed, {**original, **submitted['candidate'],
                '.htaccess': PREFIX + submitted['candidate']['.htaccess'] + SUFFIX})
            self.assertEqual(report['phase'], 'verified')
            self.assertEqual(report['generation'], 8)
            self.assertFalse(report['deployment_authorized'])
            self.assertTrue(report['fixture_only'])
            with transition.session(handle) as session:
                self.assertEqual(session.reconcile(session.ticket())['last_reconciliation'], 'consistent')
                self.assertEqual(session.rollback(session.ticket())['phase'], 'rolled_back')
                self.assertEqual(session.report()['generation'], 9)
            self.assertEqual(self.contents(handle), original)
            with fs.locked(handle) as fds:
                root, entries, _files, _identities = fs.snapshot(fds['target'])
                self.assertEqual(root['mode'], 0o755)
                self.assertTrue(all(value['mode'] == (0o755 if value['kind'] == 'directory' else 0o644)
                                    for value in entries.values()))

    def test_forged_old_pair_fails_independent_baseline_before_attempt(self):
        with fixture() as (handle, submitted, original):
            submitted['predecessor'] = payload({'.htaccess': b'forged', 'index.html': b'forged'})
            submitted['predecessor_descriptor'] = descriptor(submitted['predecessor'], OLD)
            with transition.session(handle) as session:
                self.expect_error(lambda: session._prepare_captured(**submitted), 'baseline_mismatch')
                self.assertEqual(session.report()['phase'], 'empty')
            self.assertEqual(self.contents(handle), original)

    def test_target_conflict_blocks_apply_and_new_attempt_until_reconciled(self):
        with fixture() as (handle, submitted, _original):
            self.ready(handle, submitted)
            page = Path(handle.container) / 'target/index.html'
            page.write_bytes(b'newer unrelated edit')
            with transition.session(handle) as session:
                self.expect_error(lambda: session.apply(session.ticket()), 'target_conflict')
                self.assertEqual(session.report()['phase'], 'reconciliation_required')
                self.expect_error(lambda: session._prepare_captured(**submitted), 'unfinished_attempt')
                self.expect_error(lambda: session.reconcile(session.ticket()), 'target_conflict')
            self.assertEqual(page.read_bytes(), b'newer unrelated edit')

    def test_unfinished_attempt_survives_process_exit_and_blocks_another(self):
        with fixture() as (handle, submitted, _original):
            self.process(handle, submitted, 'start', 'prepared')
            with transition.session(handle) as session:
                self.expect_error(lambda: session._prepare_captured(**submitted), 'unfinished_attempt')
            result = self.process(handle, submitted, 'recover')
            self.assertEqual(result['report']['phase'], 'verified')

    def test_every_forward_boundary_recovers_in_a_fresh_process(self):
        events = []
        with fixture() as (handle, submitted, _original):
            with transition.session(handle, events.append) as session:
                session._prepare_captured(**submitted)
                session.apply(session.ticket())
        self.assertGreater(len(events), 25)
        for event in events:
            with self.subTest(event=event), fixture() as (handle, submitted, original):
                self.process(handle, submitted, 'start', event)
                result = self.process(handle, submitted, 'recover')
                self.assertNotIn('error', result)
                phase = result['report']['phase']
                if event == 'observed' or event == 'staged' or event.startswith(('stage-', 'backup-')):
                    self.assertEqual(phase, 'aborted')
                    self.assertEqual(self.contents(handle), original)
                    # An aborted preparation permits a newly validated attempt.
                    self.ready(handle, submitted)
                else:
                    self.assertEqual(phase, 'verified')
                    self.assertEqual(self.contents(handle)['index.html'], b'new page')

    def test_every_rollback_boundary_recovers_in_a_fresh_process(self):
        events = []
        with fixture() as (handle, submitted, _original):
            self.applied(handle, submitted)
            with transition.session(handle, events.append) as session:
                session.rollback(session.ticket())
        self.assertGreater(len(events), 20)
        for event in events:
            with self.subTest(event=event), fixture() as (handle, submitted, original):
                self.applied(handle, submitted)
                self.process(handle, submitted, 'rollback', event)
                result = self.process(handle, submitted, 'recover-rollback')
                self.assertNotIn('error', result)
                self.assertEqual(result['report']['phase'], 'rolled_back')
                self.assertEqual(self.contents(handle), original)

    def test_pending_intent_reports_both_before_and_after_without_target_writes(self):
        for event, expected in [('forward-intent-0', 'not_applied'), ('forward-written-0', 'applied')]:
            with self.subTest(event=event), fixture() as (handle, submitted, _original):
                self.process(handle, submitted, 'start', event)
                before = self.contents(handle)
                with transition.session(handle) as session:
                    self.assertEqual(session.reconcile(session.ticket())['last_reconciliation'], expected)
                self.assertEqual(self.contents(handle), before)

    def test_third_postimage_and_metadata_conflict_never_retry(self):
        for metadata_only in [False, True]:
            with self.subTest(metadata=metadata_only), fixture() as (handle, submitted, _original):
                self.process(handle, submitted, 'start', 'forward-written-2')
                path = Path(handle.container) / 'target/assets/new.js'
                if metadata_only:
                    path.chmod(0o600)
                else:
                    path.write_bytes(b'third value')
                before = path.read_bytes()
                with transition.session(handle) as session:
                    self.expect_error(lambda: session.reconcile(session.ticket()))
                    self.assertEqual(session.report()['phase'], 'reconciliation_required')
                self.assertEqual(path.read_bytes(), before)

    def test_cooperative_lock_rejects_a_concurrent_process(self):
        with fixture() as (handle, submitted, _original):
            with transition.session(handle):
                result = self.process(handle, submitted, 'lock')
                self.assertEqual(result, {'error': 'fixture_busy'})
            self.assertNotIn('error', self.process(handle, submitted, 'lock'))

    def test_rollback_preserves_new_non_target_file_and_its_created_directories(self):
        with fixture() as (handle, submitted, original):
            self.applied(handle, submitted)
            newer = Path(handle.container) / 'target/data/new/community.txt'
            newer.write_bytes(b'newer non-target work')
            with transition.session(handle) as session:
                report = session.rollback(session.ticket())
                self.assertEqual(report['retained_directories'], 2)
            self.assertEqual(self.contents(handle), {**original, 'data/new/community.txt': b'newer non-target work'})

    def test_changed_owned_postimage_or_backup_blocks_rollback_before_writes(self):
        for change in ['postimage', 'backup', 'missing-backup', 'overlay']:
            with self.subTest(change=change), fixture() as (handle, submitted, _original):
                self.applied(handle, submitted)
                with transition.session(handle) as session:
                    attempt = session.attempt
                    index = next(i for i, step in enumerate(attempt['steps']) if step['path'] == 'index.html')
                    backup = Path(handle.container) / 'control' / attempt['id'] / f'before-{index}'
                if change == 'backup':
                    backup.write_bytes(b'altered backup')
                elif change == 'missing-backup':
                    backup.unlink()
                else:
                    name = '.htaccess' if change == 'overlay' else 'index.html'
                    (Path(handle.container) / 'target' / name).write_bytes(b'newer work')
                before = self.contents(handle)
                with transition.session(handle) as session:
                    self.expect_error(lambda: session.rollback(session.ticket()))
                self.assertEqual(self.contents(handle), before)

    def test_partial_forward_can_be_rolled_back_after_reconciliation(self):
        with fixture() as (handle, submitted, original):
            self.process(handle, submitted, 'start', 'forward-written-3')
            with transition.session(handle) as session:
                session.reconcile(session.ticket())
                report = session.rollback(session.ticket())
                self.assertEqual(report['generation'], 8)
            self.assertEqual(self.contents(handle), original)

    def test_generation_change_blocks_both_forward_and_rollback(self):
        for completed in [False, True]:
            with self.subTest(completed=completed), fixture() as (handle, submitted, _original):
                self.applied(handle, submitted) if completed else self.ready(handle, submitted)
                with fs.locked(handle) as fds:
                    state = fs.read_json(fds['control'], 'state.json')
                    state['generation'] += 1
                    fs.save_json(fds['control'], 'state.json', state)
                before = self.contents(handle)
                with transition.session(handle) as session:
                    self.expect_error(lambda: (session.rollback if completed else session.apply)(session.ticket()), 'generation_conflict')
                self.assertEqual(self.contents(handle), before)

    def test_successful_journal_baseline_can_bind_a_second_transition(self):
        with fixture() as (handle, submitted, _original):
            self.applied(handle, submitted)
            next_files = payload({**{name: data for name, data in submitted['candidate'].items()
                                    if name != MANIFEST}, 'index.html': b'next page'})
            following = {'candidate': next_files, 'candidate_descriptor': descriptor(next_files, '3' * 40),
                         'expected_candidate_commit': '3' * 40, 'predecessor': submitted['candidate'],
                         'predecessor_descriptor': submitted['candidate_descriptor']}
            report = self.applied(handle, following)
            self.assertEqual(report['generation'], 9)
            with transition.session(handle) as session:
                session.rollback(session.ticket())
            self.assertEqual(self.contents(handle)['index.html'], b'new page')

    def test_old_attempt_ticket_cannot_act_on_newer_prepared_or_verified_attempt(self):
        with fixture() as (handle, submitted, _original):
            self.applied(handle, submitted)
            with transition.session(handle) as session:
                old_ticket = session.ticket()
            following = {**submitted, 'predecessor': submitted['candidate'],
                         'predecessor_descriptor': submitted['candidate_descriptor']}
            self.ready(handle, following)
            for committed in [False, True]:
                with transition.session(handle) as session:
                    if committed:
                        session.apply(session.ticket())
                    for operation in [session.apply, session.reconcile, session.rollback]:
                        self.expect_error(lambda: operation(old_ticket), 'stale_attempt')

    def test_successful_write_return_is_not_accepted_without_observed_postimage(self):
        with fixture() as (handle, submitted, original):
            self.ready(handle, submitted)
            original_replace = os.replace
            def ignore_publish(source, destination, **kwargs):
                if not source.startswith('publish-'):
                    original_replace(source, destination, **kwargs)
            with transition.session(handle) as session:
                expected = session.ticket()
                with patch.object(os, 'replace', side_effect=ignore_publish):
                    self.expect_error(lambda: session.apply(expected), 'target_conflict')
                self.assertEqual(session.reconcile(expected)['last_reconciliation'], 'not_applied')
            self.assertEqual(self.contents(handle), original)
            with transition.session(handle) as session:
                self.assertEqual(session.apply(expected)['phase'], 'verified')

    def test_change_at_final_verification_blocks_generation_commit(self):
        with fixture() as (handle, submitted, _original):
            self.ready(handle, submitted)
            def mutate(event):
                if event == 'verified-before-commit':
                    (Path(handle.container) / 'target/index.html').write_bytes(b'newer work')
            with transition.session(handle, mutate) as session:
                self.expect_error(lambda: session.apply(session.ticket()), 'target_conflict')
                self.assertEqual(session.report()['generation'], 7)

    def test_incomplete_private_restore_transfer_can_be_recreated_before_rollback(self):
        with fixture() as (handle, submitted, original):
            self.applied(handle, submitted)
            self.process(handle, submitted, 'rollback', 'rollback-preparing')
            with transition.session(handle) as session:
                attempt = session.attempt
                index = next(i for i, step in enumerate(attempt['steps']) if step['before'] is not None)
                path = Path(handle.container) / 'control' / attempt['id'] / f'restore-{index}'
                path.write_bytes(b'partial private transfer')
                path.chmod(0o600)
                ticket = session.ticket()
                session.reconcile(ticket)
                session.rollback(ticket)
            self.assertEqual(self.contents(handle), original)

    def test_corrupt_journal_and_replaced_lock_are_rejected(self):
        for change in ['journal', 'lock']:
            with self.subTest(change=change), fixture() as (handle, submitted, _original):
                self.ready(handle, submitted)
                path = Path(handle.container) / 'control' / ('state.json' if change == 'journal' else 'lock')
                if change == 'lock':
                    path.rename(path.with_name('old-lock'))
                    path.touch(mode=0o600)
                else:
                    path.write_bytes(b'{"value": "incomplete"}')
                self.expect_error(lambda: self.ready(handle, submitted))

    def test_links_special_files_and_unowned_collision_fail_before_apply(self):
        for kind in ['symlink', 'hardlink', 'fifo', 'collision']:
            with self.subTest(kind=kind), fixture() as (handle, submitted, _original):
                target = Path(handle.container) / 'target'
                path = target / 'assets/new.js'
                if kind == 'symlink':
                    path.symlink_to(target / 'index.html')
                elif kind == 'hardlink':
                    path.hardlink_to(target / 'index.html')
                elif kind == 'fifo':
                    os.mkfifo(path)
                else:
                    path.write_bytes(b'belongs to someone else')
                with transition.session(handle) as session:
                    self.expect_error(lambda: session._prepare_captured(**submitted))
                    self.assertEqual(session.report()['phase'], 'empty')

    def test_parent_path_replacement_and_root_metadata_changes_are_detected(self):
        for change in ['parent', 'root-mode']:
            with self.subTest(change=change), fixture() as (handle, submitted, _original):
                self.ready(handle, submitted)
                target = Path(handle.container) / 'target'
                if change == 'parent':
                    (target / 'assets').rename(target / 'saved-assets')
                    (target / 'assets').mkdir()
                    for name in ['same.css', 'retired.js']:
                        (target / 'assets' / name).write_bytes((target / 'saved-assets' / name).read_bytes())
                else:
                    target.chmod(0o700)
                with transition.session(handle) as session:
                    self.expect_error(lambda: session.apply(session.ticket()))
                self.assertFalse((target / 'data').exists())

    def test_root_alias_control_replacement_and_peer_changes_fail(self):
        for change in ['alias', 'control', 'peer']:
            with self.subTest(change=change), fixture() as (handle, _submitted, _original):
                root = Path(handle.container)
                if change == 'alias':
                    (root / 'target').rename(root / 'saved')
                    (root / 'target').symlink_to(root / 'saved', target_is_directory=True)
                elif change == 'control':
                    (root / 'control').rename(root / 'saved')
                    (root / 'control').mkdir(mode=0o700)
                else:
                    (root / 'peer/sentinel').write_bytes(b'changed peer')
                self.expect_error(lambda: self.ready(handle, inputs()))

    def test_material_substitution_between_intent_and_write_fails(self):
        with fixture() as (handle, submitted, _original):
            self.ready(handle, submitted)
            def replace(event):
                if event == 'forward-intent-0':
                    with os.scandir(Path(handle.container) / 'control') as children:
                        material = next(Path(item.path) for item in children if item.is_dir())
                    (material / 'publish-0').rmdir()
                    (material / 'publish-0').symlink_to(Path(handle.container) / 'peer', target_is_directory=True)
            with transition.session(handle, replace) as session:
                self.expect_error(lambda: session.apply(session.ticket()))
            self.assertFalse((Path(handle.container) / 'target/data').exists())

    def test_bounded_input_and_private_exclusive_sanitized_report(self):
        _files, baseline = trusted_installation(inputs())
        with self.assertRaises(ArtifactError):
            with fs.create_fixture({f'f-{number}': b'' for number in range(MAX_ENTRIES + 1)}, baseline):
                self.fail('oversized fixture accepted')
        with fixture() as (handle, submitted, _original), tempfile.TemporaryDirectory() as temporary:
            self.applied(handle, submitted)
            report = Path(temporary) / 'report.json'
            with transition.session(handle) as session:
                session.write_report(report)
                self.expect_error(lambda: session.write_report(Path(handle.container) / 'target/report.json'))
                with self.assertRaises(FileExistsError):
                    session.write_report(report)
            raw = report.read_text()
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            for excluded in [handle.container, PREFIX.decode().strip(), SUFFIX.decode().strip(), 'uid', '.htaccess']:
                self.assertNotIn(excluded, raw)
            self.assertFalse(json.loads(raw)['deployment_authorized'])

    def test_generation_exhaustion_is_rejected_before_staging(self):
        files, baseline = trusted_installation(inputs())
        baseline['generation'] = 2**63 - 2
        with fs.create_fixture(files, baseline) as handle, transition.session(handle) as session:
            self.expect_error(lambda: session._prepare_captured(**inputs()), 'generation_limit')
            self.assertEqual(session.report()['phase'], 'empty')

    def test_artifact_root_overlap_is_rejected_before_capture(self):
        with fixture() as (handle, _submitted, _original), transition.session(handle) as session:
            target = Path(handle.container) / 'target'
            for candidate, predecessor in [(target, Path(handle.container)), (ROOT, ROOT / 'site')]:
                self.expect_error(lambda: session.prepare(candidate_root=candidate,
                    candidate_descriptor=ROOT / 'nonexistent.json', expected_candidate_commit=NEW,
                    predecessor_root=predecessor, predecessor_descriptor=ROOT / 'nonexistent.json'),
                    'overlapping_roots')

    def test_substituted_directory_descriptor_cannot_write_to_peer(self):
        with fixture() as (handle, submitted, _original):
            self.ready(handle, submitted)
            peer = Path(handle.container) / 'peer'
            original_open = os.open
            def substituted(path, flags, *args, **kwargs):
                if path == 'assets' and flags == fs.DIR_FLAGS:
                    return original_open(peer, fs.DIR_FLAGS)
                return original_open(path, flags, *args, **kwargs)
            with transition.session(handle) as session, patch.object(os, 'open', side_effect=substituted):
                self.expect_error(lambda: session.apply(session.ticket()), 'tree_changed')
            self.assertEqual(sorted(path.name for path in peer.iterdir()), ['sentinel'])
            self.assertEqual((peer / 'sentinel').read_bytes(), b'unrelated fixture: preserve\n')

    def test_backend_has_no_network_or_subprocess_execution(self):
        with fixture() as (handle, submitted, _original):
            with patch.object(socket, 'socket', side_effect=AssertionError('network')), \
                 patch.object(subprocess, 'Popen', side_effect=AssertionError('execution')), \
                 patch.dict(os.environ, {}, clear=True):
                self.applied(handle, submitted)
                with transition.session(handle) as session:
                    session.rollback(session.ticket())


class ProductIntegrationTests(unittest.TestCase):
    def test_current_product_capture_and_generic_historical_roundtrip(self):
        with tempfile.TemporaryDirectory(prefix='oss-transition-product-') as temporary:
            root = Path(temporary)
            current = root / 'current'
            subprocess.run(['sh', str(ROOT / 'scripts/build-site.sh'), str(current)],
                           check=True, capture_output=True, timeout=30)
            new = transition.product_capture(current)
            # Historical integrity deliberately permits a retired file and lacks
            # a current file. Today's checker must still run for the candidate.
            old_values = {name: data for name, data in new.items() if name != MANIFEST}
            removed = next(name for name in old_values if name.startswith('assets/') and name.endswith('.js'))
            del old_values[removed]
            old_values['assets/historical-retired.js'] = b'/* historical inert bytes */'
            old_values['index.html'] = b'<html>historical page</html>'
            old = payload(old_values)
            original = {'candidate': new, 'candidate_descriptor': descriptor(new, NEW),
                        'expected_candidate_commit': NEW, 'predecessor': old,
                        'predecessor_descriptor': descriptor(old, OLD)}
            previous = root / 'previous'
            previous.mkdir()
            for name, data in old.items():
                destination = previous / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            old_description, new_description = root / 'old.json', root / 'new.json'
            old_description.write_bytes(original['predecessor_descriptor'])
            new_description.write_bytes(original['candidate_descriptor'])
            with fixture(original) as (handle, _submitted, installed):
                with transition.session(handle) as session:
                    session.prepare(candidate_root=current, candidate_descriptor=new_description,
                                    expected_candidate_commit=NEW, predecessor_root=previous,
                                    predecessor_descriptor=old_description)
                    session.apply(session.ticket())
                    self.assertEqual((Path(handle.container) / 'target' / removed).read_bytes(), new[removed])
                    self.assertTrue((Path(handle.container) / 'target/assets/historical-retired.js').exists())
                    session.rollback(session.ticket())
                with fs.locked(handle) as fds:
                    self.assertEqual(fs.snapshot(fds['target'])[2], installed)
            # A manifest-valid historical payload is NOT a valid current product.
            with fixture(original) as (handle, _submitted, _installed), transition.session(handle) as session:
                with self.assertRaises(ArtifactError):
                    session.prepare(candidate_root=previous, candidate_descriptor=old_description,
                                    expected_candidate_commit=OLD, predecessor_root=current,
                                    predecessor_descriptor=new_description)
                self.assertEqual(session.report()['phase'], 'empty')


if __name__ == '__main__':
    unittest.main()
