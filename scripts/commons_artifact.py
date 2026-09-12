"""Offline, bounded transport for Commons code and its existing schema profile.

Nothing here executes Worker code, contacts a provider or applies a migration.
Only hash-pinned initialization SQL is evaluated in a fresh in-memory database.
"""
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from site_artifact import ArtifactError, TreeReader, fingerprint, open_directory, read_external, require

REPOSITORY = 'oss-singularity/website'
MODULES = {'worker.mjs', 'security.mjs', 'identity.mjs', 'participations.mjs', 'activity.mjs', 'work-items.mjs'}
LOCAL_MODULES = {'local-d1.mjs', 'dev-server.mjs'}
MIGRATIONS = {
    '0001_commons.sql': '3832201d0a7d80c9b33e8908fc2b91354619d8326f69e8c4750363b38a98345e',
    '0002_participations.sql': '5a8f020d34c307b4fe87c66907299639c3eb6efb6b535cc0949bd96c72289ad3',
    '0003_work_items.sql': '45960c242ae7d41b5ed960724e3bdef8ef887670c32b5773c7d2e34137017fa0',
}
RUNTIME = {'entrypoint': 'worker.mjs', 'compatibility_date': '2026-09-04', 'compatibility_flags': []}
MAX_MODULE = 512 * 1024
MAX_CODE = 2 * 1024 * 1024
MAX_PACKET = 3 * 1024 * 1024
MAX_SCHEMA = 256 * 1024
SCHEMA_QUERY = ("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND name NOT GLOB 'sqlite_*' AND name != '_cf_KV' ORDER BY type,name")
PENDING = ['canonical-source-and-checks', 'trusted-artifact-transport', 'fresh-installed-schema',
           'scoped-provider-access', 'serialized-promotion', 'durable-recovery', 'live-verification']


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def commit(value):
    require(type(value) is str and re.fullmatch(r'[a-f0-9]{40}', value) is not None, 'invalid_commit')
    return value


def hash_value(value):
    require(type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) is not None, 'invalid_digest')
    return value


def sql_tokens(sql):
    """Ignore formatting outside SQL quotes, preserving literal and token boundaries."""
    require(type(sql) is str and 0 < len(sql.encode('utf-8')) <= MAX_SCHEMA
            and all(ord(c) >= 32 or c in '\t\r\n' for c in sql), 'invalid_schema')
    tokens, position = [], 0
    while position < len(sql):
        c = sql[position]
        if c in ' \t\r\n':
            position += 1
            continue
        if sql.startswith('--', position):
            end = sql.find('\n', position + 2)
            position = len(sql) if end < 0 else end + 1
            continue
        if sql.startswith('/*', position):
            end = sql.find('*/', position + 2)
            require(end >= 0, 'invalid_schema')
            position = end + 2
            continue
        start = position
        if c in "'\"`[":
            terminator = ']' if c == '[' else c
            position += 1
            while position < len(sql):
                if sql[position] == terminator:
                    position += 1
                    if c != '[' and position < len(sql) and sql[position] == terminator:
                        position += 1
                        continue
                    break
                position += 1
            else:
                raise ArtifactError('invalid_schema')
        elif c.isalnum() or c in '_$':
            position += 1
            while position < len(sql) and (sql[position].isalnum() or sql[position] in '_$'):
                position += 1
        else:
            position += 1
        tokens.append(sql[start:position])
    require(bool(tokens), 'invalid_schema')
    return tokens


