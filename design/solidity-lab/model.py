"""Python side-model of the generated DeliveryAcceptance contract.

Mirrors the Solidity state machine exactly, so role, replay and failure
behaviour can be exercised without any chain, compiler or network. When the
contract changes, this model must change with it — the lab tests keep both
honest against each other. Synthetic example only; no funds, no deployment.
"""

from __future__ import annotations


class Revert(Exception):
    """The named failure the contract would raise."""


class DeliveryAcceptanceModel:
    """Same rules as DeliveryAcceptance.sol: forward-only revisions, role
    checks at every decision point, acceptance binds exactly the newest
    revision before the deadline and is final."""

    def __init__(self, contributor: str, coordinator: str, deadline: int) -> None:
        self.contributor = contributor
        self.coordinator = coordinator
        self.deadline = deadline
        self.current_revision = 0
        self.accepted_revision = 0
        self.revision_request = ""
        self.now = 0

    def _only(self, sender: str, role_address: str, failure: str) -> None:
        if sender != role_address:
            raise Revert(failure)

    def record_delivery(self, sender: str, revision: int) -> None:
        self._only(sender, self.contributor, "NotContributor")
        if self.accepted_revision != 0:
            raise Revert("AlreadyAccepted")
        if revision != self.current_revision + 1:
            raise Revert("DuplicateRevision")
        self.current_revision = revision

    def request_revision(self, sender: str, revision: int, note: str) -> None:
        self._only(sender, self.coordinator, "NotCoordinator")
        if self.current_revision == 0:
            raise Revert("NothingDelivered")
        if revision != self.current_revision:
            raise Revert("NotNewestRevision")
        self.revision_request = note

    def accept(self, sender: str, revision: int) -> None:
        self._only(sender, self.coordinator, "NotCoordinator")
        if self.accepted_revision != 0:
            raise Revert("AlreadyAccepted")
        if self.current_revision == 0:
            raise Revert("NothingDelivered")
        if revision != self.current_revision:
            raise Revert("NotNewestRevision")
        if self.now > self.deadline:
            raise Revert("DeadlinePassed")
        self.accepted_revision = revision
