"""Python side-model of independent QA roles and dispute paths.

Mirrors the accepted design note (docs/qa-roles-and-dispute-paths.md): the
reviewer who checks evidence is not the party who benefits from acceptance;
acceptance binds exactly the newest revision and, when quality review is
required, needs that revision's qa `passed`; an opened dispute blocks
acceptance until it is resolved — upheld, rejected, or upheld by the window
default. Same named-failure discipline as the contract lab models.
Synthetic model only; no chain, no network, no funds.
"""

from __future__ import annotations


class Revert(Exception):
    """The named failure the live protocol's rules would raise."""


class QaModel:
    """Review roles for the coordination protocol, with dispute paths."""

    def __init__(self, contributor: str, coordinator: str, qa_reviewer: str,
                 review_deadline: int, dispute_window: int, qa_required: bool = True) -> None:
        if qa_reviewer == contributor:
            raise ValueError("the qa reviewer must never be the contributor")
        if not dispute_window > 0:
            raise ValueError("dispute_window must be positive")
        self.contributor = contributor
        self.coordinator = coordinator
        self.qa_reviewer = qa_reviewer
        self.review_deadline = review_deadline
        self.dispute_window = dispute_window
        self.qa_required = qa_required
        # Independence is structural, not verified: same person as coordinator
        # is allowed when no third party exists, but it is marked visibly.
        self.degraded = qa_reviewer == coordinator
        self.latest_revision = 0
        self.accepted_revision = 0
        self.revision_request = ""
        self.qa_state = "none"          # none | passed | failed (about latest_revision)
        self.qa_note = ""
        self.dispute_state = "none"     # none | opened | upheld | rejected
        self.dispute_reason = ""
        self.dispute_opened_at = 0
        self.now = 0

    def _only(self, sender: str, role_address: str, failure: str) -> None:
        if sender != role_address:
            raise Revert(failure)

    def record_delivery(self, sender: str, revision: int) -> None:
        """The contributor records the next immutable delivery revision. A new
        revision restarts qa review and renders an older dispute moot."""
        self._only(sender, self.contributor, "NotContributor")
        if self.accepted_revision != 0:
            raise Revert("AlreadyAccepted")
        if revision != self.latest_revision + 1:
            raise Revert("DuplicateRevision")
        self.latest_revision = revision
        self.qa_state = "none"
        self.qa_note = ""
        self.dispute_state = "none"
        self.dispute_reason = ""

    def qa_review(self, sender: str, passed: bool, note: str) -> None:
        """The qa reviewer records the judgment on the newest revision."""
        self._only(sender, self.qa_reviewer, "NotReviewer")
        if self.latest_revision == 0:
            raise Revert("NothingDelivered")
        if self.dispute_state == "opened":
            raise Revert("DisputeOpen")
        if self.now > self.review_deadline:
            raise Revert("DeadlinePassed")
        self.qa_state = "passed" if passed else "failed"
        self.qa_note = note

    def request_revision(self, sender: str, note: str) -> None:
        """The coordinator names what is missing; no recorded state is destroyed."""
        self._only(sender, self.coordinator, "NotCoordinator")
        if self.latest_revision == 0:
            raise Revert("NothingDelivered")
        if self.accepted_revision != 0:
            raise Revert("AlreadyAccepted")
        self.revision_request = note

    def accept(self, sender: str, revision: int) -> None:
        """The coordinator accepts exactly the newest revision before the
        deadline; when qa is required, that revision must have qa `passed`."""
        self._only(sender, self.coordinator, "NotCoordinator")
        if self.accepted_revision != 0:
            raise Revert("AlreadyAccepted")
        if self.latest_revision == 0:
            raise Revert("NothingDelivered")
        if revision != self.latest_revision:
            raise Revert("NotNewestRevision")
        if self.now > self.review_deadline:
            raise Revert("DeadlinePassed")
        if self.dispute_state == "opened":
            raise Revert("DisputeOpen")
        if self.dispute_state == "upheld":
            raise Revert("DisputeUpheld")
        if self.qa_required and self.qa_state != "passed":
            raise Revert("QaNotPassed")
        self.accepted_revision = revision

    def open_dispute(self, sender: str, reason: str) -> None:
        """Either bound party disputes the live revision's evidence. Blocks
        acceptance; destroys no recorded state. One dispute cycle per revision."""
        if sender != self.contributor and sender != self.coordinator:
            raise Revert("NotParty")
        if self.latest_revision == 0:
            raise Revert("NothingDelivered")
        if self.accepted_revision != 0:
            raise Revert("AlreadyAccepted")
        if self.dispute_state != "none":
            raise Revert("DisputeAlreadyResolved" if self.dispute_state != "opened" else "DisputeOpen")
        self.dispute_state = "opened"
        self.dispute_reason = reason
        self.dispute_opened_at = self.now

    def resolve_dispute(self, sender: str, upheld: bool) -> None:
        """The qa reviewer resolves the dispute under the agreement's rules,
        inside the window. Upheld: the revision can no longer be accepted.
        Rejected: the review path reopens unchanged."""
        self._only(sender, self.qa_reviewer, "NotReviewer")
        if self.dispute_state != "opened":
            raise Revert("NoOpenDispute")
        if self.now > self.dispute_opened_at + self.dispute_window:
            raise Revert("DeadlinePassed")
        self.dispute_state = "upheld" if upheld else "rejected"

    def apply_dispute_default(self, sender: str) -> None:
        """Past the dispute window an unresolved dispute defaults to upheld —
        never silently accepted. Permissionless: it only reaches the fixed
        outcome, so it mints no authority."""
        if self.dispute_state != "opened":
            raise Revert("NoOpenDispute")
        if self.now <= self.dispute_opened_at + self.dispute_window:
            raise Revert("DisputeWindowStillOpen")
        self.dispute_state = "upheld"
