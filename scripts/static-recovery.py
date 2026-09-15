#!/usr/bin/env python3
"""Reconcile and close one blocked static publication record."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile

import static_recovery as recovery
from site_artifact import ArtifactError


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ArtifactError('invalid_arguments')


def main(argv=None, environ=None):
    parser = Parser(description=__doc__)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='oss-recovery-') as folder:
        return recovery.run(args.commit, Path(folder), environ)


def cli(argv=None):
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
            result = main(argv)
    except SystemExit as error:
        if error.code == 0:
            print(buffer.getvalue(), end='')
            return 0
        result = {'error': 'invalid_arguments', 'recovery_verified': False}
    except ArtifactError as error:
        result = {'error': error.code if error.code in {
            'no_unresolved_publication', 'invalid_recovery_record', 'runtime_changed',
            'untrusted_workflow', 'rollback_unconfirmed', 'maintenance_unconfirmed',
            'unfinished_publication', 'deployment_record_unconfirmed', 'deployment_history_unverified',
            'remote_stale_attempt', 'remote_outcome_unconfirmed', 'remote_host_identity_failed',
            'remote_authentication_failed', 'remote_connection_failed', 'remote_identity_mismatch',
            'filesystem_promotion_unconfirmed', 'baseline_mismatch', 'stale_attempt',
            'generation_conflict', 'maintenance_required', 'maintenance_conflict', 'target_busy',
            'provider_configuration_changed', 'provider_request_failed', 'cache_purge_unconfirmed',
            'edge_policy_mismatch', 'security_text_mismatch', 'edge_unverified',
            'http_bytes_mismatch', 'http_mime_mismatch', 'http_cache_mismatch', 'http_security_mismatch',
            'http_compression_missing', 'http_transport_failed', 'tls_unverified', 'api_unverified',
            'unexpected_http_cookie', 'cache_transition_failed', 'redirect_mismatch',
            'missing_credential', 'github_read_failed', 'artifact_transport_failed',
            'ambiguous_rehearsal', 'ambiguous_artifacts', 'ambiguous_workflow_runs',
            'unsuccessful_run', 'artifact_identity_mismatch', 'descriptor_mismatch',
            'invalid_http_target', 'untrusted_checkout'} else 'recovery_failed',
            'recovery_verified': False}
    except Exception:
        result = {'error': 'recovery_failed', 'recovery_verified': False}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    raise SystemExit(cli())
