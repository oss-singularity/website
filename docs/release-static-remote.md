# Restricted static filesystem writer

`scripts/static-remote-release.py` is an installed SSH endpoint for one fixed
static destination. It supports preparation, application, status, reconciliation,
conditional rollback and bounded maintenance of its private release material.
It does not enable automatic production deployment.
GitHub provenance, required checks, compatibility and HTTP verification remain
the separate gates in the [release architecture](release-automation.md).

The writer and [offline fixture](release-static-transition.md) use the same
`static_engine.py` journal and `static_posix.py` filesystem primitives. Their
entry points establish their own destination bindings. The fixture API still
exclusively creates its own temporary targets; production paths are never opened
through a fixture handle.

## Independently installed boundary

An operator installs the exact launcher and the eight modules listed in its
`MODULES` constant. Keep that directory outside every public root and outside the
writer's control directory. Files must be single-link, current-owner 0400 regular
files; the containing directory must be 0500 or 0700. Launch with an absolute
Python 3.11+ interpreter and `-I -S`. The launcher loads only these fixed installed
modules, with no payload imports or Python startup customizations. Its reported
bundle digest must be compared with an independently retained digest.

Use a dedicated SSH key with `restrict` and one forced command selecting the
interpreter, launcher and private `--config` file. The sole accepted original
command is exactly `oss-static-release-v1`, with no arguments. Pin the server host
key and prevent fallback to an operator identity or connection. As with the
observer, test a real restricted key before enabling production access.

The binding file uses the [observer configuration](release-static-observer.md)
schema and identities. A sibling private `policy.json` fixes:

| Field | Meaning |
| --- | --- |
| `schema` | Integer `1` |
| `htaccess` | Canonical base64 of the independently reviewed artifact `.htaccess` block |
| `installed_htaccess_sha256` | Hash of the complete installed configuration, including provider additions and the static handler guard |
| `ancestor_htaccess` | Every absolute parent of the target, mapped to its independently reviewed `.htaccess` hash or `null` for verified absence |

For an independently approved server-configuration change, policy schema `2`
replaces the two top-level configuration fields with `htaccess_versions`: a list
of exactly two objects, each containing `htaccess` and
`installed_htaccess_sha256` with the meanings above. Both source blocks and both
complete installed hashes must differ. `ancestor_htaccess` is unchanged. This
privately installed pair permits the reviewed predecessor and candidate; no
request can add a version or change the policy.

The installed hash selects its associated source block, which must occur exactly
once in the complete file. Before staging, replacing that block with the candidate
must produce the candidate's approved complete hash while preserving the actual
prefix and suffix. The existing journal, baseline, generation, descriptors and
conditional rollback remain authoritative. The configuration change is an ordinary
journaled file operation within these independently approved pairs. Do not replace
the live file or reinitialize the journal to bypass predecessor checks. Keep the
old pair until every retained rollback that needs it has been retired; a return
to schema `1` is a separate operator change after that verification. Installing a
changed runtime also requires independently verifying its digest and updating
the client's `STATIC_RUNTIME_SHA256` binding before publication resumes.

Both configuration files must be 0600 inside a separate 0700 directory. Resolve
the actual target through authenticated provider mapping and establish all
public-root boundaries before installation. The endpoint cannot perform that
account inventory or grant its own permission to a selected path.

The operator separately initializes the owner marker, lock, journal and trusted
predecessor descriptor in the private control directory. The schema remains that
of the transition engine: an independently bound initial baseline, creation
metadata, generation, nonce and no current attempt. Store the exact initial
descriptor as `descriptor-<sha256>` with mode 0600. No SSH request can initialize,
rebind or reset this state. The executable test setup demonstrates the layout
using exclusively self-created directories; it is not a production installer.

## Static content and server configuration

The independently installed policy permits the site's static route, data and
asset layouts. It rejects arbitrary extensions, interpreter files, nested server
configuration, ambiguous multi-extension names, hidden paths and other
`.well-known` destinations. The currently supported types are HTML, CSS,
JavaScript, JSON, XML, text, manifests, SVG, WebP, PNG and ICO. New file types
require a reviewed endpoint update. New files use 0644 and directories 0755;
existing managed-file ownership must be representable without changing groups.

