"""Pure, bounded static file operation planning for a future offline backend.

TRUST PREREQUISITES: trusted code has captured both payloads without races,
validated the candidate against CURRENT product checks, checked historical
integrity without executing historical code, independently obtained the baseline
and a fresh complete installation inventory, and selected expected candidate
identity and creation metadata. This module establishes none of that authority.

No filesystem, network, execution, locking or application of operations occurs.
Outputs contain installation paths/ownership and are not public release reports.
"""
import hashlib
import json
import re

from site_artifact import (MANIFEST, MAX_DEPTH, MAX_ENTRIES, MAX_FILE_BYTES,
                           MAX_FILES, MAX_MANIFEST_BYTES, MAX_TOTAL_BYTES,
                           normalized_path, require, summary, verify_manifest)

TARGET = 'oss-static'
REPOSITORY = 'oss-singularity/website'
DESCRIPTOR_FIELDS = {'schema_version', 'repository', 'kind', 'commit',
                     'manifest_sha256', 'file_count', 'total_bytes'}
META_FIELDS = {'mode', 'uid', 'gid'}
FILE_FIELDS = META_FIELDS | {'kind', 'sha256', 'size', 'nlink'}
BASELINE_FIELDS = {'schema_version', 'target', 'generation', 'commit',
                   'descriptor_sha256', 'manifest_sha256',
                   'owned_inventory_sha256', 'overlay'}
OVERLAY_FIELDS = {'prefix_sha256', 'prefix_bytes', 'suffix_sha256', 'suffix_bytes'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True).encode('ascii')


def object_fields(value, fields, code):
    require(type(value) is dict and set(value) == fields, code)


def integer(value, minimum, maximum, code):
    require(type(value) is int and minimum <= value <= maximum, code)


def hex_value(value, length, code):
    require(type(value) is str and re.fullmatch('[a-f0-9]{' + str(length) + '}', value) is not None, code)


def path(value):
    require(type(value) is str and len(value) <= 1024, 'invalid_path')
    normalized_path(value)
    require(len(value.split('/')) <= MAX_DEPTH + 1
            and all(len(part) <= 255 for part in value.split('/')), 'invalid_path')


def parents(name):
    parts = name.split('/')
    return ['/'.join(parts[:index]) for index in range(1, len(parts))]


def payload(files):
    require(type(files) is dict and 0 < len(files) <= MAX_FILES, 'invalid_payload')
    total = 0
    directories = set()
    for name, data in files.items():
        path(name)
        require(type(data) is bytes, 'invalid_payload')
        limit = MAX_MANIFEST_BYTES if name == MANIFEST else MAX_FILE_BYTES
        require(len(data) <= limit, 'size_limit')
        total += len(data)
        directories.update(parents(name))
        require(not any(parent in files for parent in parents(name)), 'path_collision')
    require(len(files) + len(directories) <= MAX_ENTRIES, 'tree_limit')
    require(total <= MAX_TOTAL_BYTES and {MANIFEST, '.htaccess'} <= set(files), 'invalid_payload')
    require(bool(files['.htaccess']), 'invalid_overlay')
    verify_manifest(files)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'invalid_descriptor')
        value[key] = item
    return value


def descriptor(data, files):
    require(type(data) is bytes and 0 < len(data) <= 4096, 'invalid_descriptor')
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        require(False, 'invalid_descriptor')
    object_fields(value, DESCRIPTOR_FIELDS, 'invalid_descriptor')
    integer(value['schema_version'], 1, 1, 'invalid_descriptor')
    require(type(value['repository']) is str and value['repository'] == REPOSITORY
            and type(value['kind']) is str and value['kind'] == 'static-site', 'invalid_descriptor')
    hex_value(value['commit'], 40, 'invalid_descriptor')
    hex_value(value['manifest_sha256'], 64, 'invalid_descriptor')
    integer(value['file_count'], 1, MAX_FILES, 'invalid_descriptor')
    integer(value['total_bytes'], 1, MAX_TOTAL_BYTES, 'invalid_descriptor')
    require(all(value[key] == item for key, item in summary(files).items()), 'descriptor_mismatch')
    return value


