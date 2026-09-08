"""One SSH identity, pinned host/runtime, fixed command and bounded JSON replies."""
import base64
import ipaddress
import os
from pathlib import Path
import re
import resource
import stat
import subprocess

from release_source import checks, encode, write_private
import static_plan as plan
from site_artifact import ArtifactError, require


# Keep the format marker distinct from a credential block for repository scans.
SSH_KEY_BEGIN = '-----BEGIN ' + 'OPENSSH PRIVATE KEY-----\n'


class RemoteFailure(ArtifactError):
    pass


def failure_code(raw):
    """Report only a fixed category; SSH diagnostics can contain private data.

    A category does not establish the outcome of a mutation. Callers still
    reconcile the original attempt after every RemoteFailure.
    """
    lower = raw.lower()
    categories = {
        'remote_host_identity_failed': (b'host key verification failed', b'remote host identification has changed',
                                        b'no ed25519 host key is known'),
        'remote_authentication_failed': (b'permission denied', b'invalid format', b'error in libcrypto',
                                         b'no supported authentication methods'),
        'remote_connection_failed': (b'connection timed out', b'connection refused', b'network is unreachable',
                                     b'connection reset', b'kex_exchange_identification', b'connection closed'),
    }
    for code, messages in categories.items():
        if any(message in lower for message in messages):
            return code
    return 'remote_outcome_unconfirmed'


def validate_report(value, runtime, identity=None, descriptor=None):
    require(type(value) is dict and value.get('schema_version') == 1
            and type(value['schema_version']) is int and value.get('kind') == 'static-remote-transition'
            and value.get('target') == plan.TARGET and value.get('runtime_sha256') == runtime
            and value.get('filesystem_only') is True and value.get('publication_verified') is False
            and value.get('deployment_authorized') is False, 'remote_identity_mismatch')
    plan.hex_value(value.get('baseline_commit'), 40, 'remote_identity_mismatch')
    plan.hex_value(value.get('baseline_manifest_sha256'), 64, 'remote_identity_mismatch')
    plan.integer(value.get('generation'), 1, 2**63 - 1, 'remote_identity_mismatch')
    require(type(value.get('maintenance_pending')) is bool and value.get('phase') in {
        'empty', 'observed', 'staged', 'prepared', 'applying', 'applied', 'verified',
        'reconciliation_required', 'rollback_prepared', 'rollback_applying', 'rolled_back', 'aborted'}, 'remote_identity_mismatch')
    if value['phase'] != 'empty':
        plan.hex_value(value.get('candidate_commit'), 40, 'remote_identity_mismatch')
        plan.hex_value(value.get('plan_sha256'), 64, 'remote_identity_mismatch')
    if identity is not None:
        ticket = value.get('ticket')
        require(type(ticket) is dict and set(ticket) == {'identity', 'generation', 'plan_sha256'}
                and ticket['identity'] == identity and ticket['plan_sha256'] == value['plan_sha256'],
                'remote_identity_mismatch')
        plan.integer(ticket['generation'], 1, 2**63 - 3, 'remote_identity_mismatch')
        require(value.get('candidate_descriptor_sha256') == descriptor, 'remote_identity_mismatch')
    return value


