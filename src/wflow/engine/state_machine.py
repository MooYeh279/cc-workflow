"""Run-level state machine for workflow execution."""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"


_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.PENDING: {RunStatus.RUNNING},
    RunStatus.RUNNING: {RunStatus.PAUSED, RunStatus.AWAITING_REVIEW, RunStatus.COMPLETED, RunStatus.FAILED},
    RunStatus.PAUSED: {RunStatus.RUNNING},
    RunStatus.AWAITING_REVIEW: {RunStatus.RUNNING, RunStatus.FAILED},
    RunStatus.FAILED: {RunStatus.RUNNING},   # manual retry
    RunStatus.COMPLETED: {RunStatus.RUNNING},  # allow re-run
}


class RunStateMachine:
    """Validates and executes run-level state transitions."""

    def can_transition(self, current: RunStatus, target: RunStatus) -> bool:
        """Check if a transition is valid."""
        return target in _TRANSITIONS.get(current, set())

    def transition(self, current: RunStatus, target: RunStatus) -> RunStatus:
        """Execute a state transition, raising ValueError if invalid."""
        if not self.can_transition(current, target):
            raise ValueError(
                f"Invalid transition: {current.value} -> {target.value}. "
                f"Allowed from '{current.value}': {[s.value for s in _TRANSITIONS.get(current, set())]}"
            )
        return target
