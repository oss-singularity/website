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
SETTLEMENT_AGREEMENT = REPO / "design" / "solidity-lab" / "settlement-agreement.json"
COMMITTED = REPO / "design" / "solidity-lab" / "generated"


def _load_module(name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, REPO / "design" / "solidity-lab" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PINNED_SOLC = _load_module("pinned_solc")


def generate(out: Path) -> tuple[bytes, bytes]:
    result = subprocess.run([sys.executable, str(LAB), "--agreement", str(AGREEMENT), "--out", str(out)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return (out / "DeliveryAcceptance.sol").read_bytes(), (out / "DeliveryAcceptance.explained.md").read_bytes()


def generate_settlement(out: Path) -> tuple[bytes, bytes]:
    result = subprocess.run([sys.executable, str(LAB), "--agreement", str(SETTLEMENT_AGREEMENT), "--out", str(out)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return (out / "FairSettlement.sol").read_bytes(), (out / "FairSettlement.explained.md").read_bytes()


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
            {**base, "deadline": True},   # bool is not an integer deadline (review A4)
            {**base, "deadline": 2**256},  # beyond the uint256 constant the contract declares
            {**base, "kind": "something-else"},
            {k: v for k, v in base.items() if k != "coordinator"},
            {k: v for k, v in base.items() if k != "title"},
            {**base, "title": ""},
            {**base, "title": "x" * 81},  # length rule
            {**base, "note": "note\nwith a line break"},  # charset rule
            {**base, "contributor": "0x123"},  # malformed address
            {**base, "contributor": "0x" + "g" * 40},  # non-hex characters
            {**base, "contributor": base["coordinator"]},  # roles must be independent
            {**base, "contributor": "0x" + "ab" * 20, "coordinator": "0x" + "aB" * 20},  # case is not a different address
            # a trailing injected function body must be refused before generation (review A4)
            {**base, "contributor": "0x0000000000000000000000000000000000000001; function unexpected() external {}"},
            {**base, "title": "fine title\nfunction unexpected() external {}"},  # comment escape
            {**base, "unexpected": "field"},  # the input schema is closed
        ]
        for case in cases:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "agreement.json"
                path.write_text(json.dumps(case))
                result = subprocess.run([sys.executable, str(LAB), "--agreement", str(path), "--out", folder],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 1, f"must refuse: {case}")

    def test_poisoned_agreements_are_refused_before_anything_is_written(self) -> None:
        base = json.loads(AGREEMENT.read_text())
        poisoned = (
            {"contributor": "0x0000000000000000000000000000000000000001; function unexpected() external {}"},
            {"coordinator": "0x0000000000000000000000000000000000000002 function unexpected() external {}"},
            {"title": "fine title\nfunction unexpected() external {}"},
        )
        for case in poisoned:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "agreement.json"
                path.write_text(json.dumps({**base, **case}))
                result = subprocess.run([sys.executable, str(LAB), "--agreement", str(path), "--out", folder],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 1, f"must refuse before generating: {case}")
                leftovers = sorted(p.name for p in Path(folder).iterdir() if p.name != "agreement.json")
                self.assertEqual(leftovers, [], "refusal must happen before generation writes any file")


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


class SettlementLabTests(unittest.TestCase):
    """The stage 06 settlement generator: same determinism and honesty bars."""

    def test_generation_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first = generate_settlement(Path(one))
            second = generate_settlement(Path(two))
            self.assertEqual(first, second, "same agreement must produce byte-identical output")

    def test_committed_example_reproduces_from_this_pinned_commit(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            fresh = generate_settlement(Path(folder))
        committed_sol = (COMMITTED / "FairSettlement.sol").read_bytes()
        committed_md = (COMMITTED / "FairSettlement.explained.md").read_bytes()
        self.assertEqual(fresh[0], committed_sol, "the committed contract must match a fresh generation")
        self.assertEqual(fresh[1], committed_md, "the committed explanation must match a fresh generation")

    def test_agreement_inputs_appear_and_stay_synthetic(self) -> None:
        agreement = json.loads(SETTLEMENT_AGREEMENT.read_text())
        source = (COMMITTED / "FairSettlement.sol").read_text()
        for field in ("contributor", "coordinator", "holder", "delivery_digest"):
            self.assertIn(agreement[field], source)
        for field in ("review_deadline", "dispute_deadline", "outer_deadline"):
            self.assertIn(str(agreement[field]), source)
        self.assertIn("SYNTHETIC EXAMPLE", source)
        self.assertIn("not payable", source)
        self.assertIn("never", source.lower())
        self.assertEqual(len({agreement["contributor"], agreement["coordinator"], agreement["holder"]}), 3)

    def test_contract_names_every_state_timeout_and_path_of_the_design_note(self) -> None:
        source = (COMMITTED / "FairSettlement.sol").read_text()
        for state in ("none", "funded", "delivered", "accepted", "disputed", "resolved", "released", "refunded"):
            self.assertIn(state, source, f"design-note state {state} must be named")
        for deadline in ("REVIEW_DEADLINE", "DISPUTE_DEADLINE", "OUTER_DEADLINE", "DISPUTE_FALLBACK_RELEASES"):
            self.assertIn(deadline, source, f"design-note policy {deadline} must be named")
        for name in ("recordFunding", "recordDelivery", "accept", "release", "openDispute",
                     "resolveDispute", "executeResolution", "applyDisputeFallback",
                     "refundAfterOuterDeadline", "refundOnCancellation"):
            self.assertIn(f"function {name}(", source, f"design-note path {name} must exist")

    def test_explanation_covers_every_external_function_and_signature(self) -> None:
        source = (COMMITTED / "FairSettlement.sol").read_text()
        explained = (COMMITTED / "FairSettlement.explained.md").read_text()
        import re
        functions = re.findall(r"function (\w+)\(([^)]*)\)", source)
        self.assertEqual(len(functions), 10)
        for name, signature in functions:
            self.assertIn(name, explained, f"explanation must cover {name}")
            self.assertIn(signature.replace(" ", ""), explained.replace(" ", ""),
                          f"explanation must show the exact signature of {name}")
        for failure in re.findall(r"error (\w+)\(", source):
            self.assertIn(failure, explained, f"explanation must name the failure {failure}")

    def test_explanation_never_claims_custody(self) -> None:
        explained = (COMMITTED / "FairSettlement.explained.md").read_text()
        self.assertIn("never holds funds", explained)
        self.assertIn("not payable", explained)

    def test_invalid_agreements_are_refused(self) -> None:
        base = json.loads(SETTLEMENT_AGREEMENT.read_text())
        cases = [
            {**base, "kind": "something-else"},
            {**base, "delivery_digest": "zz" * 32},
            {k: v for k, v in base.items() if k != "holder"},
            {k: v for k, v in base.items() if k != "title"},
            {**base, "holder": "0x123"},
            {**base, "holder": COORDINATOR},
            {**base, "contributor": "0x" + "cd" * 20, "coordinator": "0x" + "CD" * 20},  # same address, different case
            # a trailing injected function body must be refused before generation (review A4)
            {**base, "holder": "0x0000000000000000000000000000000000000003; function unexpected() external {}"},
            {**base, "title": "fine title\nfunction unexpected() external {}"},  # comment escape
            {**base, "note": "note\nwith a line break"},  # charset rule
            {**base, "unexpected": "field"},  # the input schema is closed
            {**base, "review_deadline": base["outer_deadline"]},
            {**base, "dispute_deadline": base["review_deadline"]},
            {**base, "outer_deadline": 0},
            {**base, "review_deadline": 2**256},  # beyond the uint256 constant
            {**base, "dispute_fallback": "keep"},
            {**base, "review_deadline": True},
        ]
        for case in cases:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "settlement-agreement.json"
                path.write_text(json.dumps(case))
                result = subprocess.run([sys.executable, str(LAB), "--agreement", str(path), "--out", folder],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 1, f"must refuse: {case}")


class CompilerTests(unittest.TestCase):
    """The pinned compiler must accept the committed examples; output is reproducible.

    Fail-closed (review A3): tool availability is probed separately, and a
    missing optional local tool may skip — but a compiler that rejects the
    committed source always fails, and in CI even missing tooling fails.
    """

    EXPECTED_ABI_NAMES = {
        "DeliveryAcceptance": ("recordDelivery", "accept", "NotNewestRevision"),
        "FairSettlement": (
            "recordFunding", "recordDelivery", "accept", "release", "openDispute",
            "resolveDispute", "executeResolution", "applyDisputeFallback",
            "refundAfterOuterDeadline", "refundOnCancellation",
            "WrongState", "DisputeWindowStillOpen", "OuterDeadlineNotPassed",
        ),
    }

    @classmethod
    def setUpClass(cls) -> None:
        PINNED_SOLC.enforce_availability()

    def _compile(self, stem: str) -> tuple[bytes, bytes]:
        with tempfile.TemporaryDirectory() as folder:
            return self._compile_path(COMMITTED / f"{stem}.sol", stem, Path(folder))

    def _compile_path(self, source: Path, stem: str, folder: Path) -> tuple[bytes, bytes]:
        try:
            artifacts = PINNED_SOLC.compile_with_artifacts(source, folder, stem)
        except PINNED_SOLC.CompileFailed as error:
            self.fail(str(error))
        return artifacts["bin"], artifacts["abi"]

    def test_compiler_rejection_fails_the_gate_instead_of_skipping(self) -> None:
        # Mutation proof (review A3): a source the compiler rejects must turn
        # this gate red — never a green skip.
        with tempfile.TemporaryDirectory() as folder:
            broken = Path(folder) / "Broken.sol"
            broken.write_text("contract Broken { this is not solidity }\n", encoding="utf-8")
            with self.assertRaises(self.failureException):
                self._compile_path(broken, "Broken", Path(folder))

    def _assert_stable_compile(self, stem: str) -> None:
        import hashlib
        binary, abi = self._compile(stem)
        self.assertGreater(len(binary), 60, "compiled binary is implausibly small")
        for name in self.EXPECTED_ABI_NAMES[stem]:
            self.assertIn(f'"name":"{name}"'.encode(), abi, f"abi must expose {name}")
        again_binary, again_abi = self._compile(stem)
        self.assertEqual(hashlib.sha256(binary).hexdigest(), hashlib.sha256(again_binary).hexdigest(),
                         "same pinned compiler and source must reproduce identical bytecode")
        self.assertEqual(hashlib.sha256(abi).hexdigest(), hashlib.sha256(again_abi).hexdigest(),
                         "same pinned compiler and source must reproduce identical abi")

    def test_pinned_compiler_compiles_delivery_acceptance_with_stable_bytecode(self) -> None:
        self._assert_stable_compile("DeliveryAcceptance")

    def test_pinned_compiler_compiles_fair_settlement_with_stable_bytecode(self) -> None:
        self._assert_stable_compile("FairSettlement")


if __name__ == "__main__":
    unittest.main()
