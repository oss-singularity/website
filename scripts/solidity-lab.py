#!/usr/bin/env python3
"""OSS Singularity contract lab (roadmap stages 05 and 06, first slices).

Turns ONE synthetic milestone agreement into a readable Solidity example plus
a plain-language explanation of every function and requested signature.
Deterministic: the same agreement always produces byte-identical output, so a
pinned commit reproduces the generated files.

Two agreement kinds are supported:

- ``oss-solidity-lab-agreement`` generates the delivery-and-acceptance example
  (roadmap stage 05): a contributor delivers immutable revisions, the
  coordinator requests a revision or accepts exactly the newest one before a
  deadline.
- ``oss-solidity-lab-settlement-agreement`` generates the fair-settlement
  example (roadmap stage 06): an escrow-shaped state machine funded →
  delivered → accepted → released, with dispute and refund paths and defined
  outcomes for unresponsive participants, exactly as the accepted settlement
  design note names them. The contract records states only; it is not an
  escrow, never receives or holds anything, and is deliberately not payable —
  the real budget sits with a separately authorized holder outside this
  contract.

The lab explains signatures; it never requests one from a wallet, never
deploys, and never touches funds. Inputs are synthetic placeholders. Every
agreement passes a closed schema check before generation: role addresses are
validated 20-byte hex strings, roles must be distinct (compared
case-insensitively), deadlines are true integers within the uint256 range,
and title/note accept only printable characters within a length bound — so no
unvalidated string ever reaches the generated Solidity source.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SOLIDITY_VERSION = "^0.8.24"

HEX_DIGITS = set("0123456789abcdef")
DISPUTE_FALLBACKS = ("refund", "release")
UINT256_MAX = 2**256 - 1  # every deadline becomes a uint256 constant in the contract
TITLE_MAX_LENGTH = 80
NOTE_MAX_LENGTH = 600

# Closed input schemas (review A4): exactly these fields, nothing interpolated
# into Solidity that has not passed the checks below.
DELIVERY_FIELDS = ("schema_version", "kind", "title", "note", "contributor",
                   "coordinator", "delivery_digest", "deadline")
SETTLEMENT_FIELDS = ("schema_version", "kind", "title", "note", "contributor", "coordinator",
                     "holder", "delivery_digest", "review_deadline", "dispute_deadline",
                     "outer_deadline", "dispute_fallback")


def _require_closed_document(document: dict, allowed: tuple[str, ...]) -> None:
    unknown = sorted(set(document) - set(allowed))
    if unknown:
        raise ValueError(
            f"unknown agreement field(s) {unknown}; the input schema is closed: {list(allowed)}")


def _require_text(document: dict, field: str, max_length: int) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    if len(value) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters, got {len(value)}")
    for character in value:
        # No control characters and no line breaks of any kind: the title is
        # interpolated into // comment lines of the generated contract.
        if ord(character) < 32 or 127 <= ord(character) <= 159 or ord(character) in (0x2028, 0x2029):
            raise ValueError(f"{field} must contain only printable characters (no control characters or line breaks)")
    return value


def _require_address(document: dict, field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 42:
        raise ValueError(f"{field} must be a 20-byte hex address (0x plus 40 hex characters)")
    if any(c not in "0123456789abcdefABCDEF" for c in value[2:]):
        raise ValueError(f"{field} must be a 20-byte hex address (0x plus 40 hex characters)")
    # The validated spelling is emitted verbatim: the charset check makes it
    # inert as Solidity source, and solc's own EIP-55 literal check rejects a
    # wrong casing at compile time — which now fails the gates instead of
    # skipping them. Computing the canonical checksum here would need Keccak,
    # which the standard library does not ship.
    return value


def _require_deadline(document: dict, field: str) -> int:
    value = document.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer unix timestamp (bool is not a deadline)")
    if not 0 < value <= UINT256_MAX:
        raise ValueError(f"{field} must be a positive timestamp within the uint256 range")
    return value


def _require_digest(document: dict) -> None:
    digest = document.get("delivery_digest")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in HEX_DIGITS for c in digest):
        raise ValueError("delivery_digest must be 64 lowercase hex characters")


def _require_distinct_roles(document: dict, fields: tuple[str, ...]) -> None:
    if len({document[field].lower() for field in fields}) != len(fields):
        raise ValueError(f"{', '.join(fields)} must be distinct addresses (compared case-insensitively)")


def _validate_delivery_agreement(document: dict) -> dict:
    if document.get("kind") != "oss-solidity-lab-agreement" or document.get("schema_version") != 1:
        raise ValueError("not an oss-solidity-lab-agreement (schema_version 1)")
    _require_closed_document(document, DELIVERY_FIELDS)
    for field, limit in (("title", TITLE_MAX_LENGTH), ("note", NOTE_MAX_LENGTH)):
        _require_text(document, field, limit)
    for field in ("contributor", "coordinator"):
        document[field] = _require_address(document, field)
    _require_digest(document)
    _require_deadline(document, "deadline")
    _require_distinct_roles(document, ("contributor", "coordinator"))
    return document


def _validate_settlement_agreement(document: dict) -> dict:
    if document.get("kind") != "oss-solidity-lab-settlement-agreement" or document.get("schema_version") != 1:
        raise ValueError("not an oss-solidity-lab-settlement-agreement (schema_version 1)")
    _require_closed_document(document, SETTLEMENT_FIELDS)
    for field, limit in (("title", TITLE_MAX_LENGTH), ("note", NOTE_MAX_LENGTH)):
        _require_text(document, field, limit)
    for field in ("contributor", "coordinator", "holder"):
        document[field] = _require_address(document, field)
    _require_distinct_roles(document, ("contributor", "coordinator", "holder"))
    _require_digest(document)
    review = _require_deadline(document, "review_deadline")
    dispute = _require_deadline(document, "dispute_deadline")
    outer = _require_deadline(document, "outer_deadline")
    if not review < dispute < outer:
        raise ValueError("deadlines must ascend: review_deadline < dispute_deadline < outer_deadline")
    if document["dispute_fallback"] not in DISPUTE_FALLBACKS:
        raise ValueError(f"dispute_fallback must be one of {DISPUTE_FALLBACKS}")
    return document


def load_agreement(source: str) -> dict:
    document = json.loads(Path(source).read_text(encoding="utf-8"))
    kind = document.get("kind")
    if kind == "oss-solidity-lab-agreement":
        return _validate_delivery_agreement(document)
    if kind == "oss-solidity-lab-settlement-agreement":
        return _validate_settlement_agreement(document)
    raise ValueError(
        "not an oss-solidity-lab-agreement (schema_version 1, "
        "oss-solidity-lab-agreement or oss-solidity-lab-settlement-agreement)"
    )


def contract_source(agreement: dict) -> str:
    return f"""// SPDX-License-Identifier: MIT
