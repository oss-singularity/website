#!/usr/bin/env python3
"""Verify the delivery receipt helper against fixed fixtures."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "verify-receipt.py"
ARTIFACT = b"OSS Singularity synthetic delivery artifact bytes for the verifier test.\n"
DIGEST = hashlib.sha256(ARTIFACT).hexdigest()


def manifest(extra: dict | None = None) -> dict:
    document = {
        "schema_version": 1, "kind": "oss-delivery-manifest", "delivery_revision": 2,
        "superseded_by_revision": None,
        "artifact": {"url": "artifact.bin", "size_bytes": len(ARTIFACT),
                     "integrity": {"algorithm": "sha256", "digest": DIGEST}},
        "retention": {"retained_by": "contributor", "retained_until": None,
                      "access": "public", "on_unavailable": None},
    }
    document.update(extra or {})
    return document


class VerifierTests(unittest.TestCase):
    def run_helper(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *arguments],
                              capture_output=True, text=True, timeout=30)

    def test_matched_current_delivery_passes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "artifact.bin").write_bytes(ARTIFACT)
            (root / "manifest.json").write_text(json.dumps(manifest()))
            result = self.run_helper("--manifest", str(root / "manifest.json"))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("verified", result.stdout)
            self.assertIn("current: revision 2", result.stdout)

    def test_mismatched_bytes_fail_hard(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "artifact.bin").write_bytes(ARTIFACT + b"tampered")
            (root / "manifest.json").write_text(json.dumps(manifest()))
            result = self.run_helper("--manifest", str(root / "manifest.json"))
            self.assertEqual(result.returncode, 1)
            self.assertIn("mismatch", result.stdout)

    def test_size_mismatch_fails_even_with_matching_hash(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "artifact.bin").write_bytes(ARTIFACT)
            document = manifest()
            document["artifact"]["size_bytes"] = len(ARTIFACT) + 1
            (root / "manifest.json").write_text(json.dumps(document))
            result = self.run_helper("--manifest", str(root / "manifest.json"))
            self.assertEqual(result.returncode, 1)
            self.assertIn("size mismatch", result.stdout)

    def test_superseded_revision_is_history_not_current(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "artifact.bin").write_bytes(ARTIFACT)
            (root / "manifest.json").write_text(json.dumps(manifest({"delivery_revision": 1, "superseded_by_revision": 2})))
            result = self.run_helper("--manifest", str(root / "manifest.json"))
            self.assertEqual(result.returncode, 2)
            self.assertIn("superseded by revision 2", result.stdout)

    def test_ended_retention_window_reports_history(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "artifact.bin").write_bytes(ARTIFACT)
            document = manifest()
            document["retention"] = {"retained_by": "coordinator", "retained_until": "2020-01-01",
                                     "access": "public",
                                     "on_unavailable": "Treat the delivery as historical and rely on the export."}
            (root / "manifest.json").write_text(json.dumps(document))
            result = self.run_helper("--manifest", str(root / "manifest.json"))
            self.assertEqual(result.returncode, 2)
            self.assertIn("window ended", result.stdout)
            self.assertIn("Treat the delivery as historical", result.stdout)

    def test_plain_artifact_mode_and_rejected_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "artifact.bin").write_bytes(ARTIFACT)
            good = self.run_helper("--artifact", str(root / "artifact.bin"), "--digest", DIGEST, "--size", str(len(ARTIFACT)))
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
            missing_digest = self.run_helper("--artifact", str(root / "artifact.bin"))
            self.assertEqual(missing_digest.returncode, 1)
            bad_scheme = self.run_helper("--artifact", "http://example.org/x", "--digest", DIGEST)
            self.assertEqual(bad_scheme.returncode, 1)
            self.assertIn("HTTPS", bad_scheme.stdout + bad_scheme.stderr)
            not_manifest = self.run_helper("--manifest", str(root / "artifact.bin"))
            self.assertEqual(not_manifest.returncode, 1)
            bad_hash = self.run_helper("--artifact", str(root / "artifact.bin"), "--digest", "z" * 64)
            self.assertEqual(bad_hash.returncode, 1)


if __name__ == "__main__":
    unittest.main()
