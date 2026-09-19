# Settlement local-chain exercise — design note

Milestone: local-chain settlement exercise (new follow-through project after
the stage 06 first slice). Companion suite:
`scripts/test-settlement-anvil.py`; agreement:
`design/solidity-lab/settlement-anvil-agreement.json`.

## Principle

Between a synthetic model and a real testnet lies a step we can take today
with zero value at risk: run the **committed, pinned** FairSettlement example
against a **local simulation chain** (Foundry's Anvil). It is a compiler-like
local tool: no external network calls, no faucets, no wallets, nothing of
value. The design-note boundary — no testnet, no real funds, nothing signs
without separate authorization — stays intact: a local chain is not a testnet.

The point of the step: the walkthrough suite has so far proven the state
machine against a Python mirror. This exercise proves the **same paths against
real EVM execution of the exact bytecode** the pinned compiler produces from
the committed generator output — and, because Anvil controls block time, it
drives the timeouts deterministically instead of waiting for wall-clock
deadlines.

## One suite, two backends

The test suite speaks the cast/anvil vocabulary only through a small runner.
Later, a separately authorized testnet exercise should be able to reuse it by
changing configuration only:

| | local chain (this exercise) | testnet (separate gate) |
| --- | --- | --- |
| endpoint | 127.0.0.1, started and OWNED by the suite | public RPC, explicitly chosen and chain-id-checked |
| keys | none in the runner: anvil signs for its unlocked dev accounts | three dedicated burner keystores, testnet funds only |
| time | deterministic block-timestamp control | wall clock, deadlines months out |
| isolation | fresh instance per scenario | fresh burner + fresh agreement instance |
| failure cost | zero | faucet logistics, slower cycles |

The gate for the testnet column is the published
[`pre-launch-threat-model.md`](pre-launch-threat-model.md) plus this rule: a
testnet run with value semantics is a launch rehearsal, never a test — it
needs its own explicit authorization decision, recorded in the coordination
system, before any key touches a public endpoint.

The runner is already backend-configured (this suite, two backends):

    # local chain (default): starts its own anvil on 127.0.0.1:8547
    python3 scripts/test-settlement-anvil.py

    # attached chain (a separately authorized rehearsal):
    # 1. cast wallet new <keystore-dir> contributor   (and coordinator, holder;
    #    created interactively, burner keys, testnet funds only)
    # 2. write an agreement JSON naming those burner addresses for the roles
    #    (copy settlement-anvil-agreement.json, swap the addresses)
    # 3. python3 scripts/test-settlement-anvil.py --attach --rpc <url> \
    #        --chain-id 11155111 --keystore-dir <dir> --password-file <file> \
    #        --agreement rehearsal-agreement.json

An attached run never starts or stops a chain and skips the time-travel
scenarios (wall-clock deadlines apply).

## Runner ownership and signer rules (review A6)

The local default run must prove the port serves its own chain, and no run
may move key material through a command line:

- **Port ownership.** Before the default run starts its anvil, the port must
  answer nothing at all; a foreign responding instance — even one speaking
  RPC — is refused as "not this suite's test server". The run then starts
  its own process (pinned to chain id 31337), waits for readiness, proves
  the process is still alive, and reads the chain id back and compares it.
- **Chain identity.** The expected chain id is always COMPARED, never
  inferred from an exit code. Attached runs must name `--chain-id`
  explicitly; the only allowed targets are a private local instance (31337)
  and the documented Sepolia rehearsal (11155111) — a new target needs an
  explicit design decision, and mainnet is out.
- **No keys in argv.** The local run signs through its own anvil's unlocked
  dev accounts (`cast send --from <address> --unlocked`); the runner holds
  no key bytes at all. Attached runs sign from encrypted keystores
  (`--keystore`/`--password-file`); the legacy raw-key file interface is
  gone. No `--private-key` flag exists anywhere in the runner.
- **Dev accounts stay local.** Anvil's public dev accounts may sign only on
  the chain this run owns. An attached run whose keystores derive those
  addresses is refused — attach support never falls back to well-known
  keys.
- **Agreement binds to signers.** Every role address in the agreement must
  be exactly the address that actually signs for that role
  (case-insensitive), checked before any transaction is sent.
- **Process hygiene.** Only processes this run started are stopped — with
  terminate, wait, and only then kill; the old cleanup left the anvil child
  un-waited (ResourceWarning). The suite's own offline guard tests cover
  the refusal cases (foreign port, wrong or unnamed chain id, dev-key
  rejection, unbound agreements), and a self-hosted attach rehearsal
  exercises the keystore path end to end against a second private instance
  with freshly generated zero-value keystores.

## Deadline priority: refund wins from the outer deadline instant on

The one precedence rule the settlement machine fixes (architecture review
A5): the refund window and every release window are disjoint, and the
deadline instant itself already belongs to the refund. From
`OUTER_DEADLINE` on (inclusive — exactly when the permissionless
`refundAfterOuterDeadline` becomes available,
`block.timestamp >= OUTER_DEADLINE`), every release-minting transition is
refused with `OuterDeadlinePassed` — the plain `release` after acceptance,
a releasing `executeResolution`, and a releasing `applyDisputeFallback`
alike. Before the deadline, releases remain possible and the refund is
refused with `OuterDeadlineNotPassed`. At every timestamp exactly one
direction can act, so a release can never race the refund for the same
state, whichever transaction is broadcast first; the Python model, the
generated contract and the local-chain exercise all assert the same
boundaries (outer−1, outer, outer+1) and both transaction orders.
Executions that already end in a refund (a refunding resolution or a
refunding fallback) stay allowed from the deadline on — they cannot
compete with the refund outcome they share. This contract records states
only and moves no funds; nothing here claims an external transfer could be
reverted — the priority rule governs the record, not any custody.

## What the suite exercises

Deployment of the freshly compiled committed example, then per scenario
(fresh instance each): the happy path `funded → delivered → accepted →
released`; acceptance refused before delivery, before qa-style role mistakes,
and on stale revisions; the review deadline (accept refused after it, dispute
path still open); the dispute fallback (refused inside the window, refund
after it); and the outer-deadline refund — including the exact boundary
blocks outer−1 / outer / outer+1 of the refund-priority rule for the
accepted release, a resolved release and a releasing dispute fallback (a
second generated agreement variant), in both transaction orders, with every
boundary block timestamp read back from the chain. Every revert is asserted
as a named transaction failure (by name where cast decodes it, otherwise by
the error's keccak selector), every state read back as the enum value. A
self-hosted attach rehearsal additionally walks the happy path and the role
guards through the keystore signing backend against a second private anvil.

The suite fails closed on its toolchain: the pinned compiler is probed
separately from compilation, so a compiler or artifact error always fails the
run and can never surface as a green skip. Missing Foundry or an unavailable
compiler skip explicitly (labelled as tool-missing) only outside CI; under CI
both are mandatory gates and missing tooling fails hard.

## Honest limits

- Anvil dev keys are public knowledge; the exercise never touches a key that
  could hold value, on any network, ever.
- A simulation chain cannot prove liveness, gas economics at scale, or
  validator-behavior realities — the threat-model checklist still applies to
  any real deployment.
- Bytecode is produced by the pinned compiler from the committed agreement
  deterministically; the suite asserts it compiles and deploys, it does not
  replace the audit items on the pre-launch checklist.
- This note designs and the suite exercises locally; nothing deploys anywhere
  public and no external network is contacted.
