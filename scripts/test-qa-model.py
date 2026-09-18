#!/usr/bin/env python3
"""Walkthrough for independent QA roles and dispute paths (project 2, M3).

Every acceptance and dispute path of the accepted design note, exercised
against the Python side-model: role separation, qa-gated acceptance,
revision restarts after a failed review, dispute blocks, upheld/rejected
resolutions, and the window default that never silently accepts.
Synthetic walkthrough only; no chain, no network, no funds.
"""

import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODEL = REPO / "design" / "coordination" / "qa_model.py"
NOTE = REPO / "docs" / "qa-roles-and-dispute-paths.md"

CONTRIBUTOR = "0x0000000000000000000000000000000000000001"
COORDINATOR = "0x0000000000000000000000000000000000000002"
REVIEWER = "0x0000000000000000000000000000000000000003"
STRANGER = "0x0000000000000000000000000000000000000004"
REVIEW_DEADLINE = 1000
DISPUTE_WINDOW = 500


def _load(qa_reviewer: str = REVIEWER, qa_required: bool = True):
    spec = importlib.util.spec_from_file_location("qa_model", MODEL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    model = module.QaModel(CONTRIBUTOR, COORDINATOR, qa_reviewer,
                           REVIEW_DEADLINE, DISPUTE_WINDOW, qa_required=qa_required)
    return model, module.Revert


def deliver(model, revision: int = 1) -> None:
    model.record_delivery(CONTRIBUTOR, revision)


class RoleSeparationTests(unittest.TestCase):
    """The reviewer who checks evidence is not the party who benefits."""

    def test_qa_reviewer_must_never_be_the_contributor(self) -> None:
        with self.assertRaises(ValueError):
            _load(qa_reviewer=CONTRIBUTOR)

    def test_no_third_party_degrades_visibly_not_silently(self) -> None:
        model, _revert = _load(qa_reviewer=COORDINATOR)
        self.assertTrue(model.degraded)  # allowed only with a visible mark
        model, _revert = _load()
        self.assertFalse(model.degraded)

    def test_stranger_holds_no_role_anywhere(self) -> None:
        model, revert = _load()
        for action in (
            lambda: model.record_delivery(STRANGER, 1),
            lambda: model.qa_review(STRANGER, True, "nope"),
            lambda: model.accept(STRANGER, 1),
            lambda: model.request_revision(STRANGER, "nope"),
            lambda: model.resolve_dispute(STRANGER, True),
        ):
            with self.assertRaises(revert):
                action()
        with self.assertRaises(revert):
            model.open_dispute(STRANGER, "nope")  # only bound parties dispute

    def test_each_role_is_bound_to_its_own_decisions(self) -> None:
        model, revert = _load()
        with self.assertRaises(revert):
            model.qa_review(CONTRIBUTOR, True, "self review refused")  # hard rule
        deliver(model)
        with self.assertRaises(revert):
            model.qa_review(CONTRIBUTOR, True, "self review refused")
        with self.assertRaises(revert):
            model.accept(REVIEWER, 1)  # reviewer cannot accept
        with self.assertRaises(revert):
            model.record_delivery(COORDINATOR, 1)  # coordinator cannot deliver
        with self.assertRaises(revert):
            model.resolve_dispute(COORDINATOR, True)  # qa resolves disputes, nobody else


class AcceptancePathTests(unittest.TestCase):
    """Acceptance binds the newest revision and honors the qa condition."""

    def setUp(self) -> None:
        self.model, self.revert = _load()

    def test_qa_gated_happy_path(self) -> None:
        deliver(self.model)
        self.model.qa_review(REVIEWER, True, "criteria v3 checked")
        self.model.accept(COORDINATOR, 1)
        self.assertEqual(self.model.accepted_revision, 1)

    def test_accept_without_qa_pass_is_refused_then_succeeds_after_review(self) -> None:
        deliver(self.model)
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 1)  # qa required, nothing recorded yet
        self.model.qa_review(REVIEWER, True, "pass")
        self.model.accept(COORDINATOR, 1)

    def test_failed_qa_names_what_is_missing_and_restart_resets_review(self) -> None:
        deliver(self.model)
        self.model.qa_review(REVIEWER, False, "retention statement missing")
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 1)
        self.model.request_revision(COORDINATOR, "add the retention statement")
        self.model.record_delivery(CONTRIBUTOR, 2)
        self.assertEqual(self.model.qa_state, "none")  # new revision restarts review
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 2)  # stale qa pass cannot carry over
        self.model.qa_review(REVIEWER, True, "criteria v3 checked")
        self.model.accept(COORDINATOR, 2)

    def test_passed_qa_is_a_condition_not_a_command(self) -> None:
        deliver(self.model)
        self.model.qa_review(REVIEWER, True, "pass")
        self.model.request_revision(COORDINATOR, "scope changed, deliver again")
        self.assertEqual(self.model.accepted_revision, 0)  # coordinator simply has not accepted

    def test_stale_replay_and_deadline_rules_hold(self) -> None:
        deliver(self.model)
        self.model.qa_review(REVIEWER, True, "pass")
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 2)  # stale
        self.model.now = REVIEW_DEADLINE + 1
        with self.assertRaises(self.revert):
            self.model.qa_review(REVIEWER, True, "too late")
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 1)  # deadline passed
        self.model.now = REVIEW_DEADLINE
        self.model.accept(COORDINATOR, 1)  # exactly on the deadline still holds
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 1)  # acceptance is final
        with self.assertRaises(self.revert):
            self.model.record_delivery(CONTRIBUTOR, 2)  # nothing after acceptance

    def test_qa_cannot_review_while_a_dispute_is_open(self) -> None:
        deliver(self.model)
        self.model.open_dispute(CONTRIBUTOR, "evidence unclear")
        with self.assertRaises(self.revert):
            self.model.qa_review(REVIEWER, True, "blocked")

    def test_optional_qa_mode_accepts_without_review(self) -> None:
        model, revert = _load(qa_required=False)
        deliver(model)
        model.accept(COORDINATOR, 1)
        self.assertEqual(model.accepted_revision, 1)