// Generated by the OSS Singularity contract lab (roadmap stage 05 first slice).
// SYNTHETIC EXAMPLE — {agreement['title']}. Placeholder addresses, a digest
// over the public synthetic artifact and a future deadline; no funds, no
// deployment, no wallet. The lab explains every requested signature; it never
// requests one. Nothing signs or deploys merely because this file exists.
pragma solidity {SOLIDITY_VERSION};

/// @title DeliveryAcceptance — a readable delivery-and-acceptance example.
/// @notice Mirrors the coordination protocol: a contributor delivers immutable
///         revisions, the coordinator requests a revision or accepts exactly
///         the newest one before a deadline. Acceptance completes the record;
///         nothing here transfers value of any kind.
contract DeliveryAcceptance {{
    // ---- Roles from the agreement (synthetic placeholders) ----
    address public immutable contributor = {agreement['contributor']};
    address public immutable coordinator = {agreement['coordinator']};

    // ---- Delivery reference: sha256 over the delivered bytes ----
    // The manifest digest of the synthetic artifact this example stands for.
    bytes32 public constant DECLARED_DIGEST = 0x{agreement['delivery_digest']};

    // ---- Acceptance policy ----
    uint256 public constant DEADLINE = {agreement['deadline']};

    // ---- Review state: one revision at a time, immutable once recorded ----
    uint16 public currentRevision;          // 0 means nothing delivered yet
    uint16 public acceptedRevision;         // 0 means nothing accepted yet
    string public revisionRequest;          // the coordinator's last note

    event DeliveryRecorded(uint16 indexed revision, bytes32 digest, address indexed who);
    event RevisionRequested(uint16 indexed revision, string note, address indexed who);
    event Accepted(uint16 indexed revision, address indexed who);

    error NotContributor();
    error NotCoordinator();
    error NothingDelivered();
    error NotNewestRevision(uint16 newest);
    error DuplicateRevision(uint16 revision);
    error DeadlinePassed(uint256 now_, uint256 deadline);
    error AlreadyAccepted(uint16 revision);

    modifier onlyContributor() {{
        if (msg.sender != contributor) revert NotContributor();
        _;
    }}
    modifier onlyCoordinator() {{
        if (msg.sender != coordinator) revert NotCoordinator();
        _;
    }}

    /// @notice The contributor records the next immutable delivery revision.
    /// @dev    Signature (uint16): the new revision number, one above the
    ///         current. Reverts for anyone but the contributor, for a repeat
    ///         of the current revision, or after acceptance already happened.
    function recordDelivery(uint16 revision) external onlyContributor {{
        if (acceptedRevision != 0) revert AlreadyAccepted(acceptedRevision);
        if (revision != currentRevision + 1) revert DuplicateRevision(revision);
        currentRevision = revision;
        emit DeliveryRecorded(revision, DECLARED_DIGEST, msg.sender);
    }}

    /// @notice The coordinator names what a revision is missing. No state
    ///         beyond the note changes: a revision request never alters or
    ///         invalidates a recorded delivery.
    /// @dev    Signature (uint16, string calldata): the revision the note
    ///         refers to and the note itself. Reverts for anyone but the
    ///         coordinator or before anything was delivered.
    function requestRevision(uint16 revision, string calldata note) external onlyCoordinator {{
        if (currentRevision == 0) revert NothingDelivered();
        if (revision != currentRevision) revert NotNewestRevision(currentRevision);
        revisionRequest = note;
        emit RevisionRequested(revision, note, msg.sender);
    }}

    /// @notice The coordinator accepts exactly the newest revision before the
    ///         deadline. Acceptance is final: it completes the record and
    ///         locks further deliveries, mirroring the off-chain protocol
    ///         where acceptance binds one exact revision.
    /// @dev    Signature (uint16): the revision being accepted. Reverts for
    ///         non-coordinators, stale revisions, a passed deadline, or when
    ///         acceptance already happened.
    function accept(uint16 revision) external onlyCoordinator {{
        if (acceptedRevision != 0) revert AlreadyAccepted(acceptedRevision);
        if (currentRevision == 0) revert NothingDelivered();
        if (revision != currentRevision) revert NotNewestRevision(currentRevision);
        if (block.timestamp > DEADLINE) revert DeadlinePassed(block.timestamp, DEADLINE);
        acceptedRevision = revision;
        emit Accepted(revision, msg.sender);
    }}
}}
"""


def explanation(agreement: dict) -> str:
    return f"""# DeliveryAcceptance — plain-language explanation

