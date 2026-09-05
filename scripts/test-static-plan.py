"""Offline planner tests; synthetic fixture bytes are not a product build."""
import copy
import json
import unittest
from unittest.mock import patch

import static_plan as planner
from site_artifact import ArtifactError, MANIFEST, MAX_FILES, MAX_ENTRIES

META = {'mode': 0o644, 'uid': 1000, 'gid': 1000}
DIRECTORY = {**META, 'mode': 0o755}
OLD = '1' * 40
NEW = '2' * 40


def payload(values):
    values = dict(values)
    values[MANIFEST] = b''.join((planner.digest(data) + '  ' + name + '\n').encode()
                               for name, data in sorted(values.items()))
    return values


def descriptor(files, commit):
    return planner.canonical({'schema_version': 1, 'repository': planner.REPOSITORY,
                              'kind': 'static-site', 'commit': commit, **planner.summary(files)})


def owned_entries(case):
    names = set(case['predecessor']) | {parent for name in case['predecessor']
                                       for parent in planner.parents(name)}
    return {name: case['inventory']['entries'][name] for name in names}


def fixture():
    old = payload({'.htaccess': b'# old managed\n', 'index.html': b'old html',
                   'assets/retired.js': b'retired', 'assets/same.css': b'stable'})
    new = payload({'.htaccess': b'# new managed\n', 'index.html': b'new html',
                   'assets/new.js': b'new', 'assets/same.css': b'stable',
                   'data/new/current.json': b'{}'})
    prefix, suffix = b'# unmanaged prefix\n', b'# unmanaged suffix\n'
    access = prefix + old['.htaccess'] + suffix
    entries = {name: planner.file_record(access if name == '.htaccess' else data, META)
               for name, data in old.items()}
    entries.update({'assets': {'kind': 'directory', **DIRECTORY},
                    '.well-known': {'kind': 'directory', **DIRECTORY},
                    '.well-known/foreign': planner.file_record(b'unmanaged', META)})
    inventory = {'target': planner.TARGET, 'generation': 7,
                 'root': dict(DIRECTORY), 'entries': entries}
    old_descriptor = descriptor(old, OLD)
    baseline = {'schema_version': 1, 'target': planner.TARGET, 'generation': 7, 'commit': OLD,
                'descriptor_sha256': planner.digest(old_descriptor),
                'manifest_sha256': planner.digest(old[MANIFEST]),
                'owned_inventory_sha256': planner.digest(planner.canonical({
                    'root': inventory['root'], 'entries': {name: entries[name] for name in
                        set(old) | {parent for item in old for parent in planner.parents(item)}}})),
                'overlay': {'prefix_sha256': planner.digest(prefix), 'prefix_bytes': len(prefix),
                            'suffix_sha256': planner.digest(suffix), 'suffix_bytes': len(suffix)}}
    return dict(candidate=new, candidate_descriptor=descriptor(new, NEW),
                expected_candidate_commit=NEW, predecessor=old,
                predecessor_descriptor=old_descriptor, baseline=baseline,
                inventory=inventory, installed_htaccess=access,
                creation_metadata={'file': dict(META), 'directory': dict(DIRECTORY)})


def rebind_installed_for_fixture(case, data):
    """Model a separately captured installation; deliberately leave overlay binding fixed."""
    case['installed_htaccess'] = data
    case['inventory']['entries']['.htaccess'] = planner.file_record(data, META)
    case['baseline']['owned_inventory_sha256'] = planner.digest(planner.canonical({
        'root': case['inventory']['root'],
        'entries': owned_entries(case)}))


