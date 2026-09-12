#!/usr/bin/env python3
"""Plan or publish the exact protected canonical commit through constrained access."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile

import static_publication as publication
from site_artifact import ArtifactError


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ArtifactError('invalid_arguments')


def main(argv=None, environ=None):
    parser = Parser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--mode', choices=['plan', 'publish'], required=True)
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='oss-publication-') as folder:
        return publication.run(args.commit, args.mode, Path(__file__).resolve().parent.parent, Path(folder), environ)


def cli(argv=None):
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
            result = main(argv)
    except SystemExit as error:
        if error.code == 0:
            print(buffer.getvalue(), end='')
            return 0
        result = {'error': 'invalid_arguments', 'publication_verified': False}
    except publication.PublicationFailure as error:
        result = {'error': error.code, 'publication_verified': False, 'failure': error.failure}
        if error.recovery_failure is not None:
            result['recovery_failure'] = error.recovery_failure
    except ArtifactError as error:
        result = {'error': error.code if error.code in {
            'publication_disabled', 'untrusted_workflow', 'untrusted_checkout', 'unfinished_publication',
            'api_compatibility_unverified', 'stale_main', 'publication_rolled_back',
            'publication_requires_reconciliation', 'deployment_record_unconfirmed',
            'http_bytes_mismatch', 'http_mime_mismatch', 'http_cache_mismatch',
            'http_security_mismatch', 'api_unverified', 'http_transport_failed', 'http_compression_missing',
            'cache_transition_failed', 'redirect_mismatch', 'tls_unverified',
            'remote_outcome_unconfirmed', 'missing_credential', 'source_wait_expired', 'edge_policy_mismatch',
            'remote_host_identity_failed', 'remote_authentication_failed', 'remote_connection_failed',
            'provider_configuration_changed', 'github_read_failed', 'ambiguous_workflow_runs',
            'unsuccessful_run', 'artifact_identity_mismatch'} else 'publication_failed',
            'publication_verified': False}
    except Exception:
        result = {'error': 'publication_failed', 'publication_verified': False}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    raise SystemExit(cli())
