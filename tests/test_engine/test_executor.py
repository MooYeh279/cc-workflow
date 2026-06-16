import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from wflow.engine.executor import WorkflowExecutor, _evaluate_condition
from wflow.engine.template import TemplateContext


def make_context() -> TemplateContext:
    return {"inputs": {}, "nodes": {}, "run": {"id": "run-1"}, "config": {"max_retries": 2}}


def make_mock_session_factory():
    """Return a session_factory mock that yields an AsyncMock session."""
    mock_db = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=mock_cm)


def test_evaluate_condition_none_returns_true():
    assert _evaluate_condition(None, make_context()) is True


def test_evaluate_condition_empty_returns_true():
    assert _evaluate_condition("", make_context()) is True


def test_evaluate_condition_true_string():
    assert _evaluate_condition("true", make_context()) is True


def test_evaluate_condition_false_string():
    assert _evaluate_condition("false", make_context()) is False


def test_evaluate_condition_with_template():
    ctx = make_context()
    ctx["nodes"]["review"] = {"output": {"approved": "true"}}
    result = _evaluate_condition("{{ nodes.review.output.approved }}", ctx)
    assert result is True


def test_evaluate_condition_not_equal():
    ctx = make_context()
    ctx["nodes"]["review"] = {"output": {"approved": True}}
    result = _evaluate_condition("{{ nodes.review.output.approved }} != false", ctx)
    assert result is True


@pytest.mark.asyncio
async def test_executor_runs_nodes_in_order():
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test-wf",
        nodes=[
            {"id": "start", "type": "claude", "prompt": "go",
             "tools": {"allowed": [], "disallowed": [], },
             "retry": {"max_retries": 1, "on_error": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "end", "type": "script",
             "command": "python ./scripts/test.py",
             "timeout_seconds": 60,
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[{"id": "e1", "from": "start", "to": "end"}],
    )

    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=[
        {"x": 42, "_session_id": "s1"},
        {"echo": {}, "_session_id": "s2"},
    ])

    session_mgr = MagicMock()
    session_mgr.get_provider_session_id = AsyncMock(return_value=None)
    session_mgr.get_session_id = AsyncMock(return_value=None)
    session_mgr.get_or_create = AsyncMock(side_effect=["s1", "s2"])
    session_mgr.is_session_valid = AsyncMock(return_value=True)
    session_mgr.mark_completed = AsyncMock()
    session_mgr.set_provider_session_id = AsyncMock()

    db_session = AsyncMock()
    executor = WorkflowExecutor(db=db_session, node_runner=node_runner, session_manager=session_mgr, session_factory=make_mock_session_factory())

    result = await executor.execute(spec, "run-1", make_context(), workflow_name="test-wf")
    assert result is True
    assert node_runner.run.call_count == 2


@pytest.mark.asyncio
async def test_executor_evaluates_conditions():
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="conditional",
        nodes=[
            {"id": "check", "type": "claude", "prompt": "check",
             "tools": {"allowed": [], "disallowed": [], },
             "retry": {"max_retries": 1, "on_error": []},
             "output": {"type": "object", "properties": {"pass": {"type": "boolean"}}, "required": ["pass"]}},
            {"id": "good", "type": "script",
             "command": "python ./scripts/test.py", "timeout_seconds": 60,
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "bad", "type": "script",
             "command": "python ./scripts/test.py", "timeout_seconds": 60,
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "check", "to": "good", "condition": "true"},
            {"id": "e2", "from": "check", "to": "bad", "condition": "false"},
        ],
    )

    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=[
        {"pass": True, "_session_id": "s1"},
        {"echo": {}, "_session_id": "s2"},
    ])

    session_mgr = MagicMock()
    session_mgr.get_provider_session_id = AsyncMock(return_value=None)
    session_mgr.get_session_id = AsyncMock(return_value=None)
    session_mgr.get_or_create = AsyncMock(side_effect=["s1", "s2"])
    session_mgr.is_session_valid = AsyncMock(return_value=True)
    session_mgr.mark_completed = AsyncMock()
    session_mgr.set_provider_session_id = AsyncMock()

    db_session = AsyncMock()
    executor = WorkflowExecutor(db=db_session, node_runner=node_runner, session_manager=session_mgr, session_factory=make_mock_session_factory())

    result = await executor.execute(spec, "run-1", make_context(), workflow_name="conditional")
    assert result is True
    calls = [c.args[0]["id"] for c in node_runner.run.call_args_list]
    assert calls == ["check", "good"]


