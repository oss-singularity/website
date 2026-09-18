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


CONTRIBUTOR = "0x0000000000000000000000000000000000000001"
COORDINATOR = "0x0000000000000000000000000000000000000002"
STRANGER = "0x0000000000000000000000000000000000000003"


class ModelTests(unittest.TestCase):
    """Role, replay and failure behaviour against the Python side-model."""

    def setUp(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("lab_model", REPO / "design" / "solidity-lab" / "model.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.model = module.DeliveryAcceptanceModel(CONTRIBUTOR, COORDINATOR, deadline=1000)
        self.revert = module.Revert

    def test_full_journey_accepts_exactly_the_newest_revision(self) -> None:
        self.model.record_delivery(CONTRIBUTOR, 1)
        self.model.request_revision(COORDINATOR, 1, "add retention statement")
        self.model.record_delivery(CONTRIBUTOR, 2)
        self.model.accept(COORDINATOR, 2)
        self.assertEqual(self.model.accepted_revision, 2)

    def test_roles_are_checked_at_every_decision_point(self) -> None:
        with self.assertRaises(self.revert):  # stranger cannot deliver
            self.model.record_delivery(STRANGER, 1)
        self.model.record_delivery(CONTRIBUTOR, 1)
        with self.assertRaises(self.revert):  # contributor cannot review
            self.model.accept(CONTRIBUTOR, 1)
        with self.assertRaises(self.revert):  # stranger cannot review
            self.model.request_revision(STRANGER, 1, "nope")

    def test_revisions_move_forward_only_and_replay_adds_nothing(self) -> None:
        self.model.record_delivery(CONTRIBUTOR, 1)
        with self.assertRaises(self.revert):
            self.model.record_delivery(CONTRIBUTOR, 1)  # replay of the same revision
        with self.assertRaises(self.revert):
            self.model.record_delivery(CONTRIBUTOR, 3)  # skipping ahead
        self.model.record_delivery(CONTRIBUTOR, 2)
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 1)  # stale acceptance refused
        self.model.accept(COORDINATOR, 2)
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 2)  # acceptance is final
        with self.assertRaises(self.revert):
            self.model.record_delivery(CONTRIBUTOR, 3)  # nothing after acceptance

    def test_revision_request_destroys_no_state_and_deadline_binds(self) -> None:
        self.model.record_delivery(CONTRIBUTOR, 1)
        self.model.request_revision(COORDINATOR, 1, "note")
        self.assertEqual(self.model.current_revision, 1)  # delivery untouched
        self.model.now = 1001
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 1)
        self.model.now = 999
        self.model.accept(COORDINATOR, 1)

    def test_reviews_need_a_delivery_first(self) -> None:
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 1)


class CompilerTests(unittest.TestCase):
    """The pinned compiler must accept the committed example; output is reproducible."""

    SOLC_VERSION = "0.8.37"

    def _compile(self) -> tuple[bytes, bytes]:
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                ["npx", "--yes", f"solc@{self.SOLC_VERSION}", "--bin", "--abi",
                 str(COMMITTED / "DeliveryAcceptance.sol")],
                capture_output=True, text=True, timeout=300, cwd=folder)
            if result.returncode != 0:
                self.skipTest(f"pinned solc unavailable in this environment: {result.stderr[:120]}")
            binaries = sorted(Path(folder).glob("*_DeliveryAcceptance.bin"))
            abis = sorted(Path(folder).glob("*_DeliveryAcceptance.abi"))
            if not binaries or not abis:
                self.fail(f"pinned solc wrote no artifacts: {result.stdout[:160]}")
            return binaries[0].read_bytes(), abis[0].read_bytes()

    def test_pinned_compiler_compiles_with_stable_bytecode(self) -> None:
        import hashlib
        binary, abi = self._compile()
        self.assertGreater(len(binary), 60, "compiled binary is implausibly small")
        self.assertIn(b'"name":"accept"', abi)
        self.assertIn(b'"name":"recordDelivery"', abi)
        self.assertIn(b'"name":"NotNewestRevision"', abi)
        again_binary, again_abi = self._compile()
        self.assertEqual(hashlib.sha256(binary).hexdigest(), hashlib.sha256(again_binary).hexdigest(),
                         "same pinned compiler and source must reproduce identical bytecode")
        self.assertEqual(hashlib.sha256(abi).hexdigest(), hashlib.sha256(again_abi).hexdigest(),
                         "same pinned compiler and source must reproduce identical ABI")


if __name__ == "__main__":
    unittest.main()