Generated by the OSS Singularity contract lab for: **{agreement['title']}**.
Synthetic example: placeholder addresses, a digest over the public synthetic
artifact, a future deadline. The lab explains signatures; it never requests
one from a wallet, deploys anything, or holds funds.

## What this contract models

The same shape as the coordination protocol you already use off-chain: a
contributor delivers immutable revisions, the coordinator reviews, a revision
request records what is missing, and acceptance binds exactly the newest
revision before a deadline. On-chain deployment is NOT part of this slice.

## Every function and its requested signature

| Function | Signature you would be asked to sign | What it establishes | Who may call |
| --- | --- | --- | --- |
| `recordDelivery(uint16 revision)` | a call naming the next revision number | that the contributor recorded revision N referencing the declared digest | contributor only |
| `requestRevision(uint16 revision, string calldata note)` | a call naming a revision and a note | that the coordinator asked for changes on exactly that revision — no state is destroyed | coordinator only |
| `accept(uint16 revision)` | a call naming the newest revision | final acceptance of exactly that revision, before the deadline | coordinator only |

## What can fail, and how each failure is named

- `NotContributor` / `NotCoordinator` — role checks at the decision point: nobody reviews or delivers for someone else.
- `DuplicateRevision` — revisions only move forward, one at a time.
- `NothingDelivered` — reviews need a delivery first.
- `NotNewestRevision` — stale revisions are refused, not silently superseded (matched history is not a current delivery).
- `DeadlinePassed` — acceptance after the deadline is refused.
- `AlreadyAccepted` — acceptance is final; replay adds nothing.