Candidate `.htaccess` bytes must exactly match a separately approved block.
The installed complete file must match that block's pinned hash and end with the
exact `STATIC_GUARD` from `static_policy.py`. That guard disables CGI, SSI and content
negotiation, clears inherited extension handlers and filters, assigns explicit
static MIME types and selects `default-handler` for permitted extensions.
[Apache documents the static handler](https://httpd.apache.org/docs/2.4/handler.html)
and the [extension mapping directives](https://httpd.apache.org/docs/2.4/mod/mod_mime.html#removehandler).
Verify its actual behavior on the target server; a successful filesystem test
does not establish compatibility with another server's configuration parser.
Include an independently confirmed inherited SSI handler in that HTTP probe:
every permitted extension must return the exact supplied bytes under the guard,
and a file URL with extra path information must be rejected. Merely disabling
SSI can prevent execution while leaving HTML inaccessible; serving the page
unchanged is part of acceptance too. Remove the owned probes and verify the
original public inventory afterward.

Ancestor configurations are checked on every anchored operation. An unexpected
nested `.htaccess` affecting a managed path blocks preparation or application.
The credential cannot update the guard, the configuration pins or the installed
runtime. Server configuration changes require an independent operator update;
they cannot be smuggled through an otherwise valid artifact.

## Request and recovery contract

Stdin carries one JSON object with integer `schema: 1` and a fixed `operation`.
There are no client-supplied filesystem destinations, commands or repair hooks.
Requests are limited to 12 MiB; decoded candidates to 8 MiB total and 4 MiB per
file, within the stricter applicable artifact bounds. The request deadline is
60 seconds. Unknown fields, duplicate keys and noncanonical base64 fail closed.

`prepare` includes a caller-generated 32-character lowercase hexadecimal
`identity`, `expected_generation`, `expected_predecessor_commit`,
`expected_manifest_sha256`, `candidate_commit`, base64 `candidate_descriptor`,
and `files` mapping safe relative paths to base64 contents. The release client
must validate the candidate and all release-authority gates before sending it.
The server independently validates its static policy, artifact integrity and
the existing baseline; it never trusts the request to redefine that baseline.

The predecessor is reconstructed from the managed installed bytes and the
configuration block selected by the actual installed hash. Its exact stored
descriptor and the journal's independent baseline must still match. Historical
code is not run.
Descriptor objects are retained only after successful plan preparation. They are
bounded by the retained-attempt limit and the maintenance rules below.

The response provides a ticket containing `identity`, `plan_sha256` and the
original `generation`. Retain the original request identity and submitted
descriptor digest before dispatch. After a lost prepare response, `status` with
that identity returns the matching ticket and candidate descriptor digest.
Replaying the same preparation is idempotent and can complete a missing private
descriptor record. A different payload cannot reuse its identity.

`apply`, `reconcile` and `rollback` require that exact ticket. A stale ticket
cannot operate on a later attempt. `status` with `identity: null` returns current
baseline information without an actionable ticket. Never replace a retained
ticket with an unrelated newly observed attempt just to make recovery pass.

An uncertain application must be observed through reconciliation before a
subsequent explicit apply or rollback. Reconciliation writes only private
journal state. The shared engine preserves root metadata, provider additions,
retired assets and unmanaged files, and conditionally restores only its own
verified preimages. It does not atomically switch the whole site or provide a
compare-and-swap against an uncooperative same-user writer.

Preparation reserves its private material directory before creating it. If a
process ends before the first attempt journal, the next fixed operation can
remove that reserved empty directory and close the reservation. Once the attempt
journal exists, it owns the material and normal attempt reconciliation applies.
The target is unchanged by reservation recovery. An unexpected nonempty or
substituted directory requires operator investigation and is preserved. Status
may finish this bounded private reservation recovery; the separate observer
remains the strictly read-only endpoint.

## Bounded retention

`maintain` accepts the exact current ticket, after its phase is `verified`,
`rolled_back` or `aborted`. It revalidates the target and keeps the current
attempt, its material, and its current/predecessor descriptors. In particular,
maintenance after a verified application preserves that attempt's rollback.

Before a new attempt replaces a closed one, the endpoint records that old
material directory's identity and descriptor hashes in a private archive record.
Maintenance deletes only material registered through those records. It does not
discover ownership from a directory name or remove unregistered operator files.
Symlinks, hardlinks, changed identities and unknown material entries stop cleanup.
The archive records are private control metadata, not public release receipts.

Each retired attempt is removed through a bounded, checksummed deletion journal.
It captures exact file hashes, metadata and identities before unlinking. A fresh
process can observe already-removed entries and resume after a lost response.
While a deletion journal is pending, new preparation, application, reconciliation
and rollback are blocked; `status` reports `maintenance_pending` and the same
`maintain` ticket resumes cleanup. This process never writes target files, resets
the active attempt, or rewinds the release generation.

The eight-attempt bound applies to retained material, not the lifetime number of
releases. Call maintenance after each completed publication to keep one current
rollback target and at most its two descriptors. Eight accumulated completed
attempts can be compacted before a ninth preparation. Unregistered material from
an older endpoint is not automatically adopted; an operator must review that
state when upgrading an existing installation.

## Limits and operational status

The endpoint retains the engine's bounded journal and limit of eight uncollected
attempts per independently initialized control state. Explicit maintenance keeps
that storage bounded over repeated releases; exhausting the retained limit blocks
preparation until maintenance succeeds. There is no remote reset or arbitrary
cleanup path. Ambiguous state still requires operator recovery.

Reports contain only bounded hashes, counts, commit identifiers, tickets and
phases. They identify filesystem outcomes with `filesystem_only: true`,
`publication_verified: false` and `deployment_authorized: false`. They contain
no private paths, account identifiers, configuration contents or raw errors.
A reported failure marks the filesystem outcome as unconfirmed. An interruption
may leave no response at all; neither a missing response nor an exit status
proves that no write occurred.

Run `python3 scripts/test-static-remote.py` and its `python3 -O` counterpart.
Tests install the actual isolated command into private temporary directories,
use the current generated artifact, exercise fixed operations and rejection
cases, run twenty consecutive bounded releases, and recover preparation and
maintenance across real process exits. They do not contact a provider or
install production credentials. Provider filesystem, restricted-key and HTTP
handler evidence must be obtained separately before production activation.