class SSH:
    def __init__(self, environ, folder, runner=subprocess.run):
        self.folder, self.runner, self.count = folder, runner, 0
        origin = str(ipaddress.ip_address(environ['STATIC_ORIGIN_IP']))
        require(ipaddress.ip_address(origin).is_global, 'invalid_origin')
        user = environ.get('STATIC_SSH_USER', '')
        port = environ.get('STATIC_SSH_PORT', '')
        require(re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', user) and re.fullmatch(r'[1-9][0-9]{0,4}', port)
                and 1 <= int(port) <= 65535, 'invalid_ssh_binding')
        host_key = environ.get('STATIC_SSH_HOST_KEY', '')
        require(re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+={0,2}', host_key), 'invalid_ssh_binding')
        decoded = base64.b64decode(host_key.split()[1], validate=True)
        require(len(decoded) == 51 and decoded.startswith(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00 '), 'invalid_ssh_binding')
        self.runtime = environ.get('STATIC_RUNTIME_SHA256')
        plan.hex_value(self.runtime, 64, 'invalid_ssh_binding')
        key = environ.get('STATIC_SSH_KEY', '')
        require(type(key) is str and 128 < len(key) <= 8192
                and key.startswith(SSH_KEY_BEGIN)
                and key.rstrip().endswith('-----END OPENSSH PRIVATE KEY-----'), 'invalid_ssh_binding')
        # Secret input may omit its last newline; OpenSSH requires it when
        # reading this private-key format from a file.
        write_private(folder / 'identity', (key.rstrip('\r\n') + '\n').encode())
        write_private(folder / 'known_hosts', ('oss-static-origin ' + host_key + '\n').encode())
        self.args = ['ssh', '-F', '/dev/null', '-T', '-i', str(folder / 'identity'), '-p', port, '-l', user]
        options = {'IdentitiesOnly': 'yes', 'IdentityAgent': 'none', 'ControlMaster': 'no', 'ControlPath': 'none',
                   'StrictHostKeyChecking': 'yes', 'UserKnownHostsFile': str(folder / 'known_hosts'),
                   'GlobalKnownHostsFile': '/dev/null', 'HostKeyAlias': 'oss-static-origin',
                   'HostKeyAlgorithms': 'ssh-ed25519', 'UpdateHostKeys': 'no', 'BatchMode': 'yes',
                   'PasswordAuthentication': 'no', 'KbdInteractiveAuthentication': 'no',
                   'ClearAllForwardings': 'yes', 'RequestTTY': 'no', 'ConnectTimeout': '15',
                   'ServerAliveInterval': '10', 'ServerAliveCountMax': '2', 'LogLevel': 'ERROR'}
        for name, value in options.items():
            self.args += ['-o', name + '=' + value]
        self.args += [origin, 'oss-static-release-v1']

    def call(self, request):
        raw = encode(request)
        require(0 < len(raw) <= 12 * 1024 * 1024, 'request_limit')
        self.count += 1
        output = self.folder / ('response-' + str(self.count))
        errors = self.folder / ('errors-' + str(self.count))
        fd = os.open(output, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        error_fd = None
        try:
            error_fd = os.open(errors, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)

            def diagnostic_code():
                info = os.fstat(error_fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16384:
                    return 'remote_outcome_unconfirmed'
                os.lseek(error_fd, 0, os.SEEK_SET)
                return failure_code(os.read(error_fd, 16384))

            try:
                result = self.runner(self.args, input=raw, stdout=fd, stderr=error_fd, check=False,
                                     timeout=80, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                                     preexec_fn=limit_response)
            except Exception:
                raise RemoteFailure(diagnostic_code()) from None
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 16384,
                    'remote_response_invalid')
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                value = checks.decode(os.read(fd, 16385))
            except ArtifactError:
                raise RemoteFailure(diagnostic_code()) from None
            if result.returncode != 0:
                code = value.get('error') if type(value) is dict else None
                raise RemoteFailure('remote_stale_attempt' if code == 'stale_attempt' else diagnostic_code())
            return validate_report(value, self.runtime)
        finally:
            os.close(fd)
            output.unlink()
            if error_fd is not None:
                os.close(error_fd)
                errors.unlink()

    def status(self, identity=None, descriptor=None):
        value = self.call({'schema': 1, 'operation': 'status', 'identity': identity})
        return validate_report(value, self.runtime, identity, descriptor)

    def operation(self, operation, ticket):
        require(operation in {'apply', 'reconcile', 'rollback', 'maintain'}, 'invalid_operation')
        result = self.call({'schema': 1, 'operation': operation, 'ticket': ticket})
        require(result['plan_sha256'] == ticket['plan_sha256'], 'remote_identity_mismatch')
        return result


def limit_response():
    # SSH calls are serialized and run after all HTTP worker threads have joined.
    # The child cannot fill runner storage with an unbounded remote response.
    resource.setrlimit(resource.RLIMIT_FSIZE, (16384, 16384))