def schema_hash(rows):
    """Fingerprint schema metadata only; never execute observed SQL or read data rows."""
    require(type(rows) is list and 0 < len(rows) <= 256 and len(encode(rows)) <= MAX_SCHEMA, 'invalid_schema')
    seen, normalized = set(), []
    for row in rows:
        require(type(row) is dict and set(row) == {'type', 'name', 'tbl_name', 'sql'}, 'invalid_schema')
        require(type(row['type']) is str and row['type'] in {'table', 'index', 'trigger', 'view'}, 'invalid_schema')
        for key in ['name', 'tbl_name']:
            require(type(row[key]) is str and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', row[key]), 'invalid_schema')
        key = (row['type'], row['name'])
        require(key not in seen, 'invalid_schema')
        seen.add(key)
        normalized.append({**row, 'sql': sql_tokens(row['sql'])})
    return digest(encode(sorted(normalized, key=lambda row: (row['type'], row['name']))))


def expected_schema(migrations):
    require(type(migrations) is dict and set(migrations) == set(MIGRATIONS), 'schema_profile_changed')
    for name, raw in migrations.items():
        require(type(raw) is bytes and digest(raw) == MIGRATIONS[name], 'schema_profile_changed')
    # Digest validation precedes every SQL evaluation. Candidate packets never
    # supply SQL to this function, and no caller-supplied database path exists.
    with sqlite3.connect(':memory:') as database:
        database.row_factory = sqlite3.Row
        for name in sorted(migrations):
            database.executescript(migrations[name].decode('utf-8'))
        rows = [dict(row) for row in database.execute(SCHEMA_QUERY)]
    return schema_hash(rows)


def validate_code(files):
    require(type(files) is dict and set(files) == MODULES, 'module_allowlist_mismatch')
    total = 0
    for raw in files.values():
        require(type(raw) is bytes and 0 < len(raw) <= MAX_MODULE and b'\0' not in raw, 'invalid_module')
        try:
            raw.decode('utf-8')
        except UnicodeError:
            raise ArtifactError('invalid_module') from None
        total += len(raw)
    require(total <= MAX_CODE, 'code_size_limit')
    return {name: {'sha256': digest(files[name]), 'size': len(files[name])} for name in sorted(files)}


def source_inputs(root):
    """Capture only the declared production files; detect unknown root modules/migrations."""
    reader = TreeReader(root)
    try:
        reader.files, reader.directories = reader.inventory()
        require({name for name in reader.files if '/' not in name and name.endswith('.mjs')}
                == MODULES | LOCAL_MODULES, 'source_module_allowlist_mismatch')
        require({name for name in reader.files if name.startswith('migrations/')}
                == {'migrations/' + name for name in MIGRATIONS}, 'schema_profile_changed')
        require(all(0 < reader.files[name][4] <= MAX_MODULE for name in MODULES), 'invalid_module')
        require(sum(reader.files[name][4] for name in MODULES) <= MAX_CODE, 'code_size_limit')
        files = {name: reader.read(name) for name in sorted(MODULES)}
        migrations = {name: reader.read('migrations/' + name) for name in sorted(MIGRATIONS)}
        require(reader.inventory() == (reader.files, reader.directories), 'tree_changed')
        current = open_directory(reader.root)
        try:
            require(fingerprint(os.fstat(current)) == reader.root_identity, 'tree_changed')
        finally:
            os.close(current)
        return files, migrations
    finally:
        reader.close()


def source_files(root):
    files, migrations = source_inputs(root)
    return files, expected_schema(migrations)


def metadata(files, sha, schema):
    return {'schema_version': 1, 'kind': 'commons-code', 'repository': REPOSITORY, 'commit': commit(sha),
            'runtime': {**RUNTIME, 'compatibility_flags': []},
            'schema': {'profile': 1, 'migrations': dict(MIGRATIONS), 'sha256': hash_value(schema)},
            'modules': validate_code(files)}


def packet(files, sha, schema):
    return encode({'descriptor': metadata(files, sha, schema),
                   'files': {name: base64.b64encode(raw).decode('ascii') for name, raw in sorted(files.items())}})


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_packet_key')
        result[key] = value
    return result


def invalid_constant(_value):
    raise ArtifactError('invalid_packet')


def unpack(raw, sha, expected_schema_sha):
    commit(sha)
    hash_value(expected_schema_sha)
    require(type(raw) is bytes and 0 < len(raw) <= MAX_PACKET, 'packet_size_limit')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid_constant)
        require(type(value) is dict and set(value) == {'descriptor', 'files'}, 'invalid_packet')
        encoded = value['files']
        require(type(encoded) is dict and set(encoded) == MODULES, 'module_allowlist_mismatch')
        files = {}
        for name, content in encoded.items():
            require(type(content) is str and len(content) <= 4 * ((MAX_MODULE + 2) // 3), 'invalid_module')
            data = base64.b64decode(content, validate=True)
            require(base64.b64encode(data).decode('ascii') == content, 'invalid_module')
            files[name] = data
        descriptor = value['descriptor']
        require(type(descriptor) is dict and type(descriptor.get('schema_version')) is int, 'invalid_descriptor')
        schema = descriptor.get('schema')
        require(type(schema) is dict and type(schema.get('profile')) is int, 'invalid_descriptor')
        sizes = descriptor.get('modules')
        require(type(sizes) is dict and set(sizes) == MODULES, 'invalid_descriptor')
        require(all(type(row) is dict and type(row.get('size')) is int for row in sizes.values()), 'invalid_descriptor')
        require(descriptor == metadata(files, sha, expected_schema_sha), 'descriptor_mismatch')
        return files, descriptor
    except (UnicodeError, json.JSONDecodeError, RecursionError, binascii.Error, ValueError) as error:
        if isinstance(error, ArtifactError):
            raise
        raise ArtifactError('invalid_packet') from None


def report(descriptor, rebuilt=False):
    return {**descriptor, 'artifact_verified': True, 'rebuild_matched': rebuilt,
            'deployment_authorized': False, 'pending_gates': list(PENDING)}


def create(source, sha, destination):
    source, destination = Path(os.path.abspath(source)), Path(os.path.abspath(destination))
    require(source not in destination.parents, 'output_inside_source')
    files, schema = source_files(source)
    raw = packet(files, sha, schema)
    # Reopen the exact output independently in verify; never overwrite an older
    # artifact or follow an output symlink. Parent directories must already exist.
    parent = TreeReader(destination.parent)
    try:
        fd = os.open(destination.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent.fd)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        parent.close()
    return report(metadata(files, sha, schema))


def verify(path, sha, schema, source=None):
    files, descriptor = unpack(read_external(path, MAX_PACKET), sha, schema)
    if source is not None:
        rebuilt, rebuilt_schema = source_files(source)
        require(files == rebuilt and schema == rebuilt_schema, 'rebuild_mismatch')
    return report(descriptor, source is not None)
