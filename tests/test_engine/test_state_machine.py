import pytest
from wflow.engine.state_machine import RunStateMachine, RunStatus


def test_valid_transitions():
    sm = RunStateMachine()
    assert sm.can_transition(RunStatus.PENDING, RunStatus.RUNNING) is True
    assert sm.can_transition(RunStatus.RUNNING, RunStatus.PAUSED) is True
    assert sm.can_transition(RunStatus.RUNNING, RunStatus.COMPLETED) is True
    assert sm.can_transition(RunStatus.RUNNING, RunStatus.FAILED) is True
    assert sm.can_transition(RunStatus.PAUSED, RunStatus.RUNNING) is True
    assert sm.can_transition(RunStatus.FAILED, RunStatus.RUNNING) is True
    assert sm.can_transition(RunStatus.COMPLETED, RunStatus.RUNNING) is True  # re-run


def test_invalid_transitions():
    sm = RunStateMachine()
    assert sm.can_transition(RunStatus.COMPLETED, RunStatus.PAUSED) is False
    assert sm.can_transition(RunStatus.COMPLETED, RunStatus.FAILED) is False
    assert sm.can_transition(RunStatus.PENDING, RunStatus.COMPLETED) is False
    assert sm.can_transition(RunStatus.PAUSED, RunStatus.COMPLETED) is False


def test_transition_raises_on_invalid():
    sm = RunStateMachine()
    with pytest.raises(ValueError, match="Invalid transition"):
        sm.transition(RunStatus.COMPLETED, RunStatus.PAUSED)


def test_transition_returns_new_status():
    sm = RunStateMachine()
    new_status = sm.transition(RunStatus.PENDING, RunStatus.RUNNING)
    assert new_status == RunStatus.RUNNING


def test_failed_can_retry_to_running():
    sm = RunStateMachine()
    assert sm.can_transition(RunStatus.FAILED, RunStatus.RUNNING) is True