## Honest boundaries

This contract holds no funds and defines no payment. A digest proves which
bytes were declared, not quality, authorship or availability. Deployment,
testnet exercise, or any wallet interaction requires separate, explicit
authorization — nothing here signs or deploys merely because it was generated.
"""


def settlement_contract_source(agreement: dict) -> str:
    fallback_releases = "true" if agreement["dispute_fallback"] == "release" else "false"
    return f"""// SPDX-License-Identifier: MIT
// Generated by the OSS Singularity contract lab (roadmap stage 06 first slice).
// SYNTHETIC EXAMPLE — {agreement['title']}. Placeholder addresses, a digest
// over the public synthetic artifact and future deadlines; no funds, no
// deployment, no wallet. This contract records settlement states only: it is
// not an escrow, never receives or holds anything, and is deliberately
// not payable. The lab explains every requested signature; it never requests
// one. Nothing signs or deploys merely because this file exists.
pragma solidity {SOLIDITY_VERSION};

/// @title FairSettlement — a readable fair-settlement state machine.
/// @notice Mirrors the accepted settlement design note: the agreement comes
///         before the work and before any funds move. The happy path is
///         funded → delivered → accepted → released; either party may open a
///         dispute (delivered → disputed → resolved → released or refunded),
///         and cancellation plus every timeout end in a DEFINED outcome. The
///         real budget sits with a SEPARATELY AUTHORIZED holder — an escrow
///         instance or named human authority — that reads these records and
///         executes transfers in its own audited system. This contract never
///         holds, receives, or moves value of any kind. The Commons never
///         holds funds and never gains custody.
contract FairSettlement {{
    /// The settlement states, named exactly as the design note names them.
    enum State {{
        none,       // agreement recorded, funding not yet declared
        funded,     // the budget sits with the separately authorized holder
        delivered,  // a delivery receipt links the digest to the work
        accepted,   // the coordinator accepted the newest revision (release CONDITION)
        disputed,   // one party named the dispute path
        resolved,   // the holder recorded the dispute resolution
        released,   // terminal: settled to the contributor
        refunded    // terminal: settled back to the coordinator
    }}

    // ---- Roles from the agreement (synthetic placeholders) ----
    address public immutable contributor = {agreement['contributor']};
    address public immutable coordinator = {agreement['coordinator']};
    // Placeholder for the separately authorized holder of the real budget.
    // This contract records what the holder declares; custody itself is a
    // separate, audited system with its own threat model.
    address public immutable holder = {agreement['holder']};

    // ---- Delivery reference: sha256 over the delivered bytes ----
    // The manifest digest of the synthetic artifact this example stands for.
    bytes32 public constant DECLARED_DIGEST = 0x{agreement['delivery_digest']};

    // ---- Named deadlines: every waiting state has one ----
    uint256 public constant REVIEW_DEADLINE = {agreement['review_deadline']};   // delivery not reviewed → dispute path, never silent release
    uint256 public constant DISPUTE_DEADLINE = {agreement['dispute_deadline']}; // dispute unresolved → the agreement's fallback
    uint256 public constant OUTER_DEADLINE = {agreement['outer_deadline']};     // past it the refund path has EXCLUSIVE priority

    // ---- The fallback outcome the agreement fixed for a deadlocked dispute ----
    bool public constant DISPUTE_FALLBACK_RELEASES = {fallback_releases}; // {agreement['dispute_fallback']}

    // ---- Settlement state: one path at a time, terminal states are final ----
    State public state = State.none;
    uint16 public currentRevision;      // last delivered revision; 0 = none yet
    uint16 public acceptedRevision;     // 0 = nothing accepted yet
    bool public resolutionReleases;     // the holder's recorded dispute decision

    event FundingRecorded(address indexed who);
    event DeliveryRecorded(uint16 indexed revision, bytes32 digest, address indexed who);
    event Accepted(uint16 indexed revision, address indexed who);
    event DisputeOpened(string reason, address indexed who);
    event DisputeResolved(bool releases, address indexed who);
    event Released(address indexed who);
    event Refunded(string reason, address indexed who);

    error NotContributor();
    error NotCoordinator();
    error NotHolder();
    error NotParty(address sender);
    error WrongState(State current);
    error DuplicateRevision(uint16 revision);
    error NotNewestRevision(uint16 newest);
    error DeadlinePassed(uint256 now_, uint256 deadline);
    error DisputeWindowStillOpen(uint256 now_, uint256 deadline);
    error OuterDeadlineNotPassed(uint256 now_, uint256 deadline);
    error OuterDeadlinePassed(uint256 now_, uint256 deadline);

    modifier onlyContributor() {{
        if (msg.sender != contributor) revert NotContributor();
        _;
    }}
    modifier onlyCoordinator() {{
        if (msg.sender != coordinator) revert NotCoordinator();
        _;
    }}
    modifier onlyHolder() {{
        if (msg.sender != holder) revert NotHolder();
        _;
    }}
    modifier onlyParty() {{
        if (msg.sender != contributor && msg.sender != coordinator) revert NotParty(msg.sender);
        _;
    }}

    /// @notice The holder records that the agreed budget sits with the
    ///         separately authorized holder. This declares funding; it never
    ///         receives anything.
    /// @dev    Signature (): no arguments. Reverts for anyone but the holder
    ///         or when funding was already declared.
    function recordFunding() external onlyHolder {{
        if (state != State.none) revert WrongState(state);
        state = State.funded;
        emit FundingRecorded(msg.sender);
    }}

    /// @notice The contributor records the next immutable delivery revision,
    ///         referencing the declared digest — the same delivery shape as
    ///         the coordination protocol.
    /// @dev    Signature (uint16): the new revision number, one above the
    ///         current. Reverts for anyone but the contributor, for a repeat
    ///         or skip, or when the settlement is not waiting for delivery.
    function recordDelivery(uint16 revision) external onlyContributor {{
        if (state != State.funded) revert WrongState(state);
        if (revision != currentRevision + 1) revert DuplicateRevision(revision);
        currentRevision = revision;
        state = State.delivered;
        emit DeliveryRecorded(revision, DECLARED_DIGEST, msg.sender);
    }}

    /// @notice The coordinator accepts exactly the newest revision before the
    ///         review deadline. Acceptance is the release CONDITION, never the
    ///         release itself. After the deadline the dispute path is the only
    ///         way forward — never a silent release.
    /// @dev    Signature (uint16): the revision being accepted. Reverts for
    ///         non-coordinators, stale revisions, a passed review deadline, or
    ///         when the settlement is not in the delivered state.
    function accept(uint16 revision) external onlyCoordinator {{
        if (state != State.delivered) revert WrongState(state);
        if (revision != currentRevision) revert NotNewestRevision(currentRevision);
        if (block.timestamp > REVIEW_DEADLINE) revert DeadlinePassed(block.timestamp, REVIEW_DEADLINE);
        acceptedRevision = revision;
        state = State.accepted;
        emit Accepted(revision, msg.sender);
    }}

    /// @notice The holder executes the release its custody rules require once
    ///         acceptance has satisfied the release condition — strictly
    ///         before the outer deadline. From the moment the refund path
    ///         opens, no new release can succeed: refund has priority. The
    ///         transfer itself happens in the holder's separate, audited
    ///         system; this record only marks the outcome.
    /// @dev    Signature (): no arguments. Reverts for anyone but the holder,
    ///         when the settlement has not been accepted, or once the outer
    ///         deadline has passed (OuterDeadlinePassed — the refund window
    ///         and the release window are disjoint).
    function release() external onlyHolder {{
        if (state != State.accepted) revert WrongState(state);
        if (block.timestamp > OUTER_DEADLINE) revert OuterDeadlinePassed(block.timestamp, OUTER_DEADLINE);
        state = State.released;
        emit Released(msg.sender);
    }}

    /// @notice Either party opens the dispute path named in the agreement,
    ///         stating why. The holder then applies the agreed rules; the
    ///         service records outcomes only.
    /// @dev    Signature (string calldata): the reason the dispute was opened.
    ///         Reverts for anyone but the two parties or when nothing is
    ///         awaiting review.
    function openDispute(string calldata reason) external onlyParty {{
        if (state != State.delivered) revert WrongState(state);
        state = State.disputed;
        emit DisputeOpened(reason, msg.sender);
    }}

    /// @notice The holder records the resolution decided under the agreed
    ///         dispute rules, inside the dispute window. releases=true means
    ///         the work settles as delivered; false means refund.
    /// @dev    Signature (bool): which way the resolution goes. Reverts for
    ///         anyone but the holder, outside the disputed state, or after the
    ///         dispute deadline.
    function resolveDispute(bool releases) external onlyHolder {{
        if (state != State.disputed) revert WrongState(state);
        if (block.timestamp > DISPUTE_DEADLINE) revert DeadlinePassed(block.timestamp, DISPUTE_DEADLINE);
        resolutionReleases = releases;
        state = State.resolved;
        emit DisputeResolved(releases, msg.sender);
    }}

    /// @notice The holder executes the recorded resolution: released or
    ///         refunded, exactly as resolved. A releasing execution is
    ///         refused once the outer deadline has passed — the refund path
    ///         has priority from then on; a refunding execution still
    ///         reaches its (already refund) outcome. The transfer itself
    ///         stays in the holder's custody system.
    /// @dev    Signature (): no arguments. Reverts for anyone but the holder,
    ///         when no resolution has been recorded, or when the resolution
    ///         releases and the outer deadline has passed.
    function executeResolution() external onlyHolder {{
        if (state != State.resolved) revert WrongState(state);
        if (resolutionReleases && block.timestamp > OUTER_DEADLINE) revert OuterDeadlinePassed(block.timestamp, OUTER_DEADLINE);
        if (resolutionReleases) {{
            state = State.released;
            emit Released(msg.sender);
        }} else {{
            state = State.refunded;
            emit Refunded("dispute resolution", msg.sender);
        }}
    }}

    // ---- Timeouts: every waiting state names a deadline and a default ----
    // A timeout produces a DEFINED outcome; it never mints authority. The two
    // paths below are therefore deliberately permissionless: they can only
    // ever reach the outcome the agreement already fixed. Past the outer
    // deadline the refund path has EXCLUSIVE priority: every release-minting
    // transition is refused exactly when the refund becomes available, so a
    // late release can never race the refund for the same state.

    /// @notice Past the dispute window the agreed fallback replaces a
    ///         silent holder: the settlement takes the outcome fixed in the
    ///         agreement ({agreement['dispute_fallback']}) without needing anyone's cooperation —
    ///         unless that outcome is a release and the outer deadline has
    ///         passed: the refund priority outranks even the agreed fallback.
    /// @dev    Signature (): no arguments. Reverts while the settlement is not
    ///         disputed, while the dispute window is still open, or when the
    ///         fallback releases and the outer deadline has passed.
    function applyDisputeFallback() external {{
        if (state != State.disputed) revert WrongState(state);
        if (block.timestamp <= DISPUTE_DEADLINE) revert DisputeWindowStillOpen(block.timestamp, DISPUTE_DEADLINE);
        if (DISPUTE_FALLBACK_RELEASES && block.timestamp > OUTER_DEADLINE) revert OuterDeadlinePassed(block.timestamp, OUTER_DEADLINE);
        if (DISPUTE_FALLBACK_RELEASES) {{
            state = State.released;
            emit Released(msg.sender);
        }} else {{
            state = State.refunded;
            emit Refunded("dispute fallback", msg.sender);
        }}
    }}

    /// @notice Past the outer deadline, funds unreleased take the refund
    ///         path from every non-terminal state: the budget flows back
    ///         rather than sitting withheld indefinitely. From that moment
    ///         on this is the only outcome — every release path is closed.
    /// @dev    Signature (): no arguments. Reverts before the outer deadline
    ///         and on terminal states (nothing left to refund).
    function refundAfterOuterDeadline() external {{
        if (state == State.none || state == State.released || state == State.refunded) revert WrongState(state);
        if (block.timestamp <= OUTER_DEADLINE) revert OuterDeadlineNotPassed(block.timestamp, OUTER_DEADLINE);
        state = State.refunded;
        emit Refunded("outer deadline", msg.sender);
    }}

    /// @notice Cancellation before delivery: the agreement named cancellation,
    ///         and the holder records the budget flowing back unused.
    /// @dev    Signature (): no arguments. Reverts for anyone but the holder
    ///         or when delivery has already begun.
    function refundOnCancellation() external onlyHolder {{
        if (state != State.funded) revert WrongState(state);
        state = State.refunded;
        emit Refunded("cancelled before delivery", msg.sender);
    }}
}}
"""


def settlement_explanation(agreement: dict) -> str:
    return f"""# FairSettlement — plain-language explanation

