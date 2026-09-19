#!/usr/bin/env python3
"""Settlement walkthrough (stage 06 M3): every outcome, including unresponsive
participants, exercised against the Python side-model of FairSettlement.sol.

The walkthroughs prove the accepted design note's promise: every release and
refund path is testable as written, every waiting state has a deadline with a
defined outcome, and a timeout never mints authority. The pinned-toolchain
compile check keeps the committed example honest under solc 0.8.37.
Synthetic walkthrough only; no chain, no network, no funds.
"""

import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAB = REPO / "design" / "solidity-lab"
CONTRACT = LAB / "generated" / "FairSettlement.sol"
MODEL = LAB / "fair_model.py"


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, LAB / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PINNED_SOLC = _load_module("pinned_solc")

CONTRIBUTOR = "0x0000000000000000000000000000000000000001"
COORDINATOR = "0x0000000000000000000000000000000000000002"
HOLDER = "0x0000000000000000000000000000000000000003"
STRANGER = "0x0000000000000000000000000000000000000004"
REVIEW_DEADLINE = 1000
DISPUTE_DEADLINE = 2000
OUTER_DEADLINE = 3000


def _load_model(releases_fallback: bool = False):
    spec = importlib.util.spec_from_file_location("fair_model", MODEL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    model = module.FairSettlementModel(
        CONTRIBUTOR, COORDINATOR, HOLDER,
        REVIEW_DEADLINE, DISPUTE_DEADLINE, OUTER_DEADLINE,
        dispute_fallback_releases=releases_fallback,
    )
    return model, module.Revert, module.State


def deliver_and_accept(model) -> None:
    model.record_funding(HOLDER)
    model.record_delivery(CONTRIBUTOR, 1)
    model.accept(COORDINATOR, 1)


def deliver_and_dispute(model, reason: str = "review never arrived") -> None:
    model.record_funding(HOLDER)
    model.record_delivery(CONTRIBUTOR, 1)
    model.open_dispute(COORDINATOR, reason)


class WalkthroughTests(unittest.TestCase):
    """Every terminal outcome of the design note, walked end to end."""

    def setUp(self) -> None:
        self.model, self.revert, self.state = _load_model()

    def test_happy_path_funded_delivered_accepted_released(self) -> None:
        deliver_and_accept(self.model)
        self.model.release(HOLDER)
        self.assertIs(self.model.state, self.state.RELEASED)
        self.assertEqual(self.model.accepted_revision, 1)

    def test_dispute_resolved_to_release(self) -> None:
        deliver_and_dispute(self.model)
        self.model.resolve_dispute(HOLDER, releases=True)
        self.model.execute_resolution(HOLDER)
        self.assertIs(self.model.state, self.state.RELEASED)

    def test_dispute_resolved_to_refund(self) -> None:
        deliver_and_dispute(self.model)
        self.model.resolve_dispute(HOLDER, releases=False)
        self.model.execute_resolution(HOLDER)
        self.assertIs(self.model.state, self.state.REFUNDED)
        self.assertEqual(self.model.refund_reason, "dispute resolution")

    def test_cancellation_before_delivery_refunds(self) -> None:
        self.model.record_funding(HOLDER)
        self.model.refund_on_cancellation(HOLDER)
        self.assertIs(self.model.state, self.state.REFUNDED)
        self.assertEqual(self.model.refund_reason, "cancelled before delivery")


class UnresponsiveParticipantTests(unittest.TestCase):
    """Every waiting state names a deadline and a defined default outcome."""

    def setUp(self) -> None:
        self.model, self.revert, self.state = _load_model()

    def test_unresponsive_coordinator_review_timeout_takes_dispute_path(self) -> None:
        self.model.record_funding(HOLDER)
        self.model.record_delivery(CONTRIBUTOR, 1)
        self.model.now = REVIEW_DEADLINE + 1
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 1)  # never a silent release
        self.model.open_dispute(CONTRIBUTOR, "review deadline passed")
        self.model.resolve_dispute(HOLDER, releases=True)
        self.model.execute_resolution(HOLDER)
        self.assertIs(self.model.state, self.state.RELEASED)

    def test_unresponsive_holder_falls_back_after_dispute_window(self) -> None:
        deliver_and_dispute(self.model)
        self.model.now = DISPUTE_DEADLINE + 1
        with self.assertRaises(self.revert):
            self.model.resolve_dispute(HOLDER, releases=True)  # window closed
        self.model.apply_dispute_fallback(STRANGER)  # permissionless by design
        self.assertIs(self.model.state, self.state.REFUNDED)
        self.assertEqual(self.model.refund_reason, "dispute fallback")

    def test_unresponsive_contributor_refunds_from_funded(self) -> None:
        self.model.record_funding(HOLDER)
        self.model.now = OUTER_DEADLINE + 1
        self.model.refund_after_outer_deadline(STRANGER)  # permissionless, refund only
        self.assertIs(self.model.state, self.state.REFUNDED)
        self.assertEqual(self.model.refund_reason, "outer deadline")

    def test_unresponsive_holder_refunds_after_acceptance(self) -> None:
        deliver_and_accept(self.model)
        self.model.now = OUTER_DEADLINE + 1
        self.model.refund_after_outer_deadline(CONTRIBUTOR)
        self.assertIs(self.model.state, self.state.REFUNDED)

    def test_unresponsive_holder_refunds_after_resolution(self) -> None:
        deliver_and_dispute(self.model)
        self.model.resolve_dispute(HOLDER, releases=True)
        self.model.now = OUTER_DEADLINE + 1
        self.model.refund_after_outer_deadline(CONTRIBUTOR)
        self.assertIs(self.model.state, self.state.REFUNDED)

    def test_agreed_fallback_release_variant_reaches_released(self) -> None:
        model, revert, state = _load_model(releases_fallback=True)
        deliver_and_dispute(model)
        model.now = DISPUTE_DEADLINE + 1
        model.apply_dispute_fallback(STRANGER)
        self.assertIs(model.state, state.RELEASED)


