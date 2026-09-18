# Independent QA roles and dispute paths — design note

Milestone: Independent QA roles and dispute paths (project: Receipt protocol
follow-through). Companion model: `design/coordination/qa_model.py`; the
walkthrough tests live in `scripts/test-qa-model.py`.

## Principle

The reviewer who checks evidence should not be the party who benefits from
acceptance. Where it matters, quality review is a separate role with its own
decision, its own named failures, and a dispute path that anyone bound by the
agreement can invoke — without ever handing either party the other's
authority.

## Roles

- **contributor** — delivers immutable revisions. Never reviews, never
  accepts, never resolves a dispute it is a party to.
- **coordinator** — scopes the work, requests revisions, accepts exactly the
  newest revision before the deadline, opens disputes. Acceptance binds the
  revision; it never substitutes for quality review.
- **qa reviewer** — a distinct identity that reviews evidence against the
  agreed criteria and records `passed` or `failed` per revision. The one hard
  rule: the qa reviewer must never be the contributor. A qa reviewer may
  coincide with the coordinator only when no third party is available, and
  the model then marks the milestone `degraded` so every reader can see that
  independence was not achieved.

A delivery digest proves bytes. A qa decision records a judgment against
named criteria. Neither proves fairness, and neither is payment authority.

## Acceptance path

    delivered(rev) → qa passed(rev) → accepted(rev)
    delivered(rev) → qa failed(rev) → revision requested → delivered(rev+1) …

- Acceptance requires exactly the newest revision, before the deadline — the
  live protocol's rules are unchanged.
- When quality review is required, acceptance additionally requires that
  revision's qa decision to be `passed`. A failed qa decision is not a
  rejection: it names what is missing, and the next revision restarts review.
- A qa `passed` is a condition, not a command: the coordinator still decides
  whether to accept.

## Dispute path

    any live revision, evidence disputed → dispute opened → upheld | rejected

- Either bound party (contributor or coordinator) may dispute the live
  revision's evidence, naming the reason. A dispute **blocks acceptance**
  while open; it destroys no recorded state.
- The dispute is resolved by the qa reviewer under the agreement's rules —
  **upheld**: the disputed revision can no longer be accepted; the next
  delivery supersedes it. **rejected**: the review path reopens unchanged.
- The dispute window names a deadline and a default: unresolved disputes past
  their window are treated as upheld (never silently accepted). A timeout
  produces a defined outcome; it never mints authority.

## Trust assumptions, honestly

- GitHub account control proves identity, not competence, availability or
  payment authority; a qa badge is only as good as the agreement behind it.
- Sybil reality is accepted: nothing here resists many fake identities.
- Independence is structural, not verified: the model can only enforce
  distinct identities and surface degradation, not judge bias.
- The live Commons worker still runs v1 (coordinator as the only reviewer).
  This note designs the target role model; implementing it in the worker is a
  separate, separately accepted slice.

## First slice boundary

Synthetic local model only: states, guards and named failures exercised by
tests, no chain, no network, no funds. This note designs; it implements
nothing.
