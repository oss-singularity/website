"""Private, self-created POSIX fixtures for the offline static transition engine.

There is no existing-target or remote-target API. Handles are trusted test-runner
state, not input capabilities for untrusted Python. The owner-only container and
cooperative lock are not a sandbox against root or hostile same-UID processes.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid

import static_plan as plan
from site_artifact import (ArtifactError, MAX_DEPTH, MAX_ENTRIES, MAX_FILE_BYTES,
                           MAX_TOTAL_BYTES, fingerprint, open_directory,
                           read_regular, require)

JSON_LIMIT = 2 * 1024 * 1024
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def identity(info):
    return [info.st_dev, info.st_ino]


def metadata(info):
    value = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid}
    plan.metadata(value)
    return value


def stable(info):
    return (*fingerprint(info), info.st_uid, info.st_gid)


def read_file(fd, name, limit=MAX_FILE_BYTES):
    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'unsafe_file')
    data = read_regular(fd, name, limit, fingerprint(before))
    after = os.stat(name, dir_fd=fd, follow_symlinks=False)
    require(stable(before) == stable(after), 'tree_changed')
    return data, plan.file_record(data, metadata(after)), identity(after)


def snapshot(fd):
    """Capture bounded bytes, metadata and identities, including empty directories."""
    entries, contents, identities = {}, {}, {}
    total = 0

    def walk(parent, prefix, depth):
        nonlocal total
        require(depth <= MAX_DEPTH, 'tree_limit')
        before = os.fstat(parent)
        names = []
        with os.scandir(parent) as iterator:
            for entry in iterator:
                names.append(entry.name)
                require(len(entries) + len(names) <= MAX_ENTRIES, 'tree_limit')
        for name in sorted(names):
            relative = prefix + name
            plan.path(relative)
            require(len(entries) < MAX_ENTRIES, 'tree_limit')
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, DIR_FLAGS, dir_fd=parent)
                try:
                    require(stable(info) == stable(os.fstat(child)), 'tree_changed')
                    entries[relative] = {'kind': 'directory', **metadata(info)}
                    identities[relative] = identity(info)
                    walk(child, relative + '/', depth + 1)
                finally:
                    os.close(child)
            else:
                data, record, inode = read_file(parent, name)
                total += len(data)
                require(total <= MAX_TOTAL_BYTES, 'size_limit')
                entries[relative], contents[relative], identities[relative] = record, data, inode
        require(stable(before) == stable(os.fstat(parent)), 'tree_changed')

    walk(fd, '', 0)
    return metadata(os.fstat(fd)), entries, contents, identities


def write_new(fd, name, data, mode=0o600):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=fd)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            require(written > 0, 'short_write')
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        inode = identity(os.fstat(descriptor))
    finally:
        os.close(descriptor)
    os.fsync(fd)
    return inode


def read_json(fd, name):
    raw, record, _inode = read_file(fd, name, JSON_LIMIT)
    require(record['mode'] == 0o600 and record['uid'] == os.getuid(), 'unsafe_control')
    try:
        envelope = json.loads(raw)
        require(type(envelope) is dict and set(envelope) == {'value', 'sha256'}, 'invalid_journal')
        value = envelope['value']
        require(plan.canonical(envelope) == raw
                and envelope['sha256'] == plan.digest(plan.canonical(value)), 'invalid_journal')
        return value
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ArtifactError('invalid_journal') from None


def save_json(fd, name, value):
    raw = plan.canonical({'value': value, 'sha256': plan.digest(plan.canonical(value))})
    require(len(raw) <= JSON_LIMIT, 'journal_limit')
    pending = name + '.next'
    try:
        # A crash before rename may leave this owned, unpublished control file.
        _data, record, _inode = read_file(fd, pending, JSON_LIMIT)
        require(record['mode'] == 0o600 and record['uid'] == os.getuid(), 'unsafe_control')
        os.unlink(pending, dir_fd=fd)
    except FileNotFoundError:
        pass
    write_new(fd, pending, raw)
    os.replace(pending, name, src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)


@dataclass(frozen=True)
class Handle:
    """Pass only between trusted fixture processes; never deserialize from a candidate."""
    container: str
    nonce: str
    anchors: dict
    lock_identity: list


def check_anchors(handle, fds):
    for name, expected in handle.anchors.items():
        path = Path(handle.container) if name == 'container' else Path(handle.container) / name
        fresh = open_directory(path)
        try:
            info = os.fstat(fresh)
            require(identity(info) == expected and identity(os.fstat(fds[name])) == expected,
                    'fixture_identity_changed')
            require(info.st_uid == os.getuid(), 'unsafe_fixture')
            if name != 'target':
                require(stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_fixture')
        finally:
            os.close(fresh)
    marker, _record, _inode = read_file(fds['control'], 'owner', 128)
    require(marker == handle.nonce.encode('ascii'), 'fixture_identity_changed')
    lock = os.stat('lock', dir_fd=fds['control'], follow_symlinks=False)
    require(stat.S_ISREG(lock.st_mode) and lock.st_nlink == 1
            and identity(lock) == handle.lock_identity
            and stat.S_IMODE(lock.st_mode) == 0o600, 'unsafe_control')
    sentinel, _record, _inode = read_file(fds['peer'], 'sentinel', 128)
    require(sentinel == b'unrelated fixture: preserve\n', 'peer_changed')


@contextmanager
def locked(handle):
    require(type(handle) is Handle, 'invalid_fixture_handle')
    fds, lock = {}, None
    try:
        for name in ('container', 'target', 'control', 'peer'):
            path = Path(handle.container) if name == 'container' else Path(handle.container) / name
            fds[name] = open_directory(path)
        check_anchors(handle, fds)
        lock = os.open('lock', os.O_RDWR | os.O_NOFOLLOW, dir_fd=fds['control'])
        info = os.fstat(lock)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and identity(info) == handle.lock_identity
                and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_control')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ArtifactError('fixture_busy') from None
        check_anchors(handle, fds)
        yield fds
    except OSError:
        raise ArtifactError('fixture_io_failed') from None
    finally:
        if lock is not None:
            os.close(lock)
        for fd in fds.values():
            os.close(fd)


@contextmanager
def create_fixture(installation, baseline, *, file_mode=0o644, directory_mode=0o755):
    """Create a synthetic installation and store a SEPARATELY trusted baseline.

    Never derive this baseline from a rejected candidate/predecessor. Fixture
    builders know their expected initial installation independently. Real release
    provenance is deliberately outside this API. Nothing selects an existing root.
    """
    require(type(installation) is dict, 'invalid_fixture')
    plan.validate_baseline(baseline)
    file_meta = {'mode': file_mode, 'uid': os.getuid(), 'gid': os.getgid()}
    directory_meta = {**file_meta, 'mode': directory_mode}
    plan.metadata(file_meta)
    plan.metadata(directory_meta)
    entries = {}
    for name, data in installation.items():
        plan.path(name)
        require(type(data) is bytes and len(data) <= MAX_FILE_BYTES, 'invalid_fixture')
        for parent in plan.parents(name):
            require(parent not in installation, 'path_collision')
            entries[parent] = {'kind': 'directory', **directory_meta}
        entries[name] = plan.file_record(data, file_meta)
    plan.installed({'target': plan.TARGET, 'generation': baseline['generation'],
                    'root': directory_meta, 'entries': entries})
    root = Path(tempfile.mkdtemp(prefix='oss-static-fixture-'))
    anchor = identity(root.stat())
    try:
        for name in ('target', 'control', 'peer'):
            (root / name).mkdir(mode=0o700)
        (root / 'target').chmod(directory_mode)
        for name in sorted(entries, key=lambda item: (len(item.split('/')), item)):
            destination = root / 'target' / name
            if entries[name]['kind'] == 'directory':
                destination.mkdir(mode=0o700)
                destination.chmod(directory_mode)
            else:
                parent = open_directory(destination.parent)
                try:
                    write_new(parent, destination.name, installation[name], file_mode)
                finally:
                    os.close(parent)
        nonce = uuid.uuid4().hex
        control, peer = open_directory(root / 'control'), open_directory(root / 'peer')
        try:
            write_new(control, 'owner', nonce.encode())
            lock_id = write_new(control, 'lock', b'')
            write_new(peer, 'sentinel', b'unrelated fixture: preserve\n')
            save_json(control, 'state.json', {
                'schema_version': 1, 'nonce': nonce, 'generation': baseline['generation'],
                'baseline': baseline, 'attempt': None, 'attempt_count': 0,
                'creation_metadata': {'file': file_meta, 'directory': directory_meta}})
        finally:
            os.close(control)
            os.close(peer)
        anchors = {'container': anchor, **{name: identity((root / name).stat())
                                           for name in ('target', 'control', 'peer')}}
        require(len({tuple(value) for value in anchors.values()}) == 4
                and len({value[0] for value in anchors.values()}) == 1, 'unsafe_fixture')
        yield Handle(str(root), nonce, anchors, lock_id)
    except OSError:
        raise ArtifactError('fixture_io_failed') from None
    finally:
        require(not root.is_symlink() and identity(root.stat()) == anchor, 'fixture_identity_changed')
        # shutil.rmtree uses its descriptor-based symlink-safe implementation.
        require(shutil.rmtree.avoids_symlink_attacks, 'unsupported_filesystem')
        shutil.rmtree(root)
