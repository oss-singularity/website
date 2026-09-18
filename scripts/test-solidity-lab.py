#!/usr/bin/env python3
"""Verify the contract lab generator: deterministic, honest, reproducible."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAB = REPO / "scripts" / "solidity-lab.py"
AGREEMENT = REPO / "design" / "solidity-lab" / "agreement.json"
COMMITTED = REPO / "design" / "solidity-lab" / "generated"


def generate(out: Path) -> tuple[bytes, bytes]:
    result = subprocess.run([sys.executable, str(LAB), "--agreement", str(AGREEMENT), "--out", str(out)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return (out / "DeliveryAcceptance.sol").read_bytes(), (out / "DeliveryAcceptance.explained.md").read_bytes()


class LabTests(unittest.TestCase):
    def test_generation_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first = generate(Path(one))
            second = generate(Path(two))
            self.assertEqual(first, second, "same agreement must produce byte-identical output")

    def test_committed_example_reproduces_from_this_pinned_commit(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            fresh = generate(Path(folder))
        committed_sol = (COMMITTED / "DeliveryAcceptance.sol").read_bytes()
        committed_md = (COMMITTED / "DeliveryAcceptance.explained.md").read_bytes()
        self.assertEqual(fresh[0], committed_sol, "the committed contract must match a fresh generation")
        self.assertEqual(fresh[1], committed_md, "the committed explanation must match a fresh generation")

    def test_agreement_inputs_appear_and_stay_synthetic(self) -> None:
        agreement = json.loads(AGREEMENT.read_text())
        source = (COMMITTED / "DeliveryAcceptance.sol").read_text()
        self.assertIn(agreement["contributor"], source)
        self.assertIn(agreement["coordinator"], source)
        self.assertIn(agreement["delivery_digest"], source)
        self.assertIn(str(agreement["deadline"]), source)
        self.assertIn("SYNTHETIC EXAMPLE", source)
        self.assertIn("never", source.lower())
        self.assertNotEqual(agreement["contributor"], agreement["coordinator"])

    def test_explanation_covers_every_external_function_and_signature(self) -> None:
        source = (COMMITTED / "DeliveryAcceptance.sol").read_text()
        explained = (COMMITTED / "DeliveryAcceptance.explained.md").read_text()
        import re
        functions = re.findall(r"function (\w+)\(([^)]*)\)", source)
        self.assertTrue(functions)
        for name, signature in functions:
            self.assertIn(name, explained, f"explanation must cover {name}")
            self.assertIn(signature.replace(" ", ""), explained.replace(" ", ""),
                          f"explanation must show the exact signature of {name}")
        for failure in re.findall(r"error (\w+)\(", source):
            self.assertIn(failure, explained, f"explanation must name the failure {failure}")

    def test_invalid_agreements_are_refused(self) -> None:
        base = json.loads(AGREEMENT.read_text())
        cases = [
            {**base, "delivery_digest": "zz" * 32},
            {**base, "deadline": -1},
            {**base, "kind": "something-else"},
            {k: v for k, v in base.items() if k != "coordinator"},
        ]
        for case in cases:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "agreement.json"
                path.write_text(json.dumps(case))
                result = subprocess.run([sys.executable, str(LAB), "--agreement", str(path), "--out", folder],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 1, f"must refuse: {case.get('kind')}")


if __name__ == "__main__":
    unittest.main()