def metadata(value):
    object_fields(value, META_FIELDS, 'invalid_metadata')
    integer(value['mode'], 0, 0o777, 'invalid_metadata')
    for key in ('uid', 'gid'):
        integer(value[key], 0, 2**32 - 2, 'invalid_metadata')


def installed(value):
    object_fields(value, {'target', 'generation', 'root', 'entries'}, 'invalid_inventory')
    require(type(value['target']) is str and value['target'] == TARGET, 'invalid_inventory')
    integer(value['generation'], 1, 2**63 - 1, 'invalid_inventory')
    metadata(value['root'])
    entries = value['entries']
    require(type(entries) is dict and len(entries) <= MAX_ENTRIES, 'invalid_inventory')
    total = 0
    for name, entry in entries.items():
        path(name)
        require(type(entry) is dict and type(entry.get('kind')) is str, 'invalid_inventory')
        if entry['kind'] == 'file':
            object_fields(entry, FILE_FIELDS, 'invalid_inventory')
            hex_value(entry['sha256'], 64, 'invalid_inventory')
            integer(entry['size'], 0, MAX_FILE_BYTES, 'invalid_inventory')
            integer(entry['nlink'], 1, 1, 'invalid_inventory')
            total += entry['size']
        else:
            require(entry['kind'] == 'directory', 'invalid_inventory')
            object_fields(entry, META_FIELDS | {'kind'}, 'invalid_inventory')
        metadata({key: entry[key] for key in META_FIELDS})
    require(total <= MAX_TOTAL_BYTES, 'size_limit')
    for name in entries:
        require(all(parent in entries and entries[parent]['kind'] == 'directory'
                    for parent in parents(name)), 'invalid_inventory')


def validate_baseline(value):
    object_fields(value, BASELINE_FIELDS, 'invalid_baseline')
    integer(value['schema_version'], 1, 1, 'invalid_baseline')
    integer(value['generation'], 1, 2**63 - 1, 'invalid_baseline')
    require(type(value['target']) is str and value['target'] == TARGET, 'invalid_baseline')
    hex_value(value['commit'], 40, 'invalid_baseline')
    for key in ('descriptor_sha256', 'manifest_sha256', 'owned_inventory_sha256'):
        hex_value(value[key], 64, 'invalid_baseline')
    object_fields(value['overlay'], OVERLAY_FIELDS, 'invalid_baseline')
    for side in ('prefix', 'suffix'):
        hex_value(value['overlay'][side + '_sha256'], 64, 'invalid_baseline')
        integer(value['overlay'][side + '_bytes'], 0, MAX_FILE_BYTES, 'invalid_baseline')


def file_record(data, meta):
    return {'kind': 'file', 'sha256': digest(data), 'size': len(data),
            'nlink': 1, **meta}


def phase(name):
    if name == MANIFEST:
        return 3
    if name == '.htaccess':
        return 2
    return 1 if name.endswith('.html') else 0