class DisputePathTests(unittest.TestCase):
    """A dispute blocks acceptance until resolved — never silently accepted."""

    def setUp(self) -> None:
        self.model, self.revert = _load()
        deliver(self.model)
        self.model.qa_review(REVIEWER, True, "pass")

    def test_open_dispute_blocks_acceptance_and_destroys_nothing(self) -> None:
        self.model.open_dispute(CONTRIBUTOR, "digest does not match my build")
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 1)
        self.assertEqual(self.model.latest_revision, 1)  # delivery untouched
        self.assertEqual(self.model.qa_state, "passed")  # qa record untouched

    def test_rejected_dispute_reopens_the_review_path(self) -> None:
        self.model.open_dispute(COORDINATOR, "digest does not match my build")
        self.model.resolve_dispute(REVIEWER, upheld=False)
        self.model.accept(COORDINATOR, 1)  # path reopened unchanged
        self.assertEqual(self.model.accepted_revision, 1)

    def test_upheld_dispute_refuses_the_revision_until_a_new_one_supersedes(self) -> None:
        self.model.open_dispute(CONTRIBUTOR, "wrong bytes delivered")
        self.model.resolve_dispute(REVIEWER, upheld=True)
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 1)  # disputed revision is spent
        self.model.record_delivery(CONTRIBUTOR, 2)  # superseding revision resets dispute
        self.model.qa_review(REVIEWER, True, "criteria v3 checked")
        self.model.accept(COORDINATOR, 2)

    def test_one_dispute_cycle_per_revision(self) -> None:
        self.model.open_dispute(CONTRIBUTOR, "first")
        with self.assertRaises(self.revert):
            self.model.open_dispute(COORDINATOR, "second while open")
        self.model.resolve_dispute(REVIEWER, upheld=False)
        with self.assertRaises(self.revert):
            self.model.open_dispute(COORDINATOR, "re-litigating the same revision")

    def test_window_stays_open_exactly_until_its_deadline(self) -> None:
        self.model.open_dispute(CONTRIBUTOR, "no reviewer reaction")
        self.model.now = self.model.dispute_opened_at + DISPUTE_WINDOW
        with self.assertRaises(self.revert):
            self.model.apply_dispute_default(STRANGER)  # still inside the window

    def test_default_reaches_upheld_after_the_window(self) -> None:
        self.model.open_dispute(CONTRIBUTOR, "no reviewer reaction")
        self.model.now = self.model.dispute_opened_at + DISPUTE_WINDOW + 1
        self.model.apply_dispute_default(STRANGER)  # permissionless by design
        self.assertEqual(self.model.dispute_state, "upheld")
        with self.assertRaises(self.revert):
            self.model.accept(COORDINATOR, 1)


class ParityTests(unittest.TestCase):
    """The model must keep carrying the design note's named rules."""

    def test_model_uses_the_design_note_state_language(self) -> None:
        model_source = MODEL.read_text()
        note_source = NOTE.read_text()
        for rule in ("passed", "failed", "opened", "upheld", "rejected", "degraded"):
            self.assertIn(rule, model_source, f"model must carry the {rule} state")
            self.assertIn(rule, note_source, f"design note must name the {rule} state")
        for failure in ("NotContributor", "NotCoordinator", "NotReviewer", "NotParty",
                        "DuplicateRevision", "NotNewestRevision", "DeadlinePassed",
                        "DisputeOpen", "NoOpenDispute", "DisputeUpheld", "QaNotPassed"):
            self.assertIn(f'"{failure}"', model_source, f"model must name the failure {failure}")


if __name__ == "__main__":
    unittest.main()
