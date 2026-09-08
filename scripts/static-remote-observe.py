#!/usr/bin/env python3
"""Fixed-command SSH observer. No upload, execution, repair or publication API.

Install independently with private operator-owned configuration; launch with
an absolute Python 3.11+ interpreter and -I -S. SSH key restrictions belong to
sshd's authorized_keys configuration, not to this program's command check.
"""
import fcntl
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import signal
import stat
import sys

COMMAND = 'oss-static-observe-v1'
MANIFEST = 'dist-manifest.sha256'
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_MANIFEST = 64 * 1024
MAX_CONFIG = 16384
ERRORS = {'command_rejected', 'invalid_arguments', 'unsupported_runtime', 'invalid_config',
          'unsafe_file', 'size_limit', 'invalid_manifest', 'identity_changed', 'state_changed',
          'unsafe_control', 'target_busy', 'observation_timeout', 'observation_failed'}


class Rejected(Exception):
    pass


def need(condition, code):
    if not condition:
        raise Rejected(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def identity(info):
    return [info.st_dev, info.st_ino]


def fingerprint(info):
    # Reading may update atime. That is not a content/ownership change.
    return [info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
            info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def absolute(value):
    need(type(value) is str and 1 < len(value) < 4096 and value.startswith('/')
         and '\x00' not in value and all(part not in {'', '.', '..'} for part in value.split('/')[1:]),
         'invalid_config')
    return value


def open_directory(value):
    """Reject symlinks in every ancestor, not just the final component."""
    value = absolute(value)
    fd = os.open('/', DIR_FLAGS)
    try:
        for name in value.split('/')[1:]:
            child = os.open(name, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def capture_file(parent, name, limit, private=False):
    fd = os.open(name, FILE_FLAGS, dir_fd=parent)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'unsafe_file')
        if private:
            need(before.st_uid == os.getuid() and stat.S_IMODE(before.st_mode) == 0o600,
                 'unsafe_control')
        need(before.st_size <= limit, 'size_limit')
        chunks, size = [], 0
        while True:
            raw = os.read(fd, min(65536, limit + 1 - size))
            if not raw:
                break
            chunks.append(raw)
            size += len(raw)
            need(size <= limit, 'size_limit')
        need(size == before.st_size and fingerprint(before) == fingerprint(os.fstat(fd)), 'state_changed')
        need(fingerprint(before) == fingerprint(os.stat(name, dir_fd=parent, follow_symlinks=False)),
             'state_changed')
        return b''.join(chunks), fingerprint(before)
    finally:
        os.close(fd)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 'invalid_config')
        result[key] = value
    return result


def load_config(path):
    path = absolute(path)
    parent_path, name = path.rsplit('/', 1)
    parent = open_directory(parent_path)
    try:
        info = os.fstat(parent)
        need(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_control')
        raw, _ = capture_file(parent, name, MAX_CONFIG, private=True)
    finally:
        os.close(parent)
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
    except (UnicodeError, ValueError, RecursionError):
        raise Rejected('invalid_config') from None
    need(type(value) is dict and set(value) == {'schema', 'target', 'root', 'control',
                                               'root_identity', 'control_identity', 'lock_identity'},
         'invalid_config')
    need(type(value['schema']) is int and value['schema'] == 1 and value['target'] == 'oss-static',
         'invalid_config')
    root, control = absolute(value['root']), absolute(value['control'])
    need(os.path.commonpath([root, control]) not in {root, control}, 'invalid_config')
    need(os.path.commonpath([root, path]) != root, 'invalid_config')
    for key in ('root_identity', 'control_identity', 'lock_identity'):
        parts = value[key]
        need(type(parts) is list and len(parts) == 2
             and all(type(part) is int and 0 <= part < 2 ** 64 for part in parts), 'invalid_config')
    return value


def parse_manifest(raw):
    need(0 < len(raw) <= MAX_MANIFEST and raw.endswith(b'\n'), 'invalid_manifest')
    records = {}
    for line in raw[:-1].split(b'\n'):
        match = re.fullmatch(rb'([a-f0-9]{64})  ([A-Za-z0-9._/-]+)', line)
        need(match is not None, 'invalid_manifest')
        name = match[2].decode('ascii')
        parts = name.split('/')
        need(len(name) <= 512 and len(parts) <= 8 and not name.startswith('/')
             and all(part not in {'', '.', '..'} for part in parts)
             and name != MANIFEST and name not in records, 'invalid_manifest')
        records[name] = match[1].decode('ascii')
        need(len(records) <= 256, 'invalid_manifest')
    need(records and '.htaccess' in records, 'invalid_manifest')
    return records


def relative_parent(root, name, directories):
    fd = os.dup(root)
    prefix = ''
    try:
        for part in name.split('/')[:-1]:
            prefix = prefix + '/' + part if prefix else part
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
            info = fingerprint(os.fstat(fd))
            if prefix in directories:
                need(directories[prefix] == info, 'state_changed')
            directories[prefix] = info
        return fd
    except BaseException:
        os.close(fd)
        raise


def verify_anchors(config, root, control, lock):
    for name, descriptor in [('root', root), ('control', control)]:
        fresh = open_directory(config[name])
        try:
            info = os.fstat(fresh)
            need(identity(info) == config[name + '_identity'] == identity(os.fstat(descriptor)),
                 'identity_changed')
            need(info.st_uid == os.getuid(), 'unsafe_control')
            if name == 'control':
                need(stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_control')
        finally:
            os.close(fresh)
    info = os.stat('lock', dir_fd=control, follow_symlinks=False)
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
         and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_control')
    need(identity(info) == config['lock_identity'] == identity(os.fstat(lock)), 'identity_changed')


def observe(config):
    root = control = lock = None
    try:
        root = open_directory(config['root'])
        control = open_directory(config['control'])
        lock = os.open('lock', FILE_FLAGS, dir_fd=control)
        verify_anchors(config, root, control, lock)
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Rejected('target_busy') from None
        verify_anchors(config, root, control, lock)
        root_info = fingerprint(os.fstat(root))
        raw_manifest, manifest_info = capture_file(root, MANIFEST, MAX_MANIFEST)
        declared = parse_manifest(raw_manifest)
        directories, files, observations = {}, {}, {}
        total, differences = 0, 0
        htaccess_differs = False
        for name in sorted(declared):
            parent = relative_parent(root, name, directories)
            try:
                raw, info = capture_file(parent, name.split('/')[-1], MAX_FILE)
            finally:
                os.close(parent)
            total += len(raw)
            need(total <= MAX_TOTAL, 'size_limit')
            observed_hash = digest(raw)
            files[name] = info
            observations[name] = {'sha256': observed_hash, 'bytes': len(raw), 'metadata': info[2:6]}
            if observed_hash != declared[name]:
                if name == '.htaccess':
                    htaccess_differs = True
                else:
                    differences += 1
        # Reopen all paths after capture, including every parent directory.
        # This detects concurrent changes; it is not a filesystem snapshot/CAS.
        final_directories = {}
        for name, info in files.items():
            parent = relative_parent(root, name, final_directories)
            try:
                after = os.stat(name.split('/')[-1], dir_fd=parent, follow_symlinks=False)
                need(info == fingerprint(after), 'state_changed')
            finally:
                os.close(parent)
        need(directories == final_directories, 'state_changed')
        need(manifest_info == fingerprint(os.stat(MANIFEST, dir_fd=root, follow_symlinks=False))
             and root_info == fingerprint(os.fstat(root)), 'state_changed')
        verify_anchors(config, root, control, lock)
        return {'schema': 1, 'target': 'oss-static', 'operation': 'observe', 'read_only': True,
                'python': list(sys.version_info[:3]), 'manifest_sha256': digest(raw_manifest),
                'observed_inventory_sha256': digest(canonical(observations)), 'files': len(files),
                'bytes': total, 'htaccess_differs': htaccess_differs,
                'other_content_differences': differences, 'deployment_authorized': False}
    finally:
        for fd in (lock, control, root):
            if fd is not None:
                os.close(fd)


def timed_out(_signum, _frame):
    raise Rejected('observation_timeout')


def main():
    try:
        need(os.environ.get('SSH_ORIGINAL_COMMAND') == COMMAND, 'command_rejected')
        need(len(sys.argv) == 3 and sys.argv[1] == '--config', 'invalid_arguments')
        need(sys.version_info >= (3, 11) and sys.flags.isolated and sys.flags.no_site, 'unsupported_runtime')
        signal.signal(signal.SIGALRM, timed_out)
        signal.alarm(20)
        config = load_config(sys.argv[2])
        result = observe(config)
        source = os.path.abspath(__file__)
        parent = open_directory(str(PurePosixPath(source).parent))
        try:
            raw, _ = capture_file(parent, PurePosixPath(source).name, MAX_CONFIG * 2)
            result['observer_sha256'] = digest(raw)
        finally:
            os.close(parent)
        signal.alarm(0)
        print(canonical(result).decode('ascii'))
        return 0
    except Exception as error:
        signal.alarm(0)
        code = str(error) if type(error) is Rejected and str(error) in ERRORS else 'observation_failed'
        print(canonical({'schema': 1, 'target': 'oss-static', 'error': code,
                         'deployment_authorized': False}).decode('ascii'))
        return 1


if __name__ == '__main__':
    sys.exit(main())