def build_plan(*, candidate, candidate_descriptor, expected_candidate_commit,
               predecessor, predecessor_descriptor, baseline, inventory,
               installed_htaccess, creation_metadata):
    """Return a new JSON-compatible plan, leaving all input objects unchanged.

    `inventory` is complete within this deliberately bounded offline scope.
    Baseline hashes must originate independently, never from the supplied inputs
    merely to make a failing comparison pass. Creation metadata is explicit policy.
    The plan is neither a write capability nor a validated product/provenance claim.
    """
    payload(candidate)
    payload(predecessor)
    new = descriptor(candidate_descriptor, candidate)
    old = descriptor(predecessor_descriptor, predecessor)
    hex_value(expected_candidate_commit, 40, 'invalid_commit')
    require(new['commit'] == expected_candidate_commit, 'candidate_identity_mismatch')
    installed(inventory)
    validate_baseline(baseline)
    object_fields(creation_metadata, {'file', 'directory'}, 'invalid_metadata')
    metadata(creation_metadata['file'])
    metadata(creation_metadata['directory'])
    require(baseline['generation'] == inventory['generation']
            and baseline['commit'] == old['commit']
            and baseline['descriptor_sha256'] == digest(predecessor_descriptor)
            and baseline['manifest_sha256'] == old['manifest_sha256'], 'baseline_mismatch')
    entries = inventory['entries']
    require(all(name in entries and entries[name]['kind'] == 'file' for name in predecessor), 'baseline_mismatch')
    owned_names = set(predecessor) | {parent for name in predecessor for parent in parents(name)}
    owned = {'root': inventory['root'], 'entries': {name: entries[name] for name in owned_names}}
    require(digest(canonical(owned)) == baseline['owned_inventory_sha256'], 'baseline_mismatch')
    for name, data in predecessor.items():
        if name != '.htaccess':
            require(entries[name]['sha256'] == digest(data) and entries[name]['size'] == len(data), 'predecessor_mismatch')
    require(type(installed_htaccess) is bytes and len(installed_htaccess) <= MAX_FILE_BYTES, 'invalid_overlay')
    access = entries['.htaccess']
    require(access['sha256'] == digest(installed_htaccess)
            and access['size'] == len(installed_htaccess), 'overlay_mismatch')
    block = predecessor['.htaccess']
    offset = installed_htaccess.find(block)
    require(offset >= 0 and installed_htaccess.find(block, offset + 1) < 0, 'ambiguous_overlay')
    prefix, suffix = installed_htaccess[:offset], installed_htaccess[offset + len(block):]
    overlay = {'prefix_sha256': digest(prefix), 'prefix_bytes': len(prefix),
               'suffix_sha256': digest(suffix), 'suffix_bytes': len(suffix)}
    require(overlay == baseline['overlay'], 'overlay_mismatch')
    composite = prefix + candidate['.htaccess'] + suffix
    require(len(composite) <= MAX_FILE_BYTES, 'size_limit')
    next_block = candidate['.htaccess']
    require(composite.find(next_block) == len(prefix)
            and composite.find(next_block, len(prefix) + 1) < 0, 'ambiguous_overlay')
    operations, directories = [], set()
    for name in sorted(candidate, key=lambda item: (phase(item), item)):
        before = entries.get(name)
        require(before is None or name in predecessor, 'unowned_collision')
        for parent in parents(name):
            require(parent not in entries or entries[parent]['kind'] == 'directory', 'path_collision')
            directories.add(parent)
        data = composite if name == '.htaccess' else candidate[name]
        meta = {key: before[key] for key in META_FIELDS} if before else creation_metadata['file']
        after = file_record(data, meta)
        operations.append({'path': name, 'phase': phase(name),
                           'operation': 'create' if before is None else 'keep' if before == after else 'replace',
                           'before': dict(before) if before else None, 'after': after,
                           'content': 'composite_htaccess' if name == '.htaccess' else 'candidate'})
    directory_plan = []
    for name in sorted(directories, key=lambda item: (len(item.split('/')), item)):
        before = entries.get(name)
        after = dict(before) if before else {'kind': 'directory', **creation_metadata['directory']}
        directory_plan.append({'path': name, 'operation': 'keep' if before else 'create',
                               'before': dict(before) if before else None, 'after': after})
    projected = {**entries, **{item['path']: item['after'] for item in directory_plan},
                 **{item['path']: item['after'] for item in operations}}
    installed({**inventory, 'entries': projected})
    plan = {'kind': 'static-operation-plan', 'schema_version': 1, 'target': TARGET,
            'plan_only': True, 'deployment_authorized': False,
            'expected_generation': baseline['generation'],
            'baseline_sha256': digest(canonical(baseline)),
            'candidate': {**new, 'descriptor_sha256': digest(candidate_descriptor)},
            'predecessor': {**old, 'descriptor_sha256': digest(predecessor_descriptor)},
            'root_metadata': dict(inventory['root']), 'operations': operations,
            'required_directories': directory_plan,
            'preserved_entries': sorted(set(entries) - set(candidate)),
            'composite_htaccess': {'sha256': digest(composite), 'size': len(composite),
                                   'artifact_sha256': digest(candidate['.htaccess']), **overlay}}
    return {**plan, 'plan_sha256': digest(canonical(plan))}
