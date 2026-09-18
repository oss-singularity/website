# Pre-launch threat-model checklist — fair settlement

For the fair-settlement design slice (roadmap stage 06). The design note, the
generated FairSettlement example and the Python walkthrough model are a
**synthetic, local first slice**: they name states, timeouts and honest
limits, and they implement nothing that moves value. This checklist records
what any real-value launch would still need to design, build, audit and
operate **before** a single unit of value moves. It is a checklist, not a
roadmap commitment; no item below is done, and none is authorized yet.

## Custody and the holder

- [ ] A separately authorized holder exists (escrow instance or named human
      authority), and its custody system is **independently audited** — the
      settlement design depends on it and cannot replace it.
- [ ] The holder's key ceremony, quorum and recovery path are documented and
      rehearsed; a lost or compromised holder key is a fund-loss scenario.
- [ ] Conservation of funds is **the holder's invariant to prove**, not ours
      to claim: reconciliation between custody balances and settlement
      records, with evidence a third party can check.
- [ ] The Commons never holds funds and never gains custody — re-verify that
      every integration preserves this boundary end to end.

## Identity and authority

- [ ] GitHub account control proves identity, **not payment authority** —
      define who may trigger or receive a settlement, separate from who can
      sign a coordination action.
- [ ] Payment credentials, coordination tokens and identity tokens live in
      separate trust domains; a leaked identity token must never be able to
      move value.
- [ ] Sybil reality is accepted: nothing in the current design resists many
      fake identities. Decide where that is acceptable (it is, for
      coordination) and where it is not (anywhere near value).

## Delivery and acceptance semantics

- [ ] A delivery digest proves **bytes**, not quality, authorship,
      availability or fitness; define who inspects work and how that
      inspection is recorded.
- [ ] An acceptance record proves a decision, not fairness — define the
      review SLA, reviewer independence and what happens when the reviewer is
      the counterparty.
- [ ] Retention of artifacts is declared, never guaranteed; define what a
      dispute means when the artifact bytes are gone.

## Dispute path

- [ ] The dispute path named in the agreement is a real, reachable process
      with rules, evidence standards, timelines and an authority that applies
      them — the service records outcomes only.
- [ ] The fallback outcome for a deadlocked dispute is chosen deliberately
      (this design defaults to **refund**); confirm the economics make that
      safe for the holder and fair for the contributor.
- [ ] Dispute resolution itself needs a trust model: who resolves, who pays,
      what stops collusion between a party and the resolver.

## Timeouts, on-chain realities

- [ ] Permissionless timeout paths are safe only because they can reach
      exactly the fixed outcome; re-verify this property after any change,
      and model griefing: who benefits from pushing a settlement into a
      timeout.
- [ ] Block timestamps (if any chain is involved) are miner/validator
      influenceable within bounds; deadlines must tolerate that drift, and a
      timeout must never depend on precise time.
- [ ] Someone must call the timeout paths — keeper incentives, gas costs and
      what happens when nobody calls them, need an owner.

## Contract and toolchain

- [ ] Any deployed contract gets a professional security audit of the exact
      bytecode that is deployed, not only of its source.
- [ ] Compiler and dependencies are pinned (this slice pins solc 0.8.37);
      verify deployed bytecode against the pinned build reproducibly.
- [ ] Upgrade and pause policies are named before launch — including who can
      stop settlements and who can never start one.
- [ ] An incident-response runbook exists: pause, communicate, refund,
      post-mortem.

## Legal, compliance and privacy

- [ ] Escrow, payment and refund rules differ by jurisdiction; get competent
      legal review for every jurisdiction value would move through.
- [ ] KYC/AML, sanctions and tax obligations attach to real value and must be
      assigned to an accountable party before launch.
- [ ] Privacy review: settlement records are public in this design; decide
      whether amounts and parties may be public, and honor data-protection
      rights for any personal data.

## Honesty boundaries of this slice

- [ ] The design note, the generated example and this checklist implement
      nothing and move nothing; no on-chain or testnet exercise has occurred
      or is authorized.
- [ ] Any testnet step is a **separately authorized** later gate with its own
      threat-model review — a testnet with value semantics is a launch, not a
      test.
- [ ] Nothing here has been audited, insured, legally reviewed or operated at
      value; treat every document in this slice as a design conversation,
      never as a payment system.
