# Fixed-command static observer

`scripts/static-remote-observe.py` reads one independently configured static
installation through a fixed SSH command. It has no upload, shell, repair,
rollback or publication operation. This is the first remote entry point in the
[release architecture](release-automation.md), separate from the exclusively
self-created [transition fixtures](release-static-transition.md).

## Server and credential boundary

An operator installs the reviewed observer and private configuration outside all
public document roots. Use an absolute Python 3.11+ interpreter with `-I -S`;
the entry point refuses a non-isolated interpreter. It uses only the standard
library and never imports repository modules, candidate code or Python startup
customizations. The installed source must remain outside the future release
credential's writable paths.

A dedicated SSH public key must have a fixed `command` and `restrict` in
`authorized_keys`. The command launches that installed interpreter and observer
with one fixed private `--config` path. Quote these operator-selected paths for
both the shell and the authorized-keys format. Do not interpolate client input.
OpenSSH's [`sshd` documentation](https://man.openbsd.org/sshd.8#restrict)
specifies that `restrict` disables PTY allocation, forwarding and user RC
execution; a forced command alone does not establish these restrictions.

The only accepted `SSH_ORIGINAL_COMMAND` is exactly `oss-static-observe-v1`.
Empty shell requests, SFTP/SCP requests, extra arguments, whitespace and shell
operators fail before configuration is opened. Stdin is unused and never parsed
as commands, paths or code. This check complements server-enforced restrictions;
it does not constrain an otherwise unrestricted SSH credential. Pin the server's
independently verified host key on clients and prevent fallback to an operator
key or an existing operator SSH connection.

Before relying on this boundary, test an actual restricted key on the target
provider: the observer succeeds while shell commands, subsystems, PTY and TCP
forwarding requests fail. Merely generating an authorized-keys line or passing
the local unit tests is insufficient. No production key or automatic deployment
workflow is supplied by this repository change.

## Independently installed configuration

The exact JSON keys are `schema` (integer `1`), `target` (`oss-static`), `root`,
`control`, `root_identity`, `control_identity` and `lock_identity`. The two paths
are absolute. Each identity is the device/inode pair observed by the authorized
operator during setup. These are private server values, never artifact inputs
or public evidence. No request can change them.

Resolve the real hosting destination through the provider's authenticated domain
mapping before setup; prove that control, runtime and configuration paths are
outside every public root. The observer does not perform this account inventory
or claim that an operator-selected path proves its domain identity. It rejects
overlap between its target/control paths and configuration inside the target.
Every ancestor is opened without following symlinks.

The configuration is a single-link 0600 regular file in an owner-only 0700
directory. Control is also owner-only 0700. The operator creates its 0600 `lock`
file beforehand and records its identity; requests never create or repair it.
Root and control identities and ownership are rechecked before and after
observation. Replacing a directory or lock requires deliberate operator setup,
not automatic rebinding from a failing request.

The observer takes a nonblocking shared lock. An exclusive holder produces
`target_busy`; multiple readers may coexist. This becomes serialization with a
writer only when that writer uses the same unchanged lock. Existing manual
release tools are not automatically coordinated by installing this observer.

## Observation and evidence

The installed `dist-manifest.sha256` selects at most 256 relative file paths,
each with at most eight components. The manifest is limited to 64 KiB; individual
files to 8 MiB and total captured content to 64 MiB. The CLI has a 20-second
deadline. One leading `./` is accepted, matching `build-site.sh` and the artifact
verifier; duplicate detection runs after this normalization. Absolute paths,
traversal, repeated `./` prefixes, duplicates, malformed lines, symlinked
ancestors, hardlinks and special files are rejected. Unlisted files are not read.
There are no filesystem writes beyond normal OS read-atime behavior.

After reading, the observer rechecks files, parent directories, manifest, root
and lock identities. Observed concurrent changes fail; this is not an atomic
filesystem snapshot or a defense against a hostile process with the same OS
identity. Configuration and the installed manifest are not release authority.

Successful JSON includes the installed manifest hash, a digest of captured
relative file names/content hashes/sizes/metadata, byte and file counts, and the
observer source hash. It exposes no account names, absolute paths, ownership
IDs, file contents or raw errors. Clients must compare the observer hash against
their independently trusted installed version; a reported hash alone is not
attestation. All reports include `deployment_authorized: false`.

`.htaccess` differences are reported separately as `htaccess_differs`, since
provider additions can legitimately surround the tracked block. That flag does
not validate the additions or their preservation. `other_content_differences`
counts mismatches for the remaining declared files. Success means observation
completed, not that deployment, provenance or all content checks passed.

## Validation and next boundary

Run both normal and optimized Python suites from [CONTRIBUTING.md](../CONTRIBUTING.md).
Tests use private synthetic installations, real subprocesses and flock. They
exercise rejected commands, interpreter isolation, configuration/identity errors,
path and file attacks, size limits, concurrent changes and preservation. They
also build the current site and observe its actual generated manifest through
the CLI. They do not install credentials or contact a provider.

The separate [restricted writer](release-static-remote.md) now provides filesystem
operations with an independently installed static-content/configuration policy,
the shared durable journal and an independently trusted baseline. Its lock must
be the same one configured here. Fresh release/check evidence, compatibility and
exact origin/edge/TLS/cache verification remain production promotion gates. The
read-only observer neither satisfies those gates nor grants database access.
