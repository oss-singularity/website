#!/usr/bin/env python3
"""Verify the delivery receipt helper against fixed fixtures."""

import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from urllib import request, response

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


class RedirectBoundaryTests(unittest.TestCase):
    """No network: urllib's real redirect engine with synthetic transports."""

    @staticmethod
    def load_module():
        spec = importlib.util.spec_from_file_location("verify_receipt_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def answer(status: int, location: str | None = None, body: bytes = b"") -> object:
        headers = Message()
        if location:
            headers["Location"] = location
        out = response.addinfourl(io.BytesIO(body), headers, "synthetic", status)
        out.msg = "Found" if status == 302 else "OK"
        return out

    def install(self, module, https_open, http_open) -> list[str]:
        seen: list[str] = []

        class SyntheticHTTPS(request.HTTPSHandler):
            def https_open(self, req):
                seen.append(req.full_url)
                return https_open(req)

        class SyntheticHTTP(request.HTTPHandler):
            def http_open(self, req):
                seen.append(req.full_url)
                return http_open(req)

        module._fetch_opener = request.build_opener(module.HTTPSOnlyRedirectHandler(), SyntheticHTTPS(), SyntheticHTTP())
        return seen

    def run_main(self, module, *arguments: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = module.main(list(arguments))
        return code, buffer.getvalue()

    def test_artifact_downgrade_redirect_is_refused_before_any_fetch(self) -> None:
        module = self.load_module()
        seen = self.install(module,
                            lambda req: self.answer(302, "http://cleartext.invalid/artifact"),
                            lambda req: self.answer(200, body=ARTIFACT))
        code, output = self.run_main(module, "--artifact", "https://trusted.invalid/artifact", "--digest", DIGEST)
        self.assertEqual(code, 1, output)
        self.assertIn("HTTPS", output)
        self.assertIn("refused", output)
        self.assertEqual(seen, ["https://trusted.invalid/artifact"],
                         "the cleartext hop must never receive a request")

    def test_manifest_downgrade_redirect_is_refused_before_any_fetch(self) -> None:
        module = self.load_module()
        seen = self.install(module,
                            lambda req: self.answer(302, "http://cleartext.invalid/manifest.json"),
                            lambda req: self.answer(200, body=b"{}"))
        code, output = self.run_main(module, "--manifest", "https://trusted.invalid/manifest.json")
        self.assertEqual(code, 1, output)
        self.assertIn("HTTPS", output)
        self.assertEqual(seen, ["https://trusted.invalid/manifest.json"],
                         "the cleartext hop must never receive a request")

    def test_https_to_https_redirect_still_verifies(self) -> None:
        module = self.load_module()
        seen = self.install(module,
                            lambda req: self.answer(302, "https://mirror.invalid/artifact")
                            if req.full_url == "https://trusted.invalid/artifact" else self.answer(200, body=ARTIFACT),
                            lambda req: self.answer(200, body=ARTIFACT))
        code, output = self.run_main(module, "--artifact", "https://trusted.invalid/artifact", "--digest", DIGEST)
        self.assertEqual(code, 0, output)
        self.assertIn("verified", output)
        self.assertEqual(seen, ["https://trusted.invalid/artifact", "https://mirror.invalid/artifact"])

    def test_second_hop_downgrade_is_refused_after_a_safe_first_hop(self) -> None:
        module = self.load_module()
        seen = self.install(module,
                            lambda req: self.answer(302, "https://mirror.invalid/artifact")
                            if req.full_url == "https://trusted.invalid/artifact" else self.answer(302, "http://cleartext.invalid/artifact"),
                            lambda req: self.answer(200, body=ARTIFACT))
        code, output = self.run_main(module, "--artifact", "https://trusted.invalid/artifact", "--digest", DIGEST)
        self.assertEqual(code, 1, output)
        self.assertIn("HTTPS", output)
        self.assertEqual(seen, ["https://trusted.invalid/artifact", "https://mirror.invalid/artifact"],
                         "every hop is checked before it is fetched")


if __name__ == "__main__":
    unittest.main()