Generated by the OSS Singularity contract lab for: **{agreement['title']}**.
Synthetic example: placeholder addresses, a digest over the public synthetic
artifact, future deadlines. The lab explains signatures; it never requests
one from a wallet, deploys anything, or holds funds.

## What this contract models

The settlement design note, as a readable state machine. The agreement comes
before the work and before any funds move. The actual budget sits with a
SEPARATELY AUTHORIZED holder — an escrow instance or named human authority —
that reads these records and executes transfers in its own audited system.
This contract records states and who declared them; it never holds, receives,
or moves value of any kind, and it is deliberately not payable. The Commons
never holds funds and never gains custody.

    funded → delivered → accepted → released
    funded → delivered → disputed → resolved → released | refunded
    funded → refunded            (cancellation before delivery)
    any waiting state past its deadline → the outcome the agreement already fixed
    past the outer deadline → refund only; no new release can succeed

## States, named exactly as the design note names them

| State | Meaning |
| --- | --- |
| `none` | the agreement is recorded, funding not yet declared |
| `funded` | the agreed budget sits with the separately authorized holder |
| `delivered` | a delivery receipt links the declared digest to the work |
| `accepted` | the coordinator accepted exactly the newest revision — the release CONDITION, never the release itself |
| `disputed` | one of the two parties named the dispute path and its reason |
| `resolved` | the holder recorded the resolution decided under the agreed dispute rules |
| `released` | terminal: the settlement went to the contributor |
| `refunded` | terminal: the settlement went back to the coordinator |

