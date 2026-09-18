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
| endpoint | 127.0.0.1, started by the suite | public RPC, explicitly chosen |
| keys | world-famous Anvil dev keys, no value | one dedicated burner key, testnet funds only |
| time | `anvil_setNextBlockTimestamp` — deterministic | wall clock, deadlines months out |
| isolation | snapshot/revert per scenario | fresh burner + fresh agreement instance |
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
    # 1. cast wallet new            -> burner key, funds it from the faucet
    # 2. write an agreement JSON naming that burner address for its role
    #    (copy settlement-anvil-agreement.json, swap the addresses)
    # 3. write three keys to a file: contributor, coordinator, holder (0x…, one per line)
    # 4. python3 scripts/test-settlement-anvil.py --attach --rpc <url> \
    #        --key-file keys.txt --agreement rehearsal-agreement.json

An attached run never starts or stops a chain, skips the time-travel
scenarios (wall-clock deadlines apply), and never reads keys from
command-line arguments — only from the key file.

## What the suite exercises

Deployment of the freshly compiled committed example, then per scenario
(snapshot-isolated): the happy path `funded → delivered → accepted →
released`; acceptance refused before delivery, before qa-style role mistakes,
and on stale revisions; the review deadline (accept refused after it, dispute
path still open); the dispute fallback (refused inside the window, refund
after it); and the outer-deadline refund. Every revert is asserted as a named
transaction failure, every state read back from the chain as the enum value.

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
