#!/usr/bin/env python3
"""Independently check one completed canonical rehearsal; never authorize deployment."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import urllib.request
import zipfile
import zlib

import site_artifact as tree
from site_artifact import ArtifactError, require

# These modules belong to the installed verifier, never to a downloaded archive.
def trusted_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(tree.__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rehearsal = trusted_module('candidate_rehearsal', 'release-rehearsal.py')
artifact = rehearsal.artifact
REPOSITORY = 'oss-singularity/website'
REPOSITORY_ID = 1351274990
WORKFLOW_ID = 351107990
WORKFLOW_PATH = '.github/workflows/static-release-rehearsal.yml'
API_URL = 'https://api.github.com'
BASE_ROUTE = '/repos/' + REPOSITORY
BRANCH_ROUTE = BASE_ROUTE + '/branches/main'
WORKFLOW_ROUTE = BASE_ROUTE + '/actions/workflows/' + str(WORKFLOW_ID)
MAX_ARCHIVE = tree.MAX_TOTAL_BYTES + 1024 * 1024
MAX_CENTRAL = 256 * 1024
MAX_RECEIPT = 16384
PENDING_GATES = ['required-github-checks', 'fresh-protected-head-at-promotion', 'serialized-promotion',
                 'api-compatibility', 'provider-authorization', 'preserved-overlays', 'live-verification', 'rollback']
ERROR_CODES = {'invalid_arguments', 'invalid_identity', 'invalid_commit', 'invalid_digest', 'invalid_json',
               'invalid_api_route', 'missing_api_authentication', 'github_read_failed', 'unprotected_main',
               'stale_main', 'workflow_identity_mismatch', 'run_identity_mismatch', 'run_not_successful',
               'artifact_identity_mismatch', 'artifact_unavailable_or_changed', 'archive_digest_mismatch',
               'invalid_archive', 'archive_limit', 'archive_layout', 'invalid_receipt', 'payload_verification_failed',
               'invalid_path', 'unsupported_filesystem', 'unsafe_file', 'size_limit', 'tree_changed'}


def run_route(run_id, attempt=None):
    path = BASE_ROUTE + '/actions/runs/' + str(run_id)
    return path if attempt is None else path + '/attempts/' + str(attempt)


def artifact_route(artifact_id):
    return BASE_ROUTE + '/actions/artifacts/' + str(artifact_id)


def github_get(route, environ):
    number = r'[1-9][0-9]{0,18}'
    allowed = (route in {BRANCH_ROUTE, WORKFLOW_ROUTE}
               or re.fullmatch(re.escape(BASE_ROUTE) + r'/actions/runs/' + number + r'(?:/attempts/' + number + r')?', route)
               or re.fullmatch(re.escape(BASE_ROUTE) + r'/actions/artifacts/' + number, route))
    require(allowed, 'invalid_api_route')
    authorization = environ.get('GH_TOKEN')
    require(type(authorization) is str and re.fullmatch(r'[\x21-\x7e]{1,8192}', authorization) is not None,
            'missing_api_authentication')
    url = API_URL + route
    try:
        request = urllib.request.Request(url, headers={
            'Authorization': 'Bearer ' + authorization, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2026-03-10', 'Cache-Control': 'no-cache',
            'User-Agent': 'oss-singularity-candidate-verifier',
        }, method='GET')
        opener = urllib.request.build_opener(rehearsal.NoRedirect())
        with opener.open(request, timeout=10) as response:
            require(response.status == 200 and response.geturl() == url, 'github_read_failed')
            raw = response.read(rehearsal.MAX_JSON + 1)
    except ArtifactError:
        raise
    except Exception:
        raise ArtifactError('github_read_failed') from None
    return rehearsal.decode_object(raw, rehearsal.MAX_JSON)


def exact(actual, expected, code):
    # JSON spelling distinguishes booleans, integers and floats, unlike ==.
    require(json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True), code)


def check_run(value, sha, run_id, attempt):
    require(type(value) is dict, 'run_identity_mismatch')
    for key, expected in [('id', run_id), ('run_attempt', attempt), ('workflow_id', WORKFLOW_ID)]:
        require(type(value.get(key)) is int and value[key] == expected, 'run_identity_mismatch')
    require(value.get('path') in {WORKFLOW_PATH, WORKFLOW_PATH + '@main'}, 'run_identity_mismatch')
    for key, expected in [('event', 'push'), ('head_branch', 'main'), ('head_sha', sha)]:
        require(value.get(key) == expected, 'run_identity_mismatch')
    for key in ['repository', 'head_repository']:
        repo = value.get(key)
        require(type(repo) is dict and type(repo.get('id')) is int and repo['id'] == REPOSITORY_ID
                and repo.get('full_name') == REPOSITORY and repo.get('fork') is False, 'run_identity_mismatch')
    require(value.get('status') == 'completed' and value.get('conclusion') == 'success', 'run_not_successful')


def check_uploaded(value, number, sha, run_id, attempt, role, raw):
    require(type(value) is dict and type(value.get('id')) is int and value['id'] == number, 'artifact_identity_mismatch')
    name = f'static-{role}-{sha}-{run_id}-{attempt}'
    require(value.get('name') == name, 'artifact_identity_mismatch')
    checksum = hashlib.sha256(raw).hexdigest()
    require(value.get('digest') == 'sha256:' + checksum, 'archive_digest_mismatch')
    require(value.get('expired') is False, 'artifact_unavailable_or_changed')
    expiry = value.get('expires_at')
    require(type(expiry) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z', expiry),
            'artifact_unavailable_or_changed')
    try:
        expiration = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
    except ValueError:
        raise ArtifactError('artifact_unavailable_or_changed') from None
    require(expiration > datetime.now(timezone.utc), 'artifact_unavailable_or_changed')
    origin = value.get('workflow_run')
    require(type(origin) is dict, 'artifact_identity_mismatch')
    for key, expected in [('id', run_id), ('repository_id', REPOSITORY_ID), ('head_repository_id', REPOSITORY_ID)]:
        require(type(origin.get(key)) is int and origin[key] == expected, 'artifact_identity_mismatch')
    require(origin.get('head_branch') == 'main' and origin.get('head_sha') == sha, 'artifact_identity_mismatch')
    return {'id': number, 'digest_sha256': checksum}


def observe(sha, run_id, attempt, candidate_id, candidate_raw, receipt_id, receipt_raw, environ, fetch):
    main = fetch(BRANCH_ROUTE, environ)
    require(type(main) is dict and main.get('name') == 'main' and main.get('protected') is True, 'unprotected_main')
    require(type(main.get('commit')) is dict and main['commit'].get('sha') == sha, 'stale_main')
    workflow = fetch(WORKFLOW_ROUTE, environ)
    require(type(workflow) is dict and type(workflow.get('id')) is int and workflow['id'] == WORKFLOW_ID
            and workflow.get('path') == WORKFLOW_PATH and workflow.get('state') == 'active', 'workflow_identity_mismatch')
    check_run(fetch(run_route(run_id), environ), sha, run_id, attempt)
    check_run(fetch(run_route(run_id, attempt), environ), sha, run_id, attempt)
    candidate = check_uploaded(fetch(artifact_route(candidate_id), environ), candidate_id, sha, run_id, attempt,
                               'candidate', candidate_raw)
    receipt = check_uploaded(fetch(artifact_route(receipt_id), environ), receipt_id, sha, run_id, attempt,
                             'rehearsal-receipt', receipt_raw)
    return {'candidate': candidate, 'receipt': receipt}


def zip_directory(raw):
    """Bound the central directory before ZipFile can allocate its entry objects.

    Accept ordinary and ZIP64 end records, but no comments or multi-disk ZIPs.
    The payload itself stays subject to stricter file and decompression budgets.
    """
    require(type(raw) is bytes and 22 <= len(raw) <= MAX_ARCHIVE, 'archive_limit')
    end = len(raw) - 22
    footer = struct.unpack_from('<4s4H2LH', raw, end)
    signature, disk, start_disk, disk_count, count, size, offset, comment = footer
    require(signature == b'PK\x05\x06' and disk == start_disk == comment == 0 and disk_count == count, 'invalid_archive')
    directory_end = end
    if end >= 20 and raw[end - 20:end - 16] == b'PK\x06\x07':
        _, locator_disk, position, disks = struct.unpack_from('<4sLQL', raw, end - 20)
        require(locator_disk == 0 and disks == 1 and position + 56 == end - 20, 'invalid_archive')
        require(0 <= position <= end - 76, 'invalid_archive')
        record = struct.unpack_from('<4sQ2H2L4Q', raw, position)
        require(record[0] == b'PK\x06\x06' and record[1] == 44 and record[4] == record[5] == 0
                and record[6] == record[7], 'invalid_archive')
        for old, new, maximum in [(count, record[7], 65535), (size, record[8], 4294967295), (offset, record[9], 4294967295)]:
            require(old == maximum or old == new, 'invalid_archive')
        count, size, offset = record[7], record[8], record[9]
        directory_end = position
    require(0 < count <= tree.MAX_ENTRIES and 0 < size <= MAX_CENTRAL, 'archive_limit')
    require(0 <= offset < directory_end and offset + size == directory_end, 'invalid_archive')
    cursor, observed = offset, 0
    while cursor < directory_end:
        require(cursor + 46 <= directory_end, 'invalid_archive')
        header = struct.unpack_from('<4s6H3L5H2L', raw, cursor)
        require(header[0] == b'PK\x01\x02' and header[13] == 0, 'invalid_archive')
        cursor += 46 + header[10] + header[11] + header[12]
        observed += 1
        require(observed <= count and cursor <= directory_end, 'archive_limit')
    require(cursor == directory_end and observed == count, 'invalid_archive')
    return count, offset


def local_sizes(extra, compressed, uncompressed):
    """Decode only the two local ZIP64 size fields, rejecting ambiguous extras."""
    fields, offset = {}, 0
    while offset < len(extra):
        require(offset + 4 <= len(extra), 'invalid_archive')
        kind, length = struct.unpack_from('<2H', extra, offset)
        offset += 4
        require(kind not in fields and offset + length <= len(extra), 'invalid_archive')
        fields[kind] = extra[offset:offset + length]
        offset += length
    sizes = [uncompressed, compressed]
    wide = sum(value == 4294967295 for value in sizes)
    require((1 in fields) == bool(wide), 'invalid_archive')
    if wide:
        data = fields[1]
        require(len(data) == wide * 8, 'invalid_archive')
        offset = 0
        for index, value in enumerate(sizes):
            if value == 4294967295:
                sizes[index] = struct.unpack_from('<Q', data, offset)[0]
                offset += 8
    return sizes[1], sizes[0]


def local_records(raw, entries, directory_offset):
    """Cover every local record once; no unindexed members or header disagreement.

    A data descriptor may use the bounded ZIP or ZIP64 form, with or without its
    optional signature. Its exact bytes must match the indexed CRC and sizes.
    """
    ordered = sorted(entries, key=lambda entry: entry.header_offset)
    ranges = {}
    require(ordered[0].header_offset == 0, 'invalid_archive')
    for index, info in enumerate(ordered):
        require(0 <= info.file_size <= tree.MAX_FILE_BYTES and 0 <= info.compress_size <= len(raw), 'archive_limit')
        offset = info.header_offset
        next_offset = ordered[index + 1].header_offset if index + 1 < len(ordered) else directory_offset
        require(0 <= offset < offset + 30 <= next_offset <= directory_offset, 'invalid_archive')
        header = struct.unpack_from('<4s5H3L2H', raw, offset)
        require(header[0] == b'PK\x03\x04' and info.reserved == 0 and header[1] == info.extract_version
                and header[2] == info.flag_bits and header[3] == info.compress_type, 'invalid_archive')
        date, time = info.date_time, info.date_time[3:]
        expected_date = ((date[0] - 1980) << 9) | (date[1] << 5) | date[2]
        expected_time = (time[0] << 11) | (time[1] << 5) | (time[2] // 2)
        require(header[4] == expected_time and header[5] == expected_date, 'invalid_archive')
        name_end = offset + 30 + header[9]
        data_start = name_end + header[10]
        data_end = data_start + info.compress_size
        require(offset + 30 <= name_end <= data_start <= data_end <= next_offset, 'invalid_archive')
        encoding = 'utf-8' if info.flag_bits & 0x800 else 'cp437'
        require(raw[offset + 30:name_end] == info.orig_filename.encode(encoding), 'invalid_archive')
        compressed, uncompressed = local_sizes(raw[name_end:data_start], header[7], header[8])
        if info.flag_bits & 0x8:
            require(header[6] in {0, info.CRC} and compressed in {0, info.compress_size}
                    and uncompressed in {0, info.file_size}, 'invalid_archive')
            narrow = struct.pack('<3L', info.CRC, info.compress_size, info.file_size)
            wide = struct.pack('<L2Q', info.CRC, info.compress_size, info.file_size)
            require(raw[data_end:next_offset] in {narrow, b'PK\x07\x08' + narrow, wide, b'PK\x07\x08' + wide},
                    'invalid_archive')
        else:
            require(data_end == next_offset and header[6] == info.CRC and compressed == info.compress_size
                    and uncompressed == info.file_size, 'invalid_archive')
        ranges[info.header_offset] = (data_start, data_end)
    return ranges



def member_bytes(raw, info, bounds, limit):
    """Decode the entire indexed stream; ZipExtFile silently truncates at file_size."""
    start, end = bounds
    require(info.file_size <= limit, 'archive_limit')
    chunks, size, crc = [], 0, 0
    def accept(block):
        nonlocal size, crc
        size += len(block)
        require(size <= info.file_size and size <= limit, 'archive_limit')
        crc = zlib.crc32(block, crc)
        chunks.append(block)
    if info.compress_type == zipfile.ZIP_STORED:
        require(end - start == info.file_size, 'invalid_archive')
        for position in range(start, end, 65536):
            accept(raw[position:min(position + 65536, end)])
    else:
        decoder = zlib.decompressobj(-zlib.MAX_WBITS)
        try:
            for position in range(start, end, 65536):
                pending = raw[position:min(position + 65536, end)]
                while pending:
                    block = decoder.decompress(pending, min(65536, info.file_size + 1 - size))
                    accept(block)
                    require(not decoder.unused_data, 'invalid_archive')
                    pending = decoder.unconsumed_tail
            while not decoder.eof:
                block = decoder.decompress(b'', min(65536, info.file_size + 1 - size))
                accept(block)
                if not block: break
            require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail, 'invalid_archive')
        except zlib.error:
            raise ArtifactError('invalid_archive') from None
    require(size == info.file_size and crc == info.CRC, 'invalid_archive')
    return b''.join(chunks)


def archive_files(raw, role):
    count, directory_offset = zip_directory(raw)
    files, directories, seen = {}, set(), set()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            require(len(entries) == count, 'invalid_archive')
            ranges = local_records(raw, entries, directory_offset)
            for info in entries:
                require(info.filename == info.orig_filename and info.flag_bits & ~(0x800 | 0x8 | 0x6) == 0
                        and info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}, 'invalid_archive')
                is_dir = info.is_dir()
                name = tree.normalized_path(info.filename[:-1] if is_dir else info.filename)
                require(name not in seen and len(name.split('/')) <= tree.MAX_DEPTH + 2, 'archive_layout')
                seen.add(name)
                mode = stat.S_IFMT(info.external_attr >> 16)
                require(mode in {0, stat.S_IFDIR if is_dir else stat.S_IFREG}, 'invalid_archive')
                require(not (info.external_attr & 0x10) or is_dir, 'invalid_archive')
                if role == 'candidate':
                    require(name == 'release.json' and not is_dir or name == 'payload' and is_dir
                            or name.startswith('payload/'), 'archive_layout')
                else:
                    require(name == 'dry-run-plan.json' and not is_dir, 'archive_layout')
                if is_dir:
                    require(info.file_size == 0 and info.CRC == 0, 'invalid_archive')
                    require(member_bytes(raw, info, ranges[info.header_offset], 0) == b'', 'invalid_archive')
                    directories.add(name)
                    continue
                limit = tree.MAX_FILE_BYTES
                if name == 'release.json': limit = 4096
                if name == 'dry-run-plan.json': limit = MAX_RECEIPT
                if name == 'payload/' + tree.MANIFEST: limit = tree.MAX_MANIFEST_BYTES
                require(0 <= info.file_size <= limit and 0 <= info.compress_size <= len(raw), 'archive_limit')
                total += info.file_size
                require(total <= (tree.MAX_TOTAL_BYTES + 4096 if role == 'candidate' else MAX_RECEIPT)
                        and len(files) < tree.MAX_FILES + 1, 'archive_limit')
                files[name] = member_bytes(raw, info, ranges[info.header_offset], limit)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, UnicodeError, EOFError):
        raise ArtifactError('invalid_archive') from None
    parents = {str(parent) for name in files for parent in Path(name).parents if str(parent) != '.'}
    require(directories <= parents and not set(files).intersection(parents), 'archive_layout')
    require('release.json' in files if role == 'candidate' else set(files) == {'dry-run-plan.json'}, 'archive_layout')
    return files


def check_receipt(raw, descriptor_raw, metadata, sha, run_id, attempt, candidate):
    value = rehearsal.decode_object(raw, MAX_RECEIPT)
    expected = {'schema_version': 1, 'kind': 'static-release-rehearsal', 'repository': REPOSITORY,
                'repository_id': REPOSITORY_ID, 'event': 'push', 'ref': 'refs/heads/main', 'commit': sha,
                'workflow_ref': REPOSITORY + '/' + WORKFLOW_PATH + '@refs/heads/main', 'workflow_sha': sha,
                'run_id': run_id, 'run_attempt': attempt, 'observed_main_sha': sha, 'observed_main_protected': True,
                'deployment_authorized': False, 'artifact': {**candidate, 'metadata_verified': True},
                'descriptor': metadata, 'descriptor_sha256': hashlib.sha256(descriptor_raw).hexdigest(),
                'checks': {'artifact_verified': True, 'reproducibility': 'matched', 'transport_roundtrip': True},
                'pending_gates': list(rehearsal.PENDING_GATES)}
    exact(value, expected, 'invalid_receipt')


def verify(sha, run_id, attempt, candidate_id, candidate_path, receipt_id, receipt_path, output, environ, fetch=github_get):
    sha = artifact.commit(sha)
    run_id, attempt, candidate_id, receipt_id = map(rehearsal.decimal, [run_id, attempt, candidate_id, receipt_id])
    require(candidate_id != receipt_id, 'invalid_identity')
    # Read each immutable byte snapshot through a bounded, no-follow actual FD.
    candidate_raw = tree.read_external(candidate_path, MAX_ARCHIVE)
    receipt_raw = tree.read_external(receipt_path, MAX_RECEIPT + MAX_CENTRAL + 1024)
    observed = observe(sha, run_id, attempt, candidate_id, candidate_raw, receipt_id, receipt_raw, environ, fetch)
    candidate_files = archive_files(candidate_raw, 'candidate')
    receipt_files = archive_files(receipt_raw, 'receipt')
    try:
        with tree.private_copy(candidate_files) as snapshot:
            metadata = artifact.verify(snapshot / 'payload', snapshot / 'release.json', sha)
            metadata = {key: metadata[key] for key in artifact.FIELDS}
    except Exception:
        raise ArtifactError('payload_verification_failed') from None
    descriptor_raw = candidate_files['release.json']
    check_receipt(receipt_files['dry-run-plan.json'], descriptor_raw, metadata, sha, run_id, attempt, observed['candidate'])
    repeated = observe(sha, run_id, attempt, candidate_id, candidate_raw, receipt_id, receipt_raw, environ, fetch)
    exact(repeated, observed, 'artifact_identity_mismatch')
    value = {'schema_version': 1, 'kind': 'static-candidate-verification', 'repository': REPOSITORY,
             'repository_id': REPOSITORY_ID, 'workflow_id': WORKFLOW_ID, 'workflow_path': WORKFLOW_PATH,
             'commit': sha, 'run_id': run_id, 'run_attempt': attempt, 'artifacts': observed,
             'descriptor': metadata, 'descriptor_sha256': hashlib.sha256(descriptor_raw).hexdigest(),
             'rehearsal_candidate_verified': True, 'deployment_authorized': False,
             'checks': {'completed_rehearsal': True, 'archive_digests': True, 'receipt_matches': True,
                        'payload_verified': True, 'producer_reproducibility': 'matched',
                        'consumer_rebuild': 'not_performed', 'observed_current_protected_main': True},
             'pending_gates': list(PENDING_GATES)}
    rehearsal.write_receipt(output, value)
    return value


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ArtifactError('invalid_arguments')


def main(argv=None, environ=None, fetch=github_get):
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
    command = commands.add_parser('verify', help='Check two caller-supplied archives and independently read GitHub metadata')
    for flag in ['expected-commit', 'run-id', 'run-attempt', 'candidate-id', 'receipt-id']:
        command.add_argument('--' + flag, required=True)
    for flag in ['candidate-archive', 'receipt-archive', 'out']:
        command.add_argument('--' + flag, type=Path, required=True)
    args = parser.parse_args(argv)
    return verify(args.expected_commit, args.run_id, args.run_attempt, args.candidate_id, args.candidate_archive,
                  args.receipt_id, args.receipt_archive, args.out, os.environ if environ is None else environ, fetch)


def cli(argv=None, environ=None, fetch=github_get):
    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            result = main(argv, environ, fetch)
    except SystemExit as error:
        if error.code == 0:
            print(output.getvalue(), end='')
            return 0
        print(json.dumps({'error': 'invalid_arguments'}), file=sys.stderr)
        return 1
    except ArtifactError as error:
        code = error.code if error.code in ERROR_CODES else 'candidate_verification_failed'
        print(json.dumps({'error': code}), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({'error': 'unsafe_or_unavailable_path'}), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({'error': 'candidate_verification_failed'}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(cli())