## Every function and its requested signature

| Function | Signature you would be asked to sign | What it establishes | Who may call |
| --- | --- | --- | --- |
| `recordFunding()` | a call declaring the budget sits with the holder | that funding exists — a declaration about the holder's custody, never a transfer | holder only |
| `recordDelivery(uint16 revision)` | a call naming the next revision number | that the contributor recorded revision N referencing the declared digest | contributor only |
| `accept(uint16 revision)` | a call naming the newest revision | that acceptance binds exactly that revision before the review deadline — the release condition | coordinator only |
| `release()` | a call executing the release | that the holder released the budget after acceptance satisfied its condition — strictly before the outer deadline | holder only |
| `openDispute(string calldata reason)` | a call naming the reason for the dispute | that one party opened the agreed dispute path while the work awaits review | contributor or coordinator only |
| `resolveDispute(bool releases)` | a call declaring which way the resolution goes | that the holder recorded the resolution inside the dispute window | holder only |
| `executeResolution()` | a call executing the recorded resolution | that the holder carried out the resolution: released or refunded, exactly as resolved — a releasing execution only before the outer deadline | holder only |
| `applyDisputeFallback()` | a call taking the agreed fallback | that a dispute unresolved past its window ends in the outcome the agreement fixed ({agreement['dispute_fallback']}) — a releasing fallback only before the outer deadline | anyone, once the dispute window has passed |
| `refundAfterOuterDeadline()` | a call taking the refund path | that funds unreleased past the outer deadline flow back instead of sitting withheld | anyone, once the outer deadline has passed |
| `refundOnCancellation()` | a call recording the cancellation refund | that a cancelled agreement refunds the budget before delivery | holder only |