class PlannerTests(unittest.TestCase):
    def reject(self, case, code):
        with self.assertRaises(ArtifactError) as failure:
            planner.build_plan(**case)
        self.assertEqual(failure.exception.code, code)

    def test_added_retired_preserved_and_composite_identity(self):
        case = fixture()
        result = planner.build_plan(**case)
        operations = {item['path']: item for item in result['operations']}
        self.assertEqual(operations['assets/new.js']['operation'], 'create')
        self.assertEqual(operations['assets/same.css']['operation'], 'keep')
        self.assertEqual(operations['index.html']['operation'], 'replace')
        self.assertNotIn('assets/retired.js', operations)
        self.assertIn('assets/retired.js', result['preserved_entries'])
        self.assertIn('.well-known/foreign', result['preserved_entries'])
        expected = b'# unmanaged prefix\n# new managed\n# unmanaged suffix\n'
        self.assertEqual(result['composite_htaccess']['sha256'], planner.digest(expected))
        self.assertEqual(operations['.htaccess']['after']['sha256'], planner.digest(expected))
        self.assertEqual(result['composite_htaccess']['artifact_sha256'],
                         planner.digest(case['candidate']['.htaccess']))
        self.assertEqual(result['required_directories'], [
            {'path': 'assets', 'operation': 'keep', 'before': {'kind': 'directory', **DIRECTORY},
             'after': {'kind': 'directory', **DIRECTORY}},
            {'path': 'data', 'operation': 'create', 'before': None,
             'after': {'kind': 'directory', **DIRECTORY}},
            {'path': 'data/new', 'operation': 'create', 'before': None,
             'after': {'kind': 'directory', **DIRECTORY}}])
        self.assertFalse(result['deployment_authorized'])

    def test_exact_order_and_insertion_order_independence(self):
        case = fixture()
        first = planner.build_plan(**case)
        self.assertEqual([op['path'] for op in first['operations']], [
            'assets/new.js', 'assets/same.css', 'data/new/current.json',
            'index.html', '.htaccess', MANIFEST])
        case['candidate'] = dict(reversed(list(case['candidate'].items())))
        case['predecessor'] = dict(reversed(list(case['predecessor'].items())))
        case['inventory']['entries'] = dict(reversed(list(case['inventory']['entries'].items())))
        self.assertEqual(first, planner.build_plan(**case))
        without_digest = {key: value for key, value in first.items() if key != 'plan_sha256'}
        self.assertEqual(first['plan_sha256'], planner.digest(planner.canonical(without_digest)))

    def test_no_mutation_io_or_execution(self):
        case = fixture()
        saved = copy.deepcopy(case)
        def forbidden(*_args, **_kwargs):
            raise AssertionError('planner attempted I/O or execution')
        with patch('builtins.open', forbidden), patch('os.open', forbidden), \
             patch('os.stat', forbidden), patch('os.mkdir', forbidden), \
             patch('os.remove', forbidden), patch('os.replace', forbidden), \
             patch('os.system', forbidden), patch('subprocess.Popen', forbidden), \
             patch('socket.socket', forbidden):
            result = planner.build_plan(**case)
        self.assertEqual(case, saved)
        result['operations'][0]['after']['uid'] = 9
        result['operations'][-1]['before']['mode'] = 0
        result['root_metadata']['gid'] = 9
        result['required_directories'][0]['after']['mode'] = 0
        result['required_directories'][0]['before']['uid'] = 9
        self.assertEqual(case, saved)

    def test_forged_predecessor_pair_rejected_by_independent_baseline(self):
        case = fixture()
        values = dict(case['predecessor'])
        del values[MANIFEST]
        values['index.html'] = b'forged'
        case['predecessor'] = payload(values)
        case['predecessor_descriptor'] = descriptor(case['predecessor'], OLD)
        self.reject(case, 'baseline_mismatch')

    def test_candidate_expected_identity_is_independent(self):
        case = fixture()
        case['expected_candidate_commit'] = OLD
        self.reject(case, 'candidate_identity_mismatch')

    def test_baseline_generation_and_owned_metadata(self):
        for mutate in (lambda c: c['inventory'].update(generation=8),
                       lambda c: c['inventory']['root'].update(gid=4),
                       lambda c: c['inventory']['entries']['index.html'].update(mode=0o600)):
            case = fixture()
            mutate(case)
            self.reject(case, 'baseline_mismatch')

    def test_owned_bytes_mismatch_even_when_inventory_binding_matches(self):
        case = fixture()
        case['inventory']['entries']['index.html']['sha256'] = '0' * 64
        case['baseline']['owned_inventory_sha256'] = planner.digest(planner.canonical({
            'root': case['inventory']['root'], 'entries': owned_entries(case)}))
        self.reject(case, 'predecessor_mismatch')

    def test_unowned_file_and_directory_collision(self):
        for value in (planner.file_record(b'new', META), {'kind': 'directory', **DIRECTORY}):
            case = fixture()
            case['inventory']['entries']['assets/new.js'] = value
            self.reject(case, 'unowned_collision')

    def test_unowned_parent_file_collision(self):
        case = fixture()
        case['inventory']['entries']['data'] = planner.file_record(b'foreign', META)
        self.reject(case, 'path_collision')

    def test_missing_repeated_and_overlapping_old_overlay_blocks(self):
        for data in (b'no managed block', b'# old managed\n# old managed\n'):
            case = fixture()
            rebind_installed_for_fixture(case, data)
            self.reject(case, 'ambiguous_overlay')
        case = fixture()
        values = dict(case['predecessor'])
        del values[MANIFEST]
        values['.htaccess'] = b'aba'
        case['predecessor'] = payload(values)
        case['predecessor_descriptor'] = descriptor(case['predecessor'], OLD)
        case['baseline']['descriptor_sha256'] = planner.digest(case['predecessor_descriptor'])
        case['baseline']['manifest_sha256'] = planner.digest(case['predecessor'][MANIFEST])
        case['inventory']['entries'][MANIFEST] = planner.file_record(case['predecessor'][MANIFEST], META)
        rebind_installed_for_fixture(case, b'ababa')
        self.reject(case, 'ambiguous_overlay')

    def test_changed_prefix_or_suffix_conflicts(self):
        for old, new in ((b'prefix', b'edited'), (b'suffix', b'edited')):
            case = fixture()
            rebind_installed_for_fixture(case, case['installed_htaccess'].replace(old, new))
            self.reject(case, 'overlay_mismatch')

    def test_new_block_cannot_make_next_overlay_ambiguous(self):
        case = fixture()
        values = dict(case['candidate'])
        del values[MANIFEST]
        values['.htaccess'] = b'# unmanaged prefix\n'
        case['candidate'] = payload(values)
        case['candidate_descriptor'] = descriptor(case['candidate'], NEW)
        self.reject(case, 'ambiguous_overlay')

    def test_installed_overlay_bytes_are_bound_to_inventory(self):
        case = fixture()
        case['installed_htaccess'] += b'changed'
        self.reject(case, 'overlay_mismatch')

    def test_reject_paths_and_payload_shapes(self):
        for path in ('../escape', '/absolute', 'a//b', 'a/./b', 'bad name',
                     'x' * 256, '/'.join(['a'] * 10)):
            case = fixture()
            case['candidate'][path] = b'x'
            self.reject(case, 'invalid_path')
        for data in (bytearray(b'x'), 'text', None):
            case = fixture()
            case['candidate']['index.html'] = data
            self.reject(case, 'invalid_payload')
        case = fixture()
        case['candidate']['assets'] = b'file'
        self.reject(case, 'path_collision')
        case = fixture()
        del case['candidate'][MANIFEST]
        self.reject(case, 'invalid_payload')

    def test_counts_sizes_and_manifest_integrity(self):
        case = fixture()
        case['candidate'] = {f'x{i}': b'x' for i in range(MAX_FILES + 1)}
        self.reject(case, 'invalid_payload')
        case = fixture()
        case['inventory']['entries'] = {f'x{i}': planner.file_record(b'', META)
                                        for i in range(MAX_ENTRIES + 1)}
        self.reject(case, 'invalid_inventory')
        case = fixture()
        case['candidate']['index.html'] = b'tampered'
        self.reject(case, 'manifest_mismatch')
        case = fixture()
        with patch.object(planner, 'MAX_FILE_BYTES', 10):
            self.reject(case, 'size_limit')

    def test_descriptor_types_counts_unknown_and_duplicate_fields(self):
        for key, value in (('schema_version', True), ('file_count', True),
                           ('total_bytes', -1), ('commit', 'G' * 40),
                           ('repository', 'other/repository'), ('kind', []), ('extra', 1)):
            case = fixture()
            decoded = json.loads(case['candidate_descriptor'])
            decoded[key] = value
            case['candidate_descriptor'] = planner.canonical(decoded)
            self.reject(case, 'invalid_descriptor')
        case = fixture()
        decoded = json.loads(case['candidate_descriptor'])
        decoded['file_count'] -= 1
        case['candidate_descriptor'] = planner.canonical(decoded)
        self.reject(case, 'descriptor_mismatch')
        case = fixture()
        case['candidate_descriptor'] = case['candidate_descriptor'][:-1] + b',"file_count":1}'
        self.reject(case, 'invalid_descriptor')

    def test_inventory_types_links_missing_parents_and_metadata(self):
        cases = [({'kind': 'symlink'}, 'invalid_inventory'),
                 ({**planner.file_record(b'x', META), 'nlink': 2}, 'invalid_inventory'),
                 ({**planner.file_record(b'x', META), 'mode': True}, 'invalid_metadata'),
                 ({**planner.file_record(b'x', META), 'uid': -1}, 'invalid_metadata')]
        for entry, code in cases:
            case = fixture()
            case['inventory']['entries']['foreign'] = entry
            self.reject(case, code)
        case = fixture()
        del case['inventory']['entries']['assets']
        self.reject(case, 'invalid_inventory')

    def test_owned_parent_directory_metadata_is_bound(self):
        for key, value in (('mode', 0o700), ('uid', 5), ('gid', 6)):
            case = fixture()
            case['inventory']['entries']['assets'][key] = value
            self.reject(case, 'baseline_mismatch')

    def test_new_parent_directory_preconditions_are_inspectable(self):
        case = fixture()
        case['inventory']['entries']['data'] = {'kind': 'directory', **DIRECTORY}
        first = planner.build_plan(**case)
        case['inventory']['entries']['data']['gid'] = 4
        second = planner.build_plan(**case)
        self.assertNotEqual(first['plan_sha256'], second['plan_sha256'])
        observed = next(item for item in second['required_directories'] if item['path'] == 'data')
        self.assertEqual(observed['before']['gid'], 4)
        self.assertEqual(observed['after']['gid'], 4)
        self.assertEqual(observed['operation'], 'keep')

    def test_implied_and_projected_inventory_limits(self):
        case = fixture()
        values = {f'd{i}/a/b/file': b'x' for i in range(130)}
        values['.htaccess'] = b'managed'
        case['candidate'] = payload(values)
        self.reject(case, 'tree_limit')
        case = fixture()
        entries = case['inventory']['entries']
        for i in range(MAX_ENTRIES - len(entries)):
            entries[f'existing-{i}'] = planner.file_record(b'', META)
        self.reject(case, 'invalid_inventory')

    def test_newer_unmanaged_data_is_preserved_without_rebinding_baseline(self):
        case = fixture()
        case['inventory']['entries']['new-unmanaged'] = planner.file_record(b'new data', META)
        plan = planner.build_plan(**case)
        self.assertIn('new-unmanaged', plan['preserved_entries'])
        self.assertNotIn('new-unmanaged', {op['path'] for op in plan['operations']})

    def test_changed_creation_policy_changes_plan_without_changing_existing_metadata(self):
        case = fixture()
        first = planner.build_plan(**case)
        case['creation_metadata']['file']['mode'] = 0o640
        second = planner.build_plan(**case)
        self.assertNotEqual(first['plan_sha256'], second['plan_sha256'])
        by_name = {op['path']: op for op in second['operations']}
        self.assertEqual(by_name['assets/new.js']['after']['mode'], 0o640)
        self.assertEqual(by_name['index.html']['after']['mode'], 0o644)


if __name__ == '__main__':
    unittest.main()