class GuardTests(unittest.TestCase):
    """Role, replay, staleness and deadline guards mirror the contract."""

    def setUp(self) -> None:
        self.model, self.revert, self.state = _load_model()

    def test_stranger_holds_no_role_anywhere(self) -> None:
        for action in (
            lambda: self.model.record_funding(STRANGER),
            lambda: self.model.record_delivery(STRANGER, 1),
            lambda: self.model.accept(STRANGER, 1),
            lambda: self.model.release(STRANGER),
            lambda: self.model.resolve_dispute(STRANGER, True),
            lambda: self.model.execute_resolution(STRANGER),
            lambda: self.model.refund_on_cancellation(STRANGER),
        ):
            self.model.state = self.state.NONE
            with self.assertRaises(self.revert):
                action()

    def test_stranger_cannot_open_dispute_but_both_parties_can(self) -> None:
        self.model.record_funding(HOLDER)
        self.model.record_delivery(CONTRIBUTOR, 1)
        with self.assertRaises(self.revert):
            self.model.open_dispute(STRANGER, "nope")
        self.model.open_dispute(CONTRIBUTOR, "contributor disagrees")
        self.assertIs(self.model.state, self.state.DISPUTED)

    def test_deliveries_move_forward_only_and_replay_adds_nothing(self) -> None:
        self.model.record_funding(HOLDER)
        self.model.record_delivery(CONTRIBUTOR, 1)
        with self.assertRaises(self.revert):
            self.model.record_delivery(CONTRIBUTOR, 1)  # replay
        with self.assertRaises(self.revert):
            self.model.record_delivery(CONTRIBUTOR, 3)  # skipping ahead

    def test_accept_binds_newest_revision_only_before_review_deadline(self) -> None:
        self.model.record_funding(HOLDER)
        self.model.record_delivery(CONTRIBUTOR, 1)
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 2)  # stale: nothing like revision 2 exists
        self.model.now = REVIEW_DEADLINE + 1
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 1)  # too late
        self.model.now = REVIEW_DEADLINE
        self.model.accept(COORDINATOR, 1)  # exactly on the deadline still holds
        self.assertEqual(self.model.accepted_revision, 1)

    def test_dispute_window_guards_resolution_and_fallback(self) -> None:
        deliver_and_dispute(self.model)
        with self.assertRaises(self.revert):
            self.model.apply_dispute_fallback(STRANGER)  # window still open
        self.model.now = DISPUTE_DEADLINE
        self.model.resolve_dispute(HOLDER, releases=False)  # last moment allowed

    def test_outer_deadline_refund_needs_the_deadline_and_a_live_state(self) -> None:
        self.model.record_funding(HOLDER)
        self.model.now = OUTER_DEADLINE
        with self.assertRaises(self.revert):
            self.model.refund_after_outer_deadline(STRANGER)  # not yet
        self.model.now = OUTER_DEADLINE + 1
        self.model.refund_after_outer_deadline(STRANGER)
        with self.assertRaises(self.revert):
            self.model.refund_after_outer_deadline(STRANGER)  # terminal is final

    def test_terminal_states_are_final(self) -> None:
        deliver_and_accept(self.model)
        self.model.release(HOLDER)
        for action in (
            lambda: self.model.accept(COORDINATOR, 1),
            lambda: self.model.record_delivery(CONTRIBUTOR, 2),
            lambda: self.model.open_dispute(CONTRIBUTOR, "too late"),
            lambda: self.model.resolve_dispute(HOLDER, True),
            lambda: self.model.execute_resolution(HOLDER),
            lambda: self.model.refund_on_cancellation(HOLDER),
            lambda: self.model.release(HOLDER),
        ):
            with self.assertRaises(self.revert):
                action()
        with self.assertRaises(self.revert):
            self.model.refund_after_outer_deadline(CONTRIBUTOR)  # nothing left to refund


