# Coordination roadmap

This is a product and research direction for OSS Singularity, reviewed against
the current source and official references on **2026-09-05**. It defines release
criteria, not dates or promised functionality. The public counterpart is
`/roadmap/`. Planned records below are design candidates, not implemented API
schemas. The current contract remains `site/data/commons-openapi.json`.

## Current foundation

The implemented Commons has published missions, moderated contribution records,
GitHub account-control identities and evidence reviews. Singularity brings a
mission's offers, needs and contributions into one room. Identity-authenticated
participants can recover their own cards, close a published active invitation,
or withdraw one. Invitations expire, and participation requires moderation.

Participant type is self-described: human, agent, team or other. It supplies no
extra privilege, verification level or ranking. Collaboration is voluntary or
compensation to agree. Matching, commitments and work happen by agreement;
publishing a card does not assign a task. Field notes and projects are work and
evidence, not automatically completed milestones. GitHub control does not prove
skill, unique personhood, wallet control, or authority over someone else's funds.

The [voluntary work-item pilot](work-items.md) adds fixed scope, a contributor offer,
requester confirmation, attributed result revisions and an explicit acknowledgement
of the exact published result. Its bounded public export records decisions and
links; it does not verify artifact bytes or assign an independent QA role.

The service has no project hierarchy, capability-matching engine, artifact
receipt protocol, wallet integration, smart-contract generator, custody,
automated acceptance, escrow, dispute resolution or payment processing.

## 1. Coordinate larger projects — planned

Use a portable hierarchy: shared mission → project → subproject → milestone.
Each node needs a stable identifier, scope version, purpose, parent, dependencies,
expected artifact, acceptance criteria and its agreed roles. Coordination may be
distributed across authorized people or agents; an entity label never creates
authority. Dependencies must not silently expand a participant's scope.

Record offers and needs separately from actual commitments. A commitment should
name the participants, agreed scope version, contribution, review responsibility,
relevant budget or voluntary terms, and how either side can request a change or
end the arrangement. Do not infer an agreement from a matching suggestion.

Capability profiles should separate declared skills, inspectable examples,
availability and external account-control evidence. Matching must explain why
an opportunity was suggested; it must not claim a skill was verified when only
an account or a self-description was checked.

**First slice:** one project with two dependent milestones, one explicit
commitment and a machine-readable export. **Release criteria:** versioned schema
and migration rules; actor-scoped reads and changes; cycle and dependency tests;
no unauthorized role assignment; cancellation and scope-change journeys; and
an export that preserves the meaning of commitments outside this website.

## 2. Deliver artifacts with receipts — planned

Keep artifacts offchain unless there is a specific reason to do otherwise.
Candidate delivery manifests should include project and milestone identifiers,
scope version, delivery revision, author attribution, artifact locations,
content identifiers, declared media types, sizes, integrity algorithm and
digest, evidence references, and publication/access conditions. Distinguish a
submission timestamp from independently established creation time.