@pytest.mark.asyncio
async def test_executor_handles_retry_and_failure():
    from wflow.models.workflow import WorkflowSpec
    from wflow.adapters.claude_cli import ClaudeCLIError

    spec = WorkflowSpec(
        name="retry-test",
        nodes=[
            {"id": "flakey", "type": "claude", "prompt": "try",
             "tools": {"allowed": [], "disallowed": [], },
             "retry": {"max_retries": 2, "on_error": ["parse_error"], "retry_delay_seconds": 0},
             "output": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}},
        ],
        edges=[{"id": "e1", "from": "flakey", "to": None}],
    )

    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=[
        ClaudeCLIError("parse failed"),
        ClaudeCLIError("parse failed"),
        {"ok": True, "_session_id": "s1"},
    ])

    session_mgr = MagicMock()
    session_mgr.get_provider_session_id = AsyncMock(return_value=None)
    session_mgr.get_session_id = AsyncMock(return_value="s1")
    session_mgr.get_or_create = AsyncMock(return_value="s1")
    session_mgr.is_session_valid = AsyncMock(return_value=True)
    session_mgr.mark_completed = AsyncMock()
    session_mgr.set_provider_session_id = AsyncMock()

    db_session = AsyncMock()
    executor = WorkflowExecutor(db=db_session, node_runner=node_runner, session_manager=session_mgr, session_factory=make_mock_session_factory())

    result = await executor.execute(spec, "run-1", make_context(), workflow_name="retry-test")
    assert result is True
    assert node_runner.run.call_count == 3


def test_get_upstream_output_first_node_returns_none():
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test",
        nodes=[{"id": "start", "type": "claude", "prompt": "go",
                "tools": {"allowed": [], "disallowed": []},
                "output": {"type": "object", "properties": {}, "required": []}}],
        edges=[],
    )
    executor = WorkflowExecutor(db=AsyncMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())

    result = executor._get_upstream_output(spec, "start", make_context())
    assert result is None


