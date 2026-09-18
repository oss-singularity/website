"""Python side-model of the generated FairSettlement contract.

Mirrors the settlement state machine exactly — same states, same guards,
same named failures — so every settlement outcome, including the timeout
paths for unresponsive participants, can be exercised without any chain,
compiler or network. When the contract changes, this model must change
with it; the walkthrough tests keep both honest against each other.
Synthetic model only; it never touches funds, wallets or networks.
"""

from __future__ import annotations

from enum import Enum


class Revert(Exception):
    """The named failure the contract would raise."""


class State(Enum):
    """The settlement states, named exactly as the design note names them."""

    NONE = "none"
    FUNDED = "funded"
    DELIVERED = "delivered"
    ACCEPTED = "accepted"
    DISPUTED = "disputed"
    RESOLVED = "resolved"
    RELEASED = "released"
    REFUNDED = "refunded"


NON_TERMINAL = (State.FUNDED, State.DELIVERED, State.ACCEPTED, State.DISPUTED, State.RESOLVED)


class FairSettlementModel:
    """Same rules as FairSettlement.sol: the agreement comes before the work
    and before any funds move; acceptance is the release CONDITION, never the
    release; every waiting state has a deadline and reaches exactly the
    outcome the agreement already fixed — a timeout never mints authority."""

    def __init__(self, contributor: str, coordinator: str, holder: str,
                 review_deadline: int, dispute_deadline: int, outer_deadline: int,
                 dispute_fallback_releases: bool) -> None:
        if len({contributor, coordinator, holder}) != 3:
            raise ValueError("contributor, coordinator and holder must be three distinct addresses")
        if not review_deadline < dispute_deadline < outer_deadline:
            raise ValueError("deadlines must ascend: review < dispute < outer")
        self.contributor = contributor
        self.coordinator = coordinator
        self.holder = holder
        self.review_deadline = review_deadline
        self.dispute_deadline = dispute_deadline
        self.outer_deadline = outer_deadline
        self.dispute_fallback_releases = dispute_fallback_releases
        self.state = State.NONE
        self.current_revision = 0
        self.accepted_revision = 0
        self.resolution_releases = False
        self.dispute_reason = ""
        self.refund_reason = ""
        self.now = 0

    def _only(self, sender: str, role_address: str, failure: str) -> None:
        if sender != role_address:
            raise Revert(failure)

    def _require_state(self, *states: State) -> None:
        if self.state not in states:
            raise Revert("WrongState")

    def _refund(self, reason: str) -> None:
        self.refund_reason = reason
        self.state = State.REFUNDED

    def record_funding(self, sender: str) -> None:
        """The holder declares the budget sits with the authorized holder."""
        self._only(sender, self.holder, "NotHolder")
        self._require_state(State.NONE)
        self.state = State.FUNDED

    def record_delivery(self, sender: str, revision: int) -> None:
        """The contributor records the next immutable delivery revision."""
        self._only(sender, self.contributor, "NotContributor")
        self._require_state(State.FUNDED)
        if revision != self.current_revision + 1:
            raise Revert("DuplicateRevision")
        self.current_revision = revision
        self.state = State.DELIVERED

    def accept(self, sender: str, revision: int) -> None:
        """The coordinator accepts exactly the newest revision — the release
        condition, never the release itself — before the review deadline."""
        self._only(sender, self.coordinator, "NotCoordinator")
        self._require_state(State.DELIVERED)
        if revision != self.current_revision:
            raise Revert("NotNewestRevision")
        if self.now > self.review_deadline:
            raise Revert("DeadlinePassed")
        self.accepted_revision = revision
        self.state = State.ACCEPTED

    def release(self, sender: str) -> None:
        """The holder executes the release its custody rules require."""
        self._only(sender, self.holder, "NotHolder")
        self._require_state(State.ACCEPTED)
        self.state = State.RELEASED

    def open_dispute(self, sender: str, reason: str) -> None:
        """Either party opens the dispute path named in the agreement."""
        if sender != self.contributor and sender != self.coordinator:
            raise Revert("NotParty")
        self._require_state(State.DELIVERED)
        self.dispute_reason = reason
        self.state = State.DISPUTED

    def resolve_dispute(self, sender: str, releases: bool) -> None:
        """The holder records the resolution inside the dispute window."""
        self._only(sender, self.holder, "NotHolder")
        self._require_state(State.DISPUTED)
        if self.now > self.dispute_deadline:
            raise Revert("DeadlinePassed")
        self.resolution_releases = releases
        self.state = State.RESOLVED

    def execute_resolution(self, sender: str) -> None:
        """The holder executes the recorded resolution: released or refunded."""
        self._only(sender, self.holder, "NotHolder")
        self._require_state(State.RESOLVED)
        if self.resolution_releases:
            self.state = State.RELEASED
        else:
            self._refund("dispute resolution")

    def apply_dispute_fallback(self, sender: str) -> None:
        """Past the dispute window the agreed fallback replaces a silent
        holder — permissionless on purpose, it only reaches the fixed outcome."""
        self._require_state(State.DISPUTED)
        if self.now <= self.dispute_deadline:
            raise Revert("DisputeWindowStillOpen")
        if self.dispute_fallback_releases:
            self.state = State.RELEASED
        else:
            self._refund("dispute fallback")

    def refund_after_outer_deadline(self, sender: str) -> None:
        """Past the outer deadline, unreleased funds take the refund path
        from every non-terminal state — permissionless, refund only."""
        self._require_state(*NON_TERMINAL)
        if self.now <= self.outer_deadline:
            raise Revert("OuterDeadlineNotPassed")
        self._refund("outer deadline")

    def refund_on_cancellation(self, sender: str) -> None:
        """Cancellation before delivery: the budget flows back unused."""
        self._only(sender, self.holder, "NotHolder")
        self._require_state(State.FUNDED)
        self._refund("cancelled before delivery")
