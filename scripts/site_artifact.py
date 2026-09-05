"""Bounded, no-follow reads and one hash manifest for static site artifacts.

POSIX directory descriptors anchor reads. The returned bytes are a captured
snapshot, not a seal on the caller's mutable directory or proof of its origin.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

MANIFEST = 'dist-manifest.sha256'
MAX_FILES = 256
MAX_ENTRIES = 512
MAX_DEPTH = 8
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024


class ArtifactError(ValueError):
    """Only static error codes may cross the CLI boundary."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise ArtifactError(code)


def normalized_path(value):
    require(isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9._/-]+', value)), 'invalid_path')
    require(not value.startswith('/') and all(part not in {'', '.', '..'} for part in value.split('/')), 'invalid_path')
    return value


def absolute_path(path):
    return Path(os.path.abspath(os.fspath(path)))


def open_directory(path):
    """Open every component without following symlinks, including root aliases."""
    require(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'), 'unsupported_filesystem')
    path = absolute_path(path)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_regular(parent, name, limit, expected=None):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'unsafe_file')
        require(before.st_size <= limit, 'size_limit')
        if expected is not None:
            require(fingerprint(before) == expected, 'tree_changed')
        chunks, size = [], 0
        while True:
            block = os.read(fd, min(65536, limit + 1 - size))
            if not block:
                break
            chunks.append(block)
            size += len(block)
            require(size <= limit, 'size_limit')
        require(size == before.st_size and fingerprint(os.fstat(fd)) == fingerprint(before), 'tree_changed')
        return b''.join(chunks)
    finally:
        os.close(fd)


def read_external(path, limit):
    path = absolute_path(path)
    parent = open_directory(path.parent)
    try:
        return read_regular(parent, path.name, limit)
    finally:
        os.close(parent)


class TreeReader:
    def __init__(self, root):
        self.root = absolute_path(root)
        self.fd = open_directory(self.root)
        self.root_identity = fingerprint(os.fstat(self.fd))
        self.files = {}
        self.directories = {}
        self.cache = {}

    def close(self):
        os.close(self.fd)

    def inventory(self):
        files, directories = {}, {}
        entries, size = 0, 0

        def walk(fd, prefix, depth):
            nonlocal entries, size
            require(depth <= MAX_DEPTH, 'tree_limit')
            names = []
            with os.scandir(fd) as iterator:
                for entry in iterator:
                    entries += 1
                    require(entries <= MAX_ENTRIES, 'tree_limit')
                    names.append(entry.name)
            for name in sorted(names):
                relative = normalized_path(prefix + name)
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        require(fingerprint(os.fstat(child)) == fingerprint(info), 'tree_changed')
                        directories[relative] = fingerprint(info)
                        walk(child, relative + '/', depth + 1)
                        require(fingerprint(os.fstat(child)) == fingerprint(info), 'tree_changed')
                    finally:
                        os.close(child)
                else:
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe_file')
                    require(info.st_size <= MAX_FILE_BYTES, 'size_limit')
                    files[relative] = fingerprint(info)
                    size += info.st_size
                    require(len(files) <= MAX_FILES and size <= MAX_TOTAL_BYTES, 'tree_limit')
        walk(self.fd, '', 0)
        return files, directories

    def read(self, relative):
        normalized_path(relative)
        require(relative in self.files, 'missing_file')
        if relative in self.cache:
            return self.cache[relative]
        parent = os.dup(self.fd)
        try:
            parts = relative.split('/')
            prefix = ''
            for part in parts[:-1]:
                prefix += part
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = child
                require(fingerprint(os.fstat(parent)) == self.directories[prefix], 'tree_changed')
                prefix += '/'
            limit = MAX_MANIFEST_BYTES if relative == MANIFEST else MAX_FILE_BYTES
            self.cache[relative] = read_regular(parent, parts[-1], limit, self.files[relative])
            return self.cache[relative]
        finally:
            os.close(parent)

    def capture(self, required_files):
        self.files, self.directories = self.inventory()
        expected = required_files(set(self.files), self.read)
        require(set(self.files) == expected, 'allowlist_mismatch')
        expected_dirs = {str(parent) for name in expected for parent in Path(name).parents if str(parent) != '.'}
        require(set(self.directories) == expected_dirs, 'allowlist_mismatch')
        for name in sorted(expected):
            self.read(name)
        require(self.inventory() == (self.files, self.directories), 'tree_changed')
        require(fingerprint(os.fstat(self.fd)) == self.root_identity, 'tree_changed')
        current = open_directory(self.root)
        try:
            require(fingerprint(os.fstat(current)) == self.root_identity, 'tree_changed')
        finally:
            os.close(current)
        return dict(self.cache)


def capture(root, required_files):
    reader = TreeReader(root)
    try:
        files = reader.capture(required_files)
        verify_manifest(files)
        return files
    finally:
        reader.close()


def verify_manifest(files):
    data = files[MANIFEST]
    require(0 < len(data) <= MAX_MANIFEST_BYTES and data.endswith(b'\n') and b'\r' not in data, 'invalid_manifest')
    try:
        text = data.decode('ascii')
    except UnicodeError:
        raise ArtifactError('invalid_manifest') from None
    seen = set()
    for line in text.splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  (.+)', line)
        require(match is not None, 'invalid_manifest')
        name = match[2]
        if name.startswith('./'):
            name = name[2:]
        normalized_path(name)
        require(name != MANIFEST and name in files and name not in seen, 'invalid_manifest')
        require(hashlib.sha256(files[name]).hexdigest() == match[1], 'manifest_mismatch')
        seen.add(name)
    require(seen == set(files) - {MANIFEST}, 'manifest_coverage')


@contextmanager
def private_copy(files):
    """Product checkers read our captured bytes, never a caller's executable code."""
    with tempfile.TemporaryDirectory(prefix='oss-artifact-check-') as temporary:
        root = Path(temporary)
        for name, content in files.items():
            target = root / normalized_path(name)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(content)
        yield root


def summary(files):
    return {'manifest_sha256': hashlib.sha256(files[MANIFEST]).hexdigest(),
            'file_count': len(files), 'total_bytes': sum(len(value) for value in files.values())}


def write_descriptor(path, content, payload):
    target, payload = absolute_path(path), absolute_path(payload)
    require(not target.is_relative_to(payload), 'descriptor_inside_payload')
    parent = open_directory(target.parent)
    try:
        fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.fsync(parent)
    finally:
        os.close(parent)