def test_get_upstream_output_returns_predecessor_output():
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test",
        nodes=[
            {"id": "a", "type": "claude", "prompt": "a",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "claude", "prompt": "b",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[{"id": "e1", "from": "a", "to": "b"}],
    )
    executor = WorkflowExecutor(db=AsyncMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())
    ctx = make_context()
    ctx["nodes"]["a"] = {"output": {"x": 1}, "status": "completed"}

    result = executor._get_upstream_output(spec, "b", ctx)
    assert result == {"x": 1}


def test_get_upstream_output_skips_failed_predecessor():
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test",
        nodes=[
            {"id": "a", "type": "claude", "prompt": "a",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "claude", "prompt": "b",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[{"id": "e1", "from": "a", "to": "b"}],
    )
    executor = WorkflowExecutor(db=AsyncMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())
    ctx = make_context()
    ctx["nodes"]["a"] = {"output": {}, "status": "failed"}

    result = executor._get_upstream_output(spec, "b", ctx)
    assert result is None


@pytest.mark.asyncio
async def test_executor_diamond_dag():
    """Diamond DAG: A→B→D, A→C→D — D receives both B and C outputs."""
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="diamond",
        nodes=[
            {"id": "a", "type": "script", "command": "echo a",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "script", "command": "echo b",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "c", "type": "script", "command": "echo c",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "d", "type": "script", "command": "echo d",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "a", "to": "b"},
            {"id": "e2", "from": "a", "to": "c"},
            {"id": "e3", "from": "b", "to": "d"},
            {"id": "e4", "from": "c", "to": "d"},
        ],
    )

    node_runner = MagicMock()
    outputs = [
        {"echo": "a", "_session_id": "s1"},
        {"echo": "b", "_session_id": "s2"},
        {"echo": "c", "_session_id": "s3"},
        {"echo": "d", "_session_id": "s4"},
    ]
    node_runner.run = AsyncMock(side_effect=outputs)

    session_mgr = MagicMock()
    session_mgr.get_session_id = AsyncMock(return_value=None)
    session_mgr.get_or_create = AsyncMock(side_effect=["s1", "s2", "s3", "s4"])
    session_mgr.is_session_valid = AsyncMock(return_value=False)
    session_mgr.mark_completed = AsyncMock()

    db_session = AsyncMock()
    executor = WorkflowExecutor(db=db_session, node_runner=node_runner, session_manager=session_mgr, session_factory=make_mock_session_factory())

    result = await executor.execute(spec, "run-1", make_context(), workflow_name="diamond")
    assert result is True
    # All 4 nodes should execute
    assert node_runner.run.call_count == 4
    # Verify D received both upstream outputs
    last_call_kwargs = node_runner.run.call_args_list[3].kwargs
    up = last_call_kwargs["upstream_output"]
    assert "b" in up
    assert "c" in up


def test_all_predecessors_done_start_node():
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test",
        nodes=[
            {"id": "start", "type": "script", "command": "echo",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[],
    )
    executor = WorkflowExecutor(db=AsyncMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())
    assert executor._all_predecessors_done(spec, "start", make_context(), {"start"}) is True


def test_all_predecessors_done_merge_all_completed():
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test",
        nodes=[
            {"id": "a", "type": "script", "command": "echo",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "script", "command": "echo",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "d", "type": "script", "command": "echo",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[{"id": "e1", "from": "a", "to": "d"}, {"id": "e2", "from": "b", "to": "d"}],
    )
    executor = WorkflowExecutor(db=AsyncMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())
    ctx = make_context()
    ctx["nodes"]["a"] = {"output": {}, "status": "completed"}
    ctx["nodes"]["b"] = {"output": {}, "status": "completed"}
    # Both a and b are reached and completed
    assert executor._all_predecessors_done(spec, "d", ctx, {"a", "b", "d"}) is True


def test_all_predecessors_done_merge_partial():
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test",
        nodes=[
            {"id": "a", "type": "script", "command": "echo",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "script", "command": "echo",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "d", "type": "script", "command": "echo",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[{"id": "e1", "from": "a", "to": "d"}, {"id": "e2", "from": "b", "to": "d"}],
    )
    executor = WorkflowExecutor(db=AsyncMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())
    ctx = make_context()
    ctx["nodes"]["a"] = {"output": {}, "status": "completed"}
    # a completed, b reached but NOT completed → d should wait
    assert executor._all_predecessors_done(spec, "d", ctx, {"a", "b", "d"}) is False


def test_all_predecessors_done_loopback():
    """Loop-back: node already executed, any predecessor done is enough."""
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test",
        nodes=[
            {"id": "coding", "type": "claude", "prompt": "code",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "review", "type": "claude", "prompt": "review",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[{"id": "e1", "from": "review", "to": "coding"}],
    )
    executor = WorkflowExecutor(db=AsyncMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())
    ctx = make_context()
    ctx["nodes"]["coding"] = {"output": {"files": ["a.py"]}, "status": "completed"}
    ctx["nodes"]["review"] = {"output": {"approved": False}, "status": "completed"}
    assert executor._all_predecessors_done(spec, "coding", ctx, {"coding", "review"}) is True


def test_all_predecessors_done_loopback_no_predecessor():
    """Loop-back: node executed but predecessor not completed."""
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test",
        nodes=[
            {"id": "coding", "type": "claude", "prompt": "code",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "review", "type": "claude", "prompt": "review",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[{"id": "e1", "from": "review", "to": "coding"}],
    )
    executor = WorkflowExecutor(db=AsyncMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())
    ctx = make_context()
    ctx["nodes"]["coding"] = {"output": {"files": ["a.py"]}, "status": "completed"}
    # review not completed yet → loop-back should wait
    assert executor._all_predecessors_done(spec, "coding", ctx, {"coding", "review"}) is False


def test_all_predecessors_done_start_with_loop_edge():
    """Start node with a future loop-back edge: predecessor not reached yet → OK."""
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test",
        nodes=[
            {"id": "coding", "type": "claude", "prompt": "code",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "review", "type": "claude", "prompt": "review",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[{"id": "e1", "from": "review", "to": "coding"}],
    )
    executor = WorkflowExecutor(db=AsyncMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())
    ctx = make_context()
    # coding is start — review (its predecessor) hasn't been reached yet
    assert executor._all_predecessors_done(spec, "coding", ctx, {"coding"}) is True


@pytest.mark.asyncio
async def test_executor_loopback_cycle():
    """Full loop-back: coding → review → coding(retry) → review → end.

    Simulates code-review workflow where review fails once then approves.
    """
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="loopback",
        nodes=[
            {"id": "coding", "type": "claude", "prompt": "code",
             "tools": {"allowed": [], "disallowed": []},
             "retry": {"max_retries": 1, "on_error": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "review", "type": "claude", "prompt": "review",
             "tools": {"allowed": [], "disallowed": []},
             "retry": {"max_retries": 1, "on_error": []},
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "coding", "to": "review"},
            {"id": "e2", "from": "review", "to": "coding", "condition": "{{ nodes.review.output.approved }} == false"},
            {"id": "e3", "from": "review", "to": None, "condition": "{{ nodes.review.output.approved }} == true"},
        ],
    )

    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=[
        {"files_changed": ["a.py"], "summary": "v1", "_session_id": "s1"},     # coding R1
        {"approved": False, "feedback": "fix line 42", "_session_id": "s2"},   # review R1
        {"files_changed": ["a.py"], "summary": "v2 fixed", "_session_id": "s1"}, # coding R2 (resume)
        {"approved": True, "feedback": "", "_session_id": "s2"},                # review R2 (resume)
    ])

    session_mgr = MagicMock()
    # coding R1 → None (first run), review R1 → None (first run),
    # coding R2 → "s1" (resume), review R2 → "s2" (resume)
    session_mgr.get_provider_session_id = AsyncMock(side_effect=[None, None, "s1", "s2"])
    # R1 coding(new) → None, R2 review(new) → None, R3 coding(loop) → s1, R4 review(loop) → s2
    session_mgr.get_session_id = AsyncMock(side_effect=[None, None, "s1", "s2"])
    session_mgr.get_or_create = AsyncMock(side_effect=["s1", "s2", "s1", "s2"])
    session_mgr.is_session_valid = AsyncMock(return_value=True)
    session_mgr.mark_completed = AsyncMock()
    session_mgr.set_provider_session_id = AsyncMock()

    db_session = AsyncMock()
    executor = WorkflowExecutor(db=db_session, node_runner=node_runner, session_manager=session_mgr, session_factory=make_mock_session_factory())

    result = await executor.execute(spec, "run-1", make_context(), workflow_name="loopback")
    assert result is True
    # 4 node executions: coding → review → coding(loop) → review(loop) → end
    assert node_runner.run.call_count == 4
    # Verify execution order (node_config is first positional arg)
    calls = [c.args[0]["id"] for c in node_runner.run.call_args_list]
    assert calls == ["coding", "review", "coding", "review"]
    # R2 of coding should resume session s1
    assert node_runner.run.call_args_list[2].kwargs["is_resume"] is True
    assert node_runner.run.call_args_list[2].kwargs["session_id"] == "s1"
    # R2 of review should resume session s2
    assert node_runner.run.call_args_list[3].kwargs["session_id"] == "s2"


@pytest.mark.asyncio
async def test_executor_multiple_start_nodes():
    """Three research agents (A,B,C) all start from user input, merge into D."""
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="multi-start",
        nodes=[
            {"id": "a", "type": "script", "command": "echo a",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "script", "command": "echo b",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "c", "type": "script", "command": "echo c",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "d", "type": "script", "command": "echo d",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "a", "to": "d"},
            {"id": "e2", "from": "b", "to": "d"},
            {"id": "e3", "from": "c", "to": "d"},
        ],
    )
    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=[
        {"echo": "a", "_session_id": "s1"},
        {"echo": "b", "_session_id": "s2"},
        {"echo": "c", "_session_id": "s3"},
        {"echo": "d", "_session_id": "s4"},
    ])
    session_mgr = MagicMock()
    session_mgr.get_session_id = AsyncMock(return_value=None)
    session_mgr.get_or_create = AsyncMock(side_effect=["s1","s2","s3","s4"])
    session_mgr.is_session_valid = AsyncMock(return_value=False)
    session_mgr.mark_completed = AsyncMock()

    db_session = AsyncMock()
    executor = WorkflowExecutor(db=db_session, node_runner=node_runner, session_manager=session_mgr, session_factory=make_mock_session_factory())

    result = await executor.execute(spec, "run-1", make_context(), workflow_name="multi")
    assert result is True
    assert node_runner.run.call_count == 4
    # All three start nodes should have received user inputs (no upstream)
    for i in range(3):
        prompt = node_runner.run.call_args_list[i].kwargs.get("upstream_output")
        # Start nodes should have no upstream_output passed
        # (actually they get None from _get_upstream_output)


@pytest.mark.asyncio
async def test_executor_retry_with_error_feedback():
    """Agent node fails first attempt, retry includes error feedback."""
    from wflow.models.workflow import WorkflowSpec
    from wflow.adapters.claude_cli import ClaudeCLIError

    spec = WorkflowSpec(
        name="retry-feedback",
        nodes=[
            {"id": "x", "type": "claude", "prompt": "do task",
             "tools": {"allowed": [], "disallowed": []},
             "retry": {"max_retries": 2, "retry_delay_seconds": 0, "on_error": ["parse_error"]},
             "output": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}},
        ],
        edges=[{"id": "e1", "from": "x", "to": None}],
    )

    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=[
        ClaudeCLIError("No valid JSON found in output"),
        {"ok": True, "_session_id": "s1"},
    ])

    session_mgr = MagicMock()
    session_mgr.get_provider_session_id = AsyncMock(return_value=None)
    session_mgr.get_session_id = AsyncMock(side_effect=[None, "s1"])
    session_mgr.get_or_create = AsyncMock(side_effect=["s1", "s1"])
    session_mgr.is_session_valid = AsyncMock(return_value=False)
    session_mgr.mark_completed = AsyncMock()
    session_mgr.set_provider_session_id = AsyncMock()

    db_session = AsyncMock()
    executor = WorkflowExecutor(db=db_session, node_runner=node_runner, session_manager=session_mgr, session_factory=make_mock_session_factory())

    result = await executor.execute(spec, "run-1", make_context(), workflow_name="rf")
    assert result is True
    assert node_runner.run.call_count == 2
    # Second call should have retry_reason with the error
    retry_kwargs = node_runner.run.call_args_list[1].kwargs
    assert retry_kwargs["retry_reason"] == "No valid JSON found in output"
    assert retry_kwargs["is_resume"] is True


# ══════════════════════════════════════════════════════════════════════════════
# _compute_stale_nodes — graph-based loop-back detection
# ══════════════════════════════════════════════════════════════════════════════


def test_compute_stale_nodes_rejected_review():
    """Rejected review: coding→review→coding loop — both nodes are stale."""
    from wflow.models.workflow import WorkflowSpec

    spec = WorkflowSpec(
        name="hr",
        nodes=[
            {"id": "coding", "type": "claude", "prompt": "code",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "review", "type": "human_review", "prompt": "review pls",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "coding", "to": "review"},
            {"id": "e2", "from": "review", "to": "coding",
             "condition": "{{ nodes.review.output.approved }} == false"},
            {"id": "e3", "from": "review", "to": None,
             "condition": "{{ nodes.review.output.approved }} == true"},
        ],
    )

    executor = WorkflowExecutor(db=MagicMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())
    ctx = make_context()
    ctx["nodes"] = {
        "coding": {"output": {"plan": "x"}, "status": "completed"},
        "review": {"output": {"approved": False, "feedback": "bad"}, "status": "completed"},
    }

    stale = executor._compute_stale_nodes(spec, ctx)
    # coding is the target of the active condition edge review→coding
    # review is downstream of coding (via unconditional e1)
    assert "coding" in stale
    assert "review" in stale


def test_compute_stale_nodes_approved_review():
    """Approved review: condition edge to null — nothing is stale."""
    from wflow.models.workflow import WorkflowSpec

    spec = WorkflowSpec(
        name="hr",
        nodes=[
            {"id": "coding", "type": "claude", "prompt": "code",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "review", "type": "human_review", "prompt": "review pls",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "coding", "to": "review"},
            {"id": "e2", "from": "review", "to": "coding",
             "condition": "{{ nodes.review.output.approved }} == false"},
            {"id": "e3", "from": "review", "to": None,
             "condition": "{{ nodes.review.output.approved }} == true"},
        ],
    )

    executor = WorkflowExecutor(db=MagicMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())
    ctx = make_context()
    ctx["nodes"] = {
        "coding": {"output": {"plan": "x"}, "status": "completed"},
        "review": {"output": {"approved": True, "feedback": ""}, "status": "completed"},
    }

    stale = executor._compute_stale_nodes(spec, ctx)
    # e2 (approved==false) → False
    # e3 (approved==true) → True → target is None → not added
    assert stale == set()


def test_compute_stale_nodes_no_conditions():
    """No conditional edges in the graph — nothing is stale."""
    from wflow.models.workflow import WorkflowSpec

    spec = WorkflowSpec(
        name="linear",
        nodes=[
            {"id": "a", "type": "script", "command": "echo a",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "script", "command": "echo b",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[{"id": "e1", "from": "a", "to": "b"}],
    )

    executor = WorkflowExecutor(db=MagicMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())
    ctx = make_context()
    ctx["nodes"] = {
        "a": {"output": {"x": 1}, "status": "completed"},
        "b": {"output": {"y": 2}, "status": "completed"},
    }

    stale = executor._compute_stale_nodes(spec, ctx)
    assert stale == set()


def test_compute_stale_nodes_complex_cycle():
    """A→B→C→D with D→B condition: only B,C,D stale (A not in cycle)."""
    from wflow.models.workflow import WorkflowSpec

    spec = WorkflowSpec(
        name="complex",
        nodes=[
            {"id": "a", "type": "script", "command": "echo a",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "script", "command": "echo b",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "c", "type": "script", "command": "echo c",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "d", "type": "human_review", "prompt": "review",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "a", "to": "b"},
            {"id": "e2", "from": "b", "to": "c"},
            {"id": "e3", "from": "c", "to": "d"},
            {"id": "e4", "from": "d", "to": "b",
             "condition": "{{ nodes.d.output.approved }} == false"},
            {"id": "e5", "from": "d", "to": None,
             "condition": "{{ nodes.d.output.approved }} == true"},
        ],
    )

    executor = WorkflowExecutor(db=MagicMock(), node_runner=MagicMock(), session_manager=MagicMock(), session_factory=MagicMock())
    ctx = make_context()
    ctx["nodes"] = {
        "a": {"output": {"x": 1}, "status": "completed"},
        "b": {"output": {"y": 2}, "status": "completed"},
        "c": {"output": {"z": 3}, "status": "completed"},
        "d": {"output": {"approved": False, "feedback": "nope"}, "status": "completed"},
    }

    stale = executor._compute_stale_nodes(spec, ctx)
    # d→b condition active → b is stale root
    # BFS from b: b→c→d → all three stale
    assert stale == {"b", "c", "d"}
    assert "a" not in stale  # a is upstream of the cycle, not in it


@pytest.mark.asyncio
async def test_executor_human_review_rejected_loopback():
    """Full integration: coding→human_review, rejected → coding re-executes → review re-executes."""
    from wflow.models.workflow import WorkflowSpec

    spec = WorkflowSpec(
        name="hr-loopback",
        nodes=[
            {"id": "coding", "type": "claude", "prompt": "write code per feedback",
             "tools": {"allowed": [], "disallowed": []},
             "retry": {"max_retries": 1, "on_error": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "review", "type": "human_review", "prompt": "human reviews output",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "coding", "to": "review"},
            {"id": "e2", "from": "review", "to": "coding",
             "condition": "{{ nodes.review.output.approved }} == false"},
            {"id": "e3", "from": "review", "to": None,
             "condition": "{{ nodes.review.output.approved }} == true"},
        ],
    )

    # Simulate: coding R1 → review (rejected) → coding R2 → review R2 (approved)
    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=[
        # coding R1
        {"plan": "v1", "steps": ["step1"], "_session_id": "s1"},
        # review R1: returns _awaiting_review (simulating pause)
        {"_awaiting_review": True, "upstream_for_review": {"upstream": {"plan": "v1"}}},
        # coding R2 (after rejection — gets rejection feedback as upstream)
        {"plan": "v2 revised", "steps": ["step1", "step2"], "_session_id": "s1"},
        # review R2 (after revision)
        {"_awaiting_review": True, "upstream_for_review": {"upstream": {"plan": "v2 revised"}}},
    ])

    session_mgr = MagicMock()
    session_mgr.get_provider_session_id = AsyncMock(return_value=None)
    session_mgr.get_session_id = AsyncMock(side_effect=[None, None, "s1", None])
    session_mgr.get_or_create = AsyncMock(side_effect=["s1", None, "s1", None])
    session_mgr.is_session_valid = AsyncMock(return_value=True)
    session_mgr.mark_completed = AsyncMock()
    session_mgr.set_provider_session_id = AsyncMock()

    # Mock DB for the human_review pause code path
    db_session = AsyncMock()
    mock_run = MagicMock()
    mock_run.status = "running"
    mock_run_result = MagicMock()
    mock_run_result.scalar_one_or_none.return_value = mock_run
    # When the executor queries WorkflowRun, return our mock
    # (other queries like commit/add are fine as plain AsyncMock)
    async def _execute_side_effect(stmt, *args, **kwargs):
        from sqlalchemy import Select
        if isinstance(stmt, Select):
            # Return a result-like object
            return mock_run_result
        return MagicMock()
    db_session.execute = _execute_side_effect
    db_session.commit = AsyncMock()
    db_session.add = MagicMock()

    executor = WorkflowExecutor(db=db_session, node_runner=node_runner, session_manager=session_mgr, session_factory=make_mock_session_factory())

    # First execution: coding→review (pauses for review)
    ctx = make_context()
    result1 = await executor.execute(spec, "run-hr", ctx, workflow_name="hr")
    # Should return False because review paused (awaiting_review)
    assert result1 is False
    assert node_runner.run.call_count == 2  # coding + review
    assert ctx["nodes"]["coding"]["status"] == "completed"
    assert ctx["nodes"]["review"]["status"] == "awaiting_review"

    # Simulate what the review API does: set review to completed (rejected)
    # AND transition run status back to RUNNING
    ctx["nodes"]["review"] = {
        "output": {"approved": False, "feedback": "add more detail"},
        "status": "completed",
    }
    mock_run.status = "running"  # API transitions AWAITING_REVIEW → RUNNING

    # Resume: executor relaunched with updated context (rejected review)
    result2 = await executor.execute(spec, "run-hr", ctx, workflow_name="hr")
    # Should return False because review pauses again
    assert result2 is False
    # coding should have re-executed (stale) + review re-executed (fresh pause)
    assert node_runner.run.call_count == 4  # 2 more calls
    calls = [c.args[0]["id"] for c in node_runner.run.call_args_list]
    assert calls == ["coding", "review", "coding", "review"]

    # Verify coding R2 got the rejection feedback as upstream
    upstream_r2 = node_runner.run.call_args_list[2].kwargs.get("upstream_output")
    assert upstream_r2 is not None
    assert upstream_r2.get("approved") is False
    assert upstream_r2.get("feedback") == "add more detail"

    # Now simulate: review approved on second attempt
    ctx["nodes"]["review"] = {
        "output": {"approved": True, "feedback": ""},
        "status": "completed",
    }
    mock_run.status = "running"  # API transitions AWAITING_REVIEW → RUNNING

    # R3: executor relaunched — approved → terminal
    result3 = await executor.execute(spec, "run-hr", ctx, workflow_name="hr")
    assert result3 is True  # workflow completes
    # No new calls — both nodes are non-stale previously_completed → skipped
    assert node_runner.run.call_count == 4


# ══════════════════════════════════════════════════════════════════════════════
# Concurrent execution
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_executor_concurrent_diamond_dag():
    """Diamond DAG A→B→D, A→C→D — verify B and C execute concurrently.

    Uses ``asyncio.sleep`` yield points inside ``node_runner.run`` so the
    event loop interleaves B and C within the same ``asyncio.gather`` batch.
    A ``max_in_flight`` counter proves both were active at the same moment.
    """
    from wflow.models.workflow import WorkflowSpec

    spec = WorkflowSpec(
        name="diamond",
        nodes=[
            {"id": "a", "type": "script", "command": "echo a",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "script", "command": "echo b",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "c", "type": "script", "command": "echo c",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "d", "type": "script", "command": "echo d",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "a", "to": "b"},
            {"id": "e2", "from": "a", "to": "c"},
            {"id": "e3", "from": "b", "to": "d"},
            {"id": "e4", "from": "c", "to": "d"},
        ],
    )

    in_flight = 0
    max_in_flight = 0

    async def tracked_run(node_config, context, **kwargs):
        nonlocal in_flight, max_in_flight
        nid = node_config["id"]
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        # Yield to the event loop so other coroutines in the same gather()
        # batch get a chance to run — without this, cooperative asyncio
        # would run each coroutine to completion before switching.
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {"echo": nid, "_session_id": f"s-{nid}"}

    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=tracked_run)

    # SessionManager is instantiated per-node in the concurrent path —
    # patch the class so every instance is a fresh mock.
    with patch("wflow.engine.executor.SessionManager") as MockSM:
        def _make_sm(_db):
            sm = MagicMock()
            sm.get_provider_session_id = AsyncMock(return_value=None)
            sm.get_or_create = AsyncMock(return_value="s-x")
            sm.mark_completed = AsyncMock()
            sm.set_provider_session_id = AsyncMock()
            return sm
        MockSM.side_effect = _make_sm

        main_sm = MagicMock()
        main_sm.get_provider_session_id = AsyncMock(return_value=None)
        main_sm.get_or_create = AsyncMock(return_value="s-main")
        main_sm.mark_completed = AsyncMock()
        main_sm.set_provider_session_id = AsyncMock()

        db_session = AsyncMock()
        executor = WorkflowExecutor(
            db=db_session,
            node_runner=node_runner,
            session_manager=main_sm,
            session_factory=make_mock_session_factory(),
        )

        ctx = make_context()
        result = await executor.execute(spec, "run-1", ctx, workflow_name="diamond")

    assert result is True
    assert node_runner.run.call_count == 4
    assert max_in_flight >= 2, (
        f"Expected B and C to execute concurrently, "
        f"but max_in_flight={max_in_flight}"
    )
    # Verify D received both upstream outputs
    d_call = node_runner.run.call_args_list[3].kwargs
    up = d_call["upstream_output"]
    assert "b" in up
    assert "c" in up


@pytest.mark.asyncio
async def test_executor_serial_with_max_concurrency_1(monkeypatch):
    """WFLOW_MAX_CONCURRENCY=1 forces serial execution even with factory."""
    monkeypatch.setenv("WFLOW_MAX_CONCURRENCY", "1")
    # Force _resolve_max_concurrency to re-read (it reads at call time)
    from wflow.models.workflow import WorkflowSpec

    spec = WorkflowSpec(
        name="diamond",
        nodes=[
            {"id": "a", "type": "script", "command": "echo a",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "script", "command": "echo b",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "c", "type": "script", "command": "echo c",
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "d", "type": "script", "command": "echo d",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "a", "to": "b"},
            {"id": "e2", "from": "a", "to": "c"},
            {"id": "e3", "from": "b", "to": "d"},
            {"id": "e4", "from": "c", "to": "d"},
        ],
    )

    in_flight = 0
    max_in_flight = 0

    async def tracked_run(node_config, context, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {"echo": node_config["id"], "_session_id": f"s-{node_config['id']}"}

    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=tracked_run)

    session_mgr = MagicMock()
    session_mgr.get_provider_session_id = AsyncMock(return_value=None)
    session_mgr.get_or_create = AsyncMock(side_effect=["s-a", "s-b", "s-c", "s-d"])
    session_mgr.mark_completed = AsyncMock()
    session_mgr.set_provider_session_id = AsyncMock()

    db_session = AsyncMock()
    executor = WorkflowExecutor(
        db=db_session, node_runner=node_runner, session_manager=session_mgr,
        session_factory=make_mock_session_factory(),
    )

    result = await executor.execute(spec, "run-1", make_context(), workflow_name="diamond")
    assert result is True
    assert node_runner.run.call_count == 4
    # With semaphore(1), execution is effectively serial → max_in_flight == 1
    assert max_in_flight == 1, (
        f"Expected serial execution with MAX_CONCURRENCY=1, "
        f"but max_in_flight={max_in_flight}"
    )


@pytest.mark.asyncio
async def test_executor_concurrent_claude_nodes():
    """Diamond DAG with claude-type nodes — exercises concurrent session resolution.

    Unlike ``script`` nodes (which skip ``_resolve_session`` entirely),
    ``claude``/``opencode`` nodes call ``SessionManager.get_provider_session_id``
    and ``SessionManager.get_or_create``.  This test verifies those calls
    happen correctly when two session-based nodes run in the same batch.
    """
    from wflow.models.workflow import WorkflowSpec

    spec = WorkflowSpec(
        name="diamond-claude",
        nodes=[
            {"id": "a", "type": "claude", "prompt": "start",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "claude", "prompt": "branch-b",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "c", "type": "claude", "prompt": "branch-c",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "d", "type": "script", "command": "echo merge",
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "a", "to": "b"},
            {"id": "e2", "from": "a", "to": "c"},
            {"id": "e3", "from": "b", "to": "d"},
            {"id": "e4", "from": "c", "to": "d"},
        ],
    )

    in_flight = 0
    max_in_flight = 0

    async def tracked_run(node_config, context, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {"echo": node_config["id"], "_session_id": f"s-{node_config['id']}"}

    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=tracked_run)

    # Patch SessionManager for the per-node instances created in
    # _execute_batch_concurrent.
    with patch("wflow.engine.executor.SessionManager") as MockSM:
        def _make_sm(_db):
            sm = MagicMock()
            sm.get_provider_session_id = AsyncMock(return_value=None)
            sm.get_or_create = AsyncMock(return_value="s-x")
            sm.mark_completed = AsyncMock()
            sm.set_provider_session_id = AsyncMock()
            return sm
        MockSM.side_effect = _make_sm

        main_sm = MagicMock()
        main_sm.get_provider_session_id = AsyncMock(return_value=None)
        main_sm.get_or_create = AsyncMock(return_value="s-main")
        main_sm.mark_completed = AsyncMock()
        main_sm.set_provider_session_id = AsyncMock()

        db_session = AsyncMock()
        executor = WorkflowExecutor(
            db=db_session,
            node_runner=node_runner,
            session_manager=main_sm,
            session_factory=make_mock_session_factory(),
        )

        ctx = make_context()
        result = await executor.execute(spec, "run-1", ctx, workflow_name="diamond-c")

    assert result is True
    assert node_runner.run.call_count == 4
    assert max_in_flight >= 2, (
        f"Expected claude B and C to execute concurrently, "
        f"but max_in_flight={max_in_flight}"
    )
    # Verify D received both upstream outputs
    d_call = node_runner.run.call_args_list[3].kwargs
    up = d_call["upstream_output"]
    assert "b" in up
    assert "c" in up


# ══════════════════════════════════════════════════════════════════════════════
# Human review concurrency — regression tests
# ══════════════════════════════════════════════════════════════════════════════


def test_human_review_pause_idempotent_when_run_already_awaiting_review():
    """_handle_human_review_pause succeeds when run is already AWAITING_REVIEW.

    The second concurrent human_review node should save its state without
    re-transitioning the run status (which would raise ValueError).
    """
    import asyncio
    from wflow.models.db import NodeExecution

    db_session = AsyncMock()
    mock_run = MagicMock()
    mock_run.status = "awaiting_review"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_run

    async def _execute_side_effect(stmt, *args, **kwargs):
        from sqlalchemy import Select
        if isinstance(stmt, Select):
            return mock_result
        return MagicMock()
    db_session.execute = _execute_side_effect
    db_session.commit = AsyncMock()
    db_session.add = MagicMock()

    executor = WorkflowExecutor(
        db=db_session,
        node_runner=MagicMock(),
        session_manager=MagicMock(),
        session_factory=MagicMock(),
    )

    ne = NodeExecution(
        id="ne-2", run_id="run-1", node_id="review_b",
        type="human_review", status="running",
        input="{}",
    )
    output = {"upstream_for_review": {"upstream": {"data": "x"}}}
    context: TemplateContext = {"inputs": {}, "nodes": {}, "run": {"id": "run-1"}, "config": {}}

    result = asyncio.run(executor._handle_human_review_pause(
        "run-1", "review_b", output, context, ne, db=db_session,
    ))

    assert result is False
    assert ne.status == "awaiting_review"
    assert context["nodes"]["review_b"]["status"] == "awaiting_review"
    assert db_session.commit.called
    # Run status should NOT have been re-transitioned (was already AWAITING_REVIEW)
    assert mock_run.status == "awaiting_review"
