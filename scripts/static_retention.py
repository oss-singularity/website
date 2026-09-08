"""Bounded maintenance of registered private release material under the target lock.

No target files are written. Keep the current attempt and its predecessor. Older
material is eligible only through records made before replacing that attempt.
An interrupted deletion plan must finish before another release can begin.
"""
from contextlib import contextmanager
import os
import re
import stat

import static_engine as engine
import static_plan as plan
import static_posix as fs
from site_artifact import require

ALLOCATION = 'allocation.json'
RETENTION = 'retention.json'
MAX_MATERIAL = 4 * plan.MAX_ENTRIES


def optional(control, name):
    try:
        return fs.read_json(control, name)
    except FileNotFoundError:
        return None


def remove(control, name):
    os.unlink(name, dir_fd=control)
    os.fsync(control)


def state_digest(transition):
    return plan.digest(plan.canonical({key: value for key, value in transition.state.items()
                                      if key != 'attempt_count'}))


def allocation(transition, identity):
    control = transition.fds['control']
    try:
        os.stat(identity, dir_fd=control, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        require(False, 'stale_attempt')
    require(optional(control, ALLOCATION) is None, 'maintenance_required')
    if transition.state['attempt'] is not None:
        attempt = transition.attempt
        require(attempt['phase'] in engine.TERMINAL, 'unfinished_attempt')
        archive = {'schema': 1, 'nonce': transition.handle.nonce, 'identity': attempt['id'],
                   'material_identity': attempt['material_identity'],
                   'descriptors': sorted({attempt['start_baseline']['descriptor_sha256'],
                       attempt['plan']['candidate']['descriptor_sha256']})}
        # Only the still-current attempt may replace its archive (for example,
        # after a failed new preparation followed by rollback of that attempt).
        fs.save_json(control, 'archive-' + attempt['id'] + '.json', archive)
    fs.save_json(control, ALLOCATION, {'schema': 1, 'nonce': transition.handle.nonce,
                                     'identity': identity, 'state_sha256': state_digest(transition)})
    transition._event('allocation-reserved')


def recover_allocation(transition):
    control = transition.fds['control']
    pending = optional(control, ALLOCATION)
    if pending is None:
        return
    plan.object_fields(pending, {'schema', 'nonce', 'identity', 'state_sha256'}, 'unsafe_control')
    require(type(pending['schema']) is int and pending['schema'] == 1
            and pending['nonce'] == transition.handle.nonce, 'unsafe_control')
    identity = pending['identity']
    plan.hex_value(identity, 32, 'unsafe_control')
    plan.hex_value(pending['state_sha256'], 64, 'unsafe_control')
    current = transition.state['attempt']
    if current is not None and current['id'] == identity:
        # The observed journal was committed before any staging. It now owns
        # this directory, even when a later staging write was interrupted.
        with transition._material():
            pass
    else:
        require(state_digest(transition) == pending['state_sha256'], 'maintenance_conflict')
        try:
            child = os.open(identity, fs.DIR_FLAGS, dir_fd=control)
        except FileNotFoundError:
            child = None
        if child is not None:
            try:
                require(fs.metadata(os.fstat(child)) == {'mode': 0o700, 'uid': os.getuid(),
                                                        'gid': os.getgid()}
                        and not os.listdir(child), 'maintenance_conflict')
                require(fs.identity(os.fstat(child)) == fs.identity(os.stat(
                    identity, dir_fd=control, follow_symlinks=False)), 'maintenance_conflict')
                os.rmdir(identity, dir_fd=control)
                os.fsync(control)
            finally:
                os.close(child)
    remove(control, ALLOCATION)
    transition._event('allocation-recovered')


@contextmanager
def material(control, name, identity):
    child = os.open(name, fs.DIR_FLAGS, dir_fd=control)
    try:
        require(fs.identity(os.fstat(child)) == identity
                and fs.metadata(os.fstat(child)) == {'mode': 0o700, 'uid': os.getuid(),
                                                     'gid': os.getgid()}, 'maintenance_conflict')
        yield child
    finally:
        os.close(child)


def record(parent, name):
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    require(info.st_uid == os.getuid() and info.st_gid == os.getgid(), 'maintenance_conflict')
    if stat.S_ISDIR(info.st_mode):
        child = os.open(name, fs.DIR_FLAGS, dir_fd=parent)
        try:
            require(fs.identity(os.fstat(child)) == fs.identity(info) and not os.listdir(child)
                    and stat.S_IMODE(info.st_mode) in {0o700, 0o755}, 'maintenance_conflict')
        finally:
            os.close(child)
        value = {'kind': 'directory', **fs.metadata(info)}
        inode = fs.identity(info)
    else:
        _raw, value, inode = fs.read_file(parent, name)
        require(value['mode'] in {0o600, 0o644}, 'maintenance_conflict')
    return {'name': name, 'record': value, 'identity': inode}


def archives(transition):
    control = transition.fds['control']
    # The entire namespace is bounded; unknown entries are never pruned.
    names = os.listdir(control)
    require(len(names) <= 48, 'maintenance_conflict')
    values = []
    for name in sorted(names):
        if re.fullmatch(r'archive-[a-f0-9]{32}\.json', name) is None:
            continue
        value = fs.read_json(control, name)
        plan.object_fields(value, {'schema', 'nonce', 'identity', 'material_identity',
                                   'descriptors'}, 'unsafe_control')
        require(type(value['schema']) is int and value['schema'] == 1
                and value['nonce'] == transition.handle.nonce
                and value['identity'] == name[8:-5], 'unsafe_control')
        require(type(value['material_identity']) is list and len(value['material_identity']) == 2
                and all(type(number) is int and number > 0 for number in value['material_identity']),
                'unsafe_control')
        require(type(value['descriptors']) is list and 1 <= len(value['descriptors']) <= 2,
                'unsafe_control')
        for digest in value['descriptors']:
            plan.hex_value(digest, 64, 'unsafe_control')
        if value['identity'] != transition.attempt['id']:
            values.append((name, value))
    require(len(values) <= 7, 'maintenance_conflict')
    require(transition.state['attempt_count'] == len(values) + 1, 'maintenance_conflict')
    registered = {value['identity'] for _name, value in values} | {transition.attempt['id']}
    require(all(re.fullmatch(r'[a-f0-9]{32}', name) is None or name in registered for name in names),
            'maintenance_conflict')
    return values


def build_deletion(transition, name, archive, other_archives):
    control = transition.fds['control']
    entries, total = [], 0
    with material(control, archive['identity'], archive['material_identity']) as child:
        names = os.listdir(child)
        require(len(names) <= MAX_MATERIAL, 'maintenance_conflict')
        for filename in sorted(names):
            match = re.fullmatch(r'(after|before|publish|restore)-([0-9]+)', filename)
            require(match is not None and int(match[2]) < plan.MAX_ENTRIES, 'maintenance_conflict')
            entry = record(child, filename)
            total += entry['record'].get('size', 0)
            require(total <= 3 * plan.MAX_TOTAL_BYTES, 'maintenance_conflict')
            entries.append(entry)
    keep = {transition.state['baseline']['descriptor_sha256'],
            transition.attempt['start_baseline']['descriptor_sha256'],
            transition.attempt['plan']['candidate']['descriptor_sha256']}
    for _other_name, other in other_archives:
        keep.update(other['descriptors'])
    descriptors = []
    for digest in archive['descriptors']:
        if digest in keep:
            continue
        filename = 'descriptor-' + digest
        try:
            raw, value, inode = fs.read_file(control, filename, 4096)
        except FileNotFoundError:
            continue  # Preparation can be aborted before remembering it.
        require(plan.digest(raw) == digest and value['mode'] == 0o600
                and value['uid'] == os.getuid(), 'maintenance_conflict')
        descriptors.append({'name': filename, 'record': value, 'identity': inode})
    require(transition.state['attempt_count'] >= 2, 'maintenance_conflict')
    return {'schema': 1, 'nonce': transition.handle.nonce,
            'state_sha256': state_digest(transition), 'count_before': transition.state['attempt_count'],
            'material': archive['identity'], 'material_identity': archive['material_identity'],
            'entries': entries, 'descriptors': descriptors, 'archive': record(control, name)}


def delete_entry(parent, entry):
    try:
        actual = record(parent, entry['name'])
    except FileNotFoundError:
        return  # A prior invocation may have completed the unlink.
    require(actual == entry, 'maintenance_conflict')
    if entry['record']['kind'] == 'directory':
        os.rmdir(entry['name'], dir_fd=parent)
    else:
        os.unlink(entry['name'], dir_fd=parent)
    os.fsync(parent)


def execute_deletion(transition, pending):
    control = transition.fds['control']
    plan.object_fields(pending, {'schema', 'nonce', 'state_sha256', 'count_before', 'material',
        'material_identity', 'entries', 'descriptors', 'archive'}, 'unsafe_control')
    require(type(pending['schema']) is int and pending['schema'] == 1
            and pending['nonce'] == transition.handle.nonce
            and pending['state_sha256'] == state_digest(transition), 'maintenance_conflict')
    plan.integer(pending['count_before'], 2, 8, 'unsafe_control')
    require(transition.state['attempt_count'] in {pending['count_before'], pending['count_before'] - 1},
            'maintenance_conflict')
    plan.hex_value(pending['material'], 32, 'unsafe_control')
    require(pending['material'] != transition.attempt['id'], 'maintenance_conflict')
    require(type(pending['entries']) is list and len(pending['entries']) <= MAX_MATERIAL
            and type(pending['descriptors']) is list and len(pending['descriptors']) <= 2,
            'unsafe_control')
    for entry in pending['entries']:
        require(type(entry) is dict and re.fullmatch(r'(after|before|publish|restore)-[0-9]+',
                                                    entry.get('name', '')) is not None, 'unsafe_control')
    for entry in pending['descriptors']:
        require(type(entry) is dict and re.fullmatch(r'descriptor-[a-f0-9]{64}',
                                                    entry.get('name', '')) is not None, 'unsafe_control')
    require(pending['archive']['name'] == 'archive-' + pending['material'] + '.json', 'unsafe_control')
    try:
        context = material(control, pending['material'], pending['material_identity'])
        child = context.__enter__()
    except FileNotFoundError:
        child = None
    if child is not None:
        try:
            for index, entry in enumerate(pending['entries']):
                transition.check_anchors(transition.handle, transition.fds)
                delete_entry(child, entry)
                transition._event(f'retention-entry-{index}')
            require(not os.listdir(child), 'maintenance_conflict')
            require(fs.identity(os.stat(pending['material'], dir_fd=control, follow_symlinks=False))
                    == pending['material_identity'], 'maintenance_conflict')
            os.rmdir(pending['material'], dir_fd=control)
            os.fsync(control)
        finally:
            context.__exit__(None, None, None)
    transition._event('retention-material-removed')
    for entry in pending['descriptors']:
        delete_entry(control, entry)
    delete_entry(control, pending['archive'])
    transition._event('retention-records-removed')
    transition.state['attempt_count'] = pending['count_before'] - 1
    transition._save()
    transition._event('retention-count-committed')
    remove(control, RETENTION)
    transition._event('retention-completed')


def maintain(transition, expected):
    transition._load()
    transition._require_ticket(expected)
    require(transition.attempt['phase'] in engine.TERMINAL
            and transition.attempt['pending'] is None, 'unfinished_attempt')
    if transition.attempt['phase'] == 'rolled_back':
        generation = transition.attempt['rollback_generation'] + 1
        require(transition.state['generation'] == generation
                and transition.state['baseline'] == {**transition.attempt['start_baseline'],
                                                     'generation': generation}, 'generation_conflict')
    else:
        transition._generation()
    transition._validate_target()
    if transition.attempt['phase'] == 'verified':
        transition._verify_material()
    pending = optional(transition.fds['control'], RETENTION)
    if pending is not None:
        execute_deletion(transition, pending)
    remaining = archives(transition)
    for index, (name, archive) in enumerate(remaining):
        pending = build_deletion(transition, name, archive, remaining[index + 1:])
        fs.save_json(transition.fds['control'], RETENTION, pending)
        transition._event('retention-intent')
        execute_deletion(transition, pending)
    transition._validate_target()
    return {**transition.report(include_ticket=True),
            'retained_attempts': transition.state['attempt_count'], 'maintenance_pending': False}