class ParityTests(unittest.TestCase):
    """The model and the committed contract must stay honest against each other."""

    def test_model_covers_every_contract_function(self) -> None:
        source = CONTRACT.read_text()
        model_source = MODEL.read_text()
        functions = re.findall(r"function (\w+)\(([^)]*)\) external", source)
        self.assertEqual(len(functions), 10)
        for name, _signature in functions:
            snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
            self.assertIn(f"def {snake}(", model_source, f"model must mirror {name}")

    def test_model_names_every_contract_failure(self) -> None:
        source = CONTRACT.read_text()
        model_source = MODEL.read_text()
        failures = re.findall(r"error (\w+)\(", source)
        self.assertEqual(len(failures), 10)
        for failure in failures:
            self.assertIn(f'"{failure}"', model_source, f"model must name the failure {failure}")


class CompilerTests(unittest.TestCase):
    """The pinned compiler must accept the committed settlement example.

    Fail-closed (review A3): tool availability is probed separately; compiler
    and artifact errors always fail, and missing tooling fails in CI.
    """

    @classmethod
    def setUpClass(cls) -> None:
        PINNED_SOLC.enforce_availability()

    def _compile(self) -> tuple[bytes, bytes]:
        with tempfile.TemporaryDirectory() as folder:
            try:
                artifacts = PINNED_SOLC.compile_with_artifacts(CONTRACT, Path(folder), "FairSettlement")
            except PINNED_SOLC.CompileFailed as error:
                self.fail(str(error))
            return artifacts["bin"], artifacts["abi"]

    def test_compiler_rejection_fails_the_gate_instead_of_skipping(self) -> None:
        # Mutation proof (review A3): a source the compiler rejects must turn
        # this gate red — never a green skip.
        with tempfile.TemporaryDirectory() as folder:
            broken = Path(folder) / "Broken.sol"
            broken.write_text("contract Broken { this is not solidity }\n", encoding="utf-8")
            with self.assertRaises(PINNED_SOLC.CompileFailed):
                PINNED_SOLC.compile_with_artifacts(broken, Path(folder), "Broken")

    def test_pinned_compiler_compiles_with_stable_bytecode(self) -> None:
        import hashlib
        binary, abi = self._compile()
        self.assertGreater(len(binary), 60, "compiled binary is implausibly small")
        for name in ("recordFunding", "openDispute", "resolveDispute", "executeResolution",
                     "applyDisputeFallback", "refundAfterOuterDeadline", "refundOnCancellation"):
            self.assertIn(f'"name":"{name}"'.encode(), abi, f"abi must expose {name}")
        again_binary, again_abi = self._compile()
        self.assertEqual(hashlib.sha256(binary).hexdigest(), hashlib.sha256(again_binary).hexdigest(),
                         "same pinned compiler and source must reproduce identical bytecode")
        self.assertEqual(hashlib.sha256(abi).hexdigest(), hashlib.sha256(again_abi).hexdigest(),
                         "same pinned compiler and source must reproduce identical abi")


if __name__ == "__main__":
    unittest.main()