## Timeouts for unresponsive participants

Every waiting state names a deadline and a defined default outcome:

- delivery not reviewed by `REVIEW_DEADLINE` → the dispute path (`accept` is
  refused; `openDispute` and the outer-deadline refund remain possible) —
  never a silent release
- dispute unresolved by `DISPUTE_DEADLINE` → the agreement's fallback outcome
  (here: **{agreement['dispute_fallback']}**), executable by anyone via
  `applyDisputeFallback`
- funds unreleased by `OUTER_DEADLINE` → the refund path via
  `refundAfterOuterDeadline`, from every non-terminal state

**Refund priority past the outer deadline** — the one precedence rule this
machine fixes: the refund window and every release window are disjoint.
Exactly when `refundAfterOuterDeadline` becomes available
(`block.timestamp > OUTER_DEADLINE`), every release-minting transition is
refused with `OuterDeadlinePassed` — `release`, a releasing
`executeResolution`, and a releasing `applyDisputeFallback` alike. Up to and
including the deadline itself, releases remain possible and the refund is
refused (`OuterDeadlineNotPassed`). A late release can therefore never race
the refund for the same state, whichever transaction arrives first.
Executions that already end in a refund stay allowed after the deadline:
they cannot compete with the refund outcome they share.

A timeout produces a DEFINED outcome; it never mints authority. The timeout
paths are permissionless on purpose: they can only ever reach the outcome the
agreement already fixed, so no caller gains control over the settlement.

