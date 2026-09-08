"""Bounded POSIX primitives shared by separately anchored static backends.

These helpers do not choose a target or establish release authority. Each caller
must supply its own independently bound descriptors and cooperative lock.
"""
import json
import os
import stat

import static_plan as plan
from site_artifact import (ArtifactError, MAX_DEPTH, MAX_ENTRIES, MAX_FILE_BYTES,
                           MAX_TOTAL_BYTES, fingerprint, read_regular, require)

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
