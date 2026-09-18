#!/usr/bin/env python3
"""Independently verify an OSS Singularity delivery receipt.

Compares the bytes you retrieve against the digest a contributor declared,
so a reviewer can repeat the check with nothing but this script and Python.
It establishes retrieval integrity only — never quality, authorship, rights,
availability, or acceptance. Byte checks are honest about their limits.

Usage:
  python3 scripts/verify-receipt.py --manifest <url-or-path>
  python3 scripts/verify-receipt.py --artifact <url-or-path> --digest <sha256-hex> [--size N]

Exit codes: 0 verified and current; 1 hard failure (mismatch, size, fetch,
invalid input); 2 bytes match but the revision is superseded or its declared
retention window has ended — history, not a current delivery.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024


def fail(message: str, code: int = 1) -> int:
    print(f"FAIL: {message}")
    return code


def load_bytes(source: str) -> bytes:
    if source.startswith("https://"):
        request = urllib.request.Request(source, headers={"User-Agent": "OSS-Singularity-Receipt-Verify"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError(f"artifact exceeds the {MAX_BYTES} byte verification bound")
        return data
    if "://" in source:
        raise ValueError("only HTTPS URLs or local paths can be verified")
    return Path(source).read_bytes()


def manifest_fields(document: dict) -> dict:
    if document.get("kind") != "oss-delivery-manifest" or document.get("schema_version") != 1:
        raise ValueError("not an oss-delivery-manifest (schema_version 1)")
    artifact = document.get("artifact") or {}
    integrity = artifact.get("integrity") or {}
    if integrity.get("algorithm") != "sha256":
        raise ValueError("the manifest must declare a sha256 integrity digest")
    return {
        "url": artifact.get("url"),
        "digest": integrity.get("digest"),
        "size": artifact.get("size_bytes"),
        "revision": document.get("delivery_revision"),
        "superseded_by": document.get("superseded_by_revision"),
        "retention": document.get("retention") or {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", help="oss-delivery-manifest URL (https) or local JSON path")
    source.add_argument("--artifact", help="artifact URL (https) or local path")
    parser.add_argument("--digest", help="declared lowercase hex sha256 (with --artifact)")
    parser.add_argument("--size", type=int, help="declared byte size (with --artifact, optional)")
    args = parser.parse_args(argv)

    try:
        if args.manifest:
            raw = load_bytes(args.manifest)
            fields = manifest_fields(json.loads(raw.decode("utf-8")))
            artifact_source = fields["url"]
            if isinstance(artifact_source, str) and not artifact_source.startswith("https://") \
                    and not Path(artifact_source).is_absolute() and not str(args.manifest).startswith("https://"):
                fields["url"] = str(Path(args.manifest).resolve().parent / artifact_source)
        else:
            if not args.digest:
                return fail("--digest is required with --artifact")
            fields = {"url": args.artifact, "digest": args.digest, "size": args.size,
                      "revision": None, "superseded_by": None, "retention": {}}
        if not isinstance(fields["digest"], str) or len(fields["digest"]) != 64 \
                or any(char not in "0123456789abcdef" for char in fields["digest"]):
            return fail("the declared digest is not a lowercase hex sha256 value")
        try:
            artifact = load_bytes(fields["url"])
        except Exception as error:  # noqa: BLE001 - report any retrieval failure honestly
            return fail(f"artifact could not be retrieved: {error}")
        actual = hashlib.sha256(artifact).hexdigest()
        if actual != fields["digest"]:
            return fail(f"mismatch: retrieved bytes hash to {actual}, the manifest declares {fields['digest']}")
        if fields["size"] is not None and len(artifact) != fields["size"]:
            return fail(f"size mismatch: retrieved {len(artifact)} bytes, the manifest declares {fields['size']}")
        print(f"verified: sha256 {actual} over {len(artifact)} bytes")
        stale = False
        if fields["revision"] is not None:
            if fields["superseded_by"] is not None:
                print(f"superseded: revision {fields['revision']} was superseded by revision {fields['superseded_by']}; "
                      "matched bytes of a superseded revision are not a current delivery")
                stale = True
            else:
                print(f"current: revision {fields['revision']} names no superseder")
        retention = fields["retention"]
        if retention.get("retained_by"):
            window = "no end date declared"
            ended = False
            until = retention.get("retained_until")
            if isinstance(until, str):
                try:
                    ended = date.fromisoformat(until) < datetime.now(timezone.utc).date()
                    window = f"until {until}" + (" (window ended)" if ended else " (window running)")
                except ValueError:
                    window = f"until {until} (unparsable date)"
            print(f"retention: declared by {retention['retained_by']}, {window}")
            if retention.get("on_unavailable"):
                print(f"if unavailable: {retention['on_unavailable']}")
            if ended:
                stale = True
        return 2 if stale else 0
    except (ValueError, json.JSONDecodeError) as error:
        return fail(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
