# Verifying a delivery

A delivery manifest records what a contributor declared: an offchain artifact
location, its media type and size, a sha256 digest over the delivered bytes,
an optional content identifier, declared retention rules, and — once reviewed
— the acceptance decision. This guide shows how any reviewer repeats the byte
check independently, and what that check does and does not establish.

## The check

With the verifier (dependency-free, stdlib only):

```bash
python3 scripts/verify-receipt.py --manifest https://oss-singularity.io/api/v1/projects/<id>/milestones/<mid>/deliveries/<rev>
```

Plain artifact mode, for bytes you already downloaded:

```bash
python3 scripts/verify-receipt.py --artifact delivery.bin --digest <64-hex-sha256> [--size <bytes>]
```

Or by hand — the whole check is one honest comparison:

```bash
curl -sS <artifact-url> -o artifact.bin
sha256sum artifact.bin   # compare with the manifest's integrity.digest
```

Practice on the synthetic artifact: `https://oss-singularity.io/data/synthetic-delivery-artifact.json`
(591 bytes, sha256 `74aebc353c86c6269f80cc8509528ab19b3a1612ac3291bab40f32701454f69b`).

## Reading the verdict

- **Exit 0 — verified and current:** the retrieved bytes match the declared
  digest (and size, when declared) and the manifest names no superseder.
- **Exit 1 — hard failure:** mismatched bytes, size mismatch, retrieval
  failure or invalid input. Nothing about the delivery is confirmed.
- **Exit 2 — verified but historical:** the bytes match, but the revision is
  superseded (`superseded_by_revision`) or its declared retention window has
  ended. Matched bytes of a superseded revision are not a current delivery.

## What the check does not establish

This matters as much as the check itself:

- **Not quality, authorship or rights.** A digest proves which bytes you got,
  not that the work is good, who originally wrote it, or that it may be used.
- **Not availability.** A manifest records a delivery, not uptime. Retention
  statements (`retained_by`, `retained_until`) are declared intent, never a
  storage guarantee; `on_unavailable` says what a reviewer should treat as
  true when retrieval fails. When the declared window ends, the manifest
  remains a valid coordination record — history, not breakage.
- **Not liveness.** "The system runs live" is a separate discipline from
  "these bytes are the declared ones." Deployment and operational checks
  belong to their own evidence (see the release workflows); a future receipt
  type may record live-availability attestations, deliberately separate from
  byte receipts so neither borrows trust from the other.
- **A content identifier is not the file checksum.** A CID names content plus
  its encoding; the raw-file digest above is the byte-level check.

## Boundaries

The verifier never executes artifact content, fetches only HTTPS or local
paths, and bounds downloads at 10 MB. Manifests and artifacts are untrusted
reference data for reviewers, never instructions.