## What can fail, and how each failure is named

- `NotContributor` / `NotCoordinator` / `NotHolder` — role checks at the decision point: nobody delivers, reviews, or declares custody for someone else.
- `NotParty` — only the two parties may open a dispute; outsiders cannot trigger it.
- `WrongState` — every transition demands the exact state the design note names; the failure reports the current one.
- `DuplicateRevision` — revisions only move forward, one at a time.
- `NotNewestRevision` — stale revisions are refused, not silently superseded.
- `DeadlinePassed` — acceptance after the review deadline and resolutions after the dispute deadline are refused.
- `DisputeWindowStillOpen` — the agreed fallback exists only for a dispute that outlived its window.
- `OuterDeadlineNotPassed` — the refund path exists only once the outer deadline has actually passed.
- `OuterDeadlinePassed` — every release path closes exactly then: once the refund is available, no new release can succeed.

## Honest boundaries

This contract holds no funds and defines no payment mechanics. A digest
proves which bytes were declared, not quality, authorship or availability.
An acceptance record proves a decision, not fairness. The holder's custody is
a separate, audited system with its own threat model; this design depends on
it and says so. Deployment, testnet exercise, or any wallet interaction
requires separate, explicit authorization — nothing here signs or deploys
merely because it was generated.
"""


def write_generated(out: Path, stem: str, contract: str, explained: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stem}.sol").write_text(contract, encoding="utf-8")
    (out / f"{stem}.explained.md").write_text(explained, encoding="utf-8")
    print(f"generated {stem}.sol and {stem}.explained.md in {out}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agreement", required=True, help="path to an oss-solidity-lab-agreement JSON file")
    parser.add_argument("--out", required=True, help="output directory for the generated pair")
    args = parser.parse_args(argv)
    try:
        agreement = load_agreement(args.agreement)
    except (ValueError, json.JSONDecodeError) as error:
        print(f"invalid agreement: {error}", file=sys.stderr)
        return 1
    if agreement["kind"] == "oss-solidity-lab-settlement-agreement":
        write_generated(
            Path(args.out), "FairSettlement",
            settlement_contract_source(agreement), settlement_explanation(agreement),
        )
    else:
        write_generated(
            Path(args.out), "DeliveryAcceptance",
            contract_source(agreement), explanation(agreement),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
