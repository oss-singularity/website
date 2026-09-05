# Ideas and their next useful step

This register keeps promising directions findable without turning every idea
into a promised feature. The [public roadmap](https://oss-singularity.io/roadmap/)
shows the main stages; linked contracts record decisions and release criteria.
An implementation is marked available only after its verification and release.

| Idea | Why it matters | State and next step |
| --- | --- | --- |
| Explicit work, from offer to evidence | Different agents and people need the same scope, roles and result reference to cooperate reliably. | **Pilot in development.** [Voluntary work items](work-items.md) define a bounded two-participant journey; finish API, browser and migration checks before release. |
| Continuity across independent agents | A collaborator should recover the same work after a restart, lost response or change of tool. | **Part of the pilot.** Stable IDs, versioned reads, scoped identity, exact replay and portable public exports. Next evidence: a complete independently operated client journey. |
| Projects composed of smaller commitments | Large cooperation needs dependencies and scope changes that remain understandable. | **Planned.** [Project coordination](coordination-roadmap.md#1-coordinate-larger-projects--planned) requires hierarchy, cycle checks and explicit authority. The voluntary pilot does not complete that stage. |
| Inspectable artifacts and decisions | A result's location, integrity, availability and acceptance answer different questions. | **Planned.** [Delivery receipts](coordination-roadmap.md#2-deliver-artifacts-with-receipts--planned) and [acceptance](coordination-roadmap.md#3-coordinate-acceptance-and-qa--planned) define the next experiments. |
| Visible operating costs and shared value | Participants should be able to understand revenue sources, agreed costs and distribution rules. | **Research.** See the historical architecture reference below and [optional settlement](coordination-roadmap.md#5-fair-compensation-and-optional-settlement--proposed). First use synthetic flows and explicit trust assumptions. |
| Reputation grounded in inspectable work | Useful evidence should help assess contributions while making account-control and independence limits visible. | **Foundation exists; broader design open.** [Evidence reviews](../services/commons/README.md) attribute a review to an account, not a unique or independent person. Future signals need resistance to collusion, stale evidence and self-awarded authority. |
| An easy home for contributors | Small improvements should be possible without private infrastructure access. | **Available.** [Contributor guide](../CONTRIBUTING.md), [local security testing](security-testing.md), reproducible checks and [bounded help requests](https://oss-singularity.io/help/) provide concrete starting points. |

## Historical architecture reference: Smart Mining

The [archived Smart Mining site from 22 November 2018](https://web.archive.org/web/20181122033538/https://smart-mining.io/)
and its public [Ethereum contracts](https://github.com/smart-mining/ethereum-contracts)
preserve an earlier attempt to make pool proceeds, operating costs and
token-proportional distribution inspectable. The
[mining contract](https://github.com/smart-mining/ethereum-contracts/blob/master/SmartMining_Mining.sol)
uses an oracle exchange-rate query for a declared EUR operating-cost amount,
sends the calculated cost portion to an operator address and forwards the
remainder to a distribution contract. Actual exchange conversion is described
there as the operator address's responsibility.

This is a reference for architectural questions, not evidence of production
operation or security, and not code to deploy unchanged. A current design needs
to state who supplies a cost, how it is authorized, what an oracle proves, which
offchain actions remain, how recipients inspect the result and how disputes or
unavailable actors are handled. No claim of historical or current market
uniqueness follows from that source.

## Keeping the register useful

Give a new idea a concrete intended benefit, its current state, an unresolved
question and a next observable experiment. Link the relevant contract, issue or
reviewed implementation when one exists. Move settled details into that contract
and leave the reference here; do not duplicate competing specifications.

Keep private conversations, personal finances, account credentials and operator
evidence out of this public register. A recorded idea is not permission to spend,
publish, contact another person, execute contributed code or change infrastructure.
