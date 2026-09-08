#!/usr/bin/env python3
"""Installed, isolated SSH launcher for the fixed static release command.

Copy this exact reviewed bundle to a private operator-owned directory. The SSH
key must have restrict and one forced command selecting this interpreter, file
and private configuration. This launcher cannot install or update itself.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types

MODULES = (
    ('site_artifact', 'site_artifact.py'),
    ('static_plan', 'static_plan.py'),
    ('static_posix', 'static_posix.py'),
    ('static_engine', 'static_engine.py'),
    ('static_remote_observer', 'static-remote-observe.py'),
    ('static_policy', 'static_policy.py'),
    ('static_retention', 'static_retention.py'),
    ('static_remote', 'static_remote.py'),
)


def require(condition):
    if not condition:
        raise ValueError('invalid installed runtime')


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
            info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def load_runtime():
    folder = Path(__file__).absolute().parent
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    try:
        for part in folder.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) in {0o500, 0o700})
        sources, hashes = {}, {}
        for filename in [Path(__file__).name] + [name for _module, name in MODULES]:
            source = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                info = os.fstat(source)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                        and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o400
                        and info.st_size <= 128 * 1024)
                raw = os.read(source, 128 * 1024 + 1)
                after = os.fstat(source)
                require(len(raw) == info.st_size and fingerprint(after) == fingerprint(info)
                        and fingerprint(os.stat(filename, dir_fd=fd, follow_symlinks=False))
                        == fingerprint(info))
                sources[filename] = raw
                hashes[filename] = hashlib.sha256(raw).hexdigest()
            finally:
                os.close(source)
        for name, filename in MODULES:
            require(name not in sys.modules)
            module = types.ModuleType(name)
            module.__file__ = str(folder / filename)
            sys.modules[name] = module
            exec(compile(sources[filename], module.__file__, 'exec'), module.__dict__)
        digest = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return sys.modules['static_remote'], folder, digest
    finally:
        os.close(fd)


def main():
    if os.environ.get('SSH_ORIGINAL_COMMAND') != 'oss-static-release-v1':
        return {'error': 'command_rejected'}, 1
    if sys.version_info < (3, 11) or not sys.flags.isolated or not sys.flags.no_site:
        return {'error': 'unsupported_runtime'}, 1
    if len(sys.argv) != 3 or sys.argv[1] != '--config':
        return {'error': 'invalid_arguments'}, 1
    remote = None
    try:
        remote, folder, digest = load_runtime()
        result = remote.main(sys.argv[2], str(folder))
        return {**result, 'runtime_sha256': digest}, 0
    except Exception as error:
        code = getattr(error, 'code', '')
        if remote is None or code not in remote.PUBLIC_ERRORS:
            code = 'release_failed'
        return {'error': code, 'filesystem_outcome': 'unconfirmed'}, 1


if __name__ == '__main__':
    response, status = main()
    print(json.dumps(response, sort_keys=True))
    raise SystemExit(status)