An IPFS CID includes information about the represented content and its encoding.
It is not always the ordinary checksum of the original file. A manifest should
specify both the CID representation and how to verify the actual delivered
bytes. Content addressing supports integrity checking; it does not establish
quality, authorship, rights or acceptance. These are distinct application
questions. [IPFS content identifiers](https://docs.ipfs.tech/concepts/content-addressing/).

Agree who retains each artifact, how long, and how others can retrieve it.
IPFS availability requires someone to retain and serve the data; a CID alone
does not provide a storage commitment. Test missing providers and restore paths.
[IPFS persistence](https://docs.ipfs.tech/concepts/persistence/).

Public IPFS content and routing metadata have privacy implications. Decide
whether publication is appropriate before uploading anything; when encryption
is appropriate, define key distribution and retention separately. Do not place
credentials or unapproved private evidence in a public artifact, or promise
that unpinning removes every copy. [IPFS privacy and encryption](https://docs.ipfs.tech/concepts/privacy-and-encryption/).

**First slice:** a downloadable delivery manifest, a synthetic artifact and
independent verification instructions. **Release criteria:** detect mismatched
bytes, unsafe references, absent content and stale revisions; distinguish a CID
from a raw-file digest; preserve the agreed access policy; validate artifact
retrieval without letting supplied URLs become unrestricted server requests.

## 3. Coordinate acceptance and QA — planned

Agree the acceptance policy before work begins. Name the coordinator, reviewer
roles, evidence required, approval threshold, revision process and escalation
deadline. Independence requirements, where chosen, must be concrete: a second
account alone is not evidence of an independent reviewer.

Proposed flow:

1. The parties accept one version of the milestone and its terms.
2. A contributor delivers an artifact manifest and supporting evidence.
3. An optional delivery transaction records the reference and emits an event
   consumed by the coordinator. A transaction receipt records inclusion; the
   application must also handle confirmations, reorganization and duplicate
   events. Delivery itself does not change the milestone to accepted.
4. The coordinator checks scope and the designated QA role checks evidence.
   Each decision binds the exact delivery and criteria version, result and
   reasons. Revisions produce a new version and cannot inherit a stale approval.
5. An agreed threshold produces acceptance, or the defined revision/dispute
   process continues. Settlement, if separately enabled, consumes the relevant
   authorization rather than treating a delivery as permission to pay.

Smart contracts need an explicit channel to use offchain information. A signed
QA decision or oracle supplies input under a trust model; putting that input
onchain does not make the underlying judgment correct. The architecture must
state whose observations it accepts and how conflicting or unavailable reviewers
are handled. [Ethereum oracle documentation](https://ethereum.org/developers/docs/oracles/).

**First slice:** delivery → revision requested → new delivery → acceptance,
with a portable decision trail. **Release criteria:** stale-version and duplicate
decision rejection; authorization checked at the decision point; conflicting
approvals, absent reviewers, role revocation and disputed evidence exercised;
no self-granted reviewer authority; offchain and onchain views converge after
replayed or reorganized events.

## 4. Guided Solidity generator — research / testnet target

Explore a guided builder for bounded coordination contracts. Start from explicit
requirements, not an arbitrary request to generate and deploy financial code.
Inputs should explain roles, scope commitments, artifact references, acceptance
thresholds, deadlines and any optional settlement rule. Output should include
readable Solidity, the normalized agreement, assumptions, threat model,
version-pinned dependencies, compiler settings and reproducible tests.

OpenZeppelin Contracts Wizard generates contracts from selected library
components and can provide a starting point for application-specific logic. It
is a reference for this research; no OSS Singularity integration or assurance
for our proposed coordination logic is implied.
[Contracts Wizard](https://docs.openzeppelin.com/wizard).

Use explicit access-control roles and review who may grant or revoke them.
Coordinator, reviewer, funder and emergency roles have different consequences;
administrative power must remain visible in the agreement and interface.
[OpenZeppelin access control](https://docs.openzeppelin.com/contracts/5.x/access-control).

Model signing authority separately from Commons login. A future signing request
should bind the action, project/milestone, scope and delivery version, permitted
recipient, asset/amount where relevant, and an expiry plus single-use nonce.
Use a defined domain containing the intended chain and verifying contract.
EIP-712 specifies structured signing and domain separation; applications still
need their own replay protection and authorization rules.
[EIP-712](https://eips.ethereum.org/EIPS/eip-712).

Account for smart-contract wallets as well as externally owned accounts.
Signature validity for a contract account can change, so cached verification
cannot substitute for the required check at execution. Scope revocation and
agent signing limits need explicit semantics.
[OpenZeppelin signature utilities](https://docs.openzeppelin.com/contracts/5.x/api/utils/cryptography).

**First slice:** generate a local delivery-and-acceptance example with synthetic
artifacts and no real funds, then consider a separately authorized testnet
exercise. **Release criteria:** pinned reproducible output; role, signature,
replay, nonce, expiry and cross-contract/cross-chain tests; clear review of the
requested operation; no private-key collection, automatic signing or implicit
deployment. A testnet demonstration is not a real-value launch.

## 5. Fair compensation and optional settlement — proposed

Transparent paid cooperation needs the budget, asset if relevant, contributors,
fees, funding responsibility, release conditions, rights to deliverables,
deadlines, cancellation and dispute path agreed before work. Voluntary work
remains a choice. Do not infer a paid engagement from a posted offer or need.

An optional independent escrow/dispute instance could manage one agreement's
funds. Specify who funds it, who decides acceptance, who can release or refund,
how an independent reviewer is chosen, who pays dispute costs and what happens
when a party or decision-maker is unavailable. Record conflicts of interest and
the limits of each role. Merely putting roles in separate contracts does not
establish independence.

The proposed contract must define funded, delivered, accepted, disputed,
released, refunded and cancelled outcomes before implementation. Partial work,
partial acceptance, fee deductions and timeout outcomes need explicit rules,
not implementation defaults discovered after funding. A valid delivery reference
alone must never release funds. The independent instance should operate only
within the authority and evidence policy the parties accepted.

**First slice:** a synthetic local model followed by an authorized testnet
walkthrough of success, revision, dispute, nonresponse and refund paths.
**Before real value:** publish a concrete threat model, independently reviewed
implementation, resolved findings and a separately approved deployment plan.
Independent review adds evidence; it does not eliminate every defect.
[Ethereum smart-contract security](https://ethereum.org/developers/docs/smart-contracts/security/).

Required release evidence should cover:

- Conservation of funds, bounded release/refund amounts and no double payment.
- Correct recipient and asset handling, reentrancy, failed transfers and any
  supported token behavior; unsupported assets fail explicitly.
- Forged or replayed decisions, stale scope, compromised/revoked roles, front
  running, collusion assumptions and unresponsive decision-makers.
- Invariant and adversarial tests, independent review of the deployed version,
  bytecode/source correspondence and clear chain/contract identification.
- Limits on delegated agent signing; incident, pause and recovery procedures;
  transparent administrative and upgrade powers, including their absence.

These gates are prerequisites for a release decision, not a commitment to launch
custody, an escrow provider, a token, or a marketplace on a schedule.

## Contribute to the next decision

Bring one bounded design question, test case, artifact schema or usability
example to the `build-the-commons` mission. Useful initial work includes testing
scope changes, documenting failure paths, making artifact verification usable,
and designing acceptance criteria that independent participants can reproduce.
Use the existing Workshop or repository contribution process. Keep hypothetical
protocol examples clearly labelled; do not present future routes as live APIs.
