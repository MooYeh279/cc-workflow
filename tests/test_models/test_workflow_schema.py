import pytest
from pydantic import ValidationError
from wflow.models.workflow import (
    WorkflowConfig, AgentNode, ScriptNode, Edge, WorkflowSpec, ToolConfig
)


def test_validate_valid_agent_node():
    node = AgentNode(
        id="coding",
        name="Code Implementation",
        type="agent",
        prompt="Write code for: {{ inputs.requirement }}",
        tools=ToolConfig(allowed=["Read", "Write"], disallowed=[]),
        output={"type": "object", "properties": {"done": {"type": "boolean"}}, "required": ["done"]},
    )
    assert node.id == "coding"
    assert node.type == "agent"
    assert node.retry.max_retries == 3


def test_validate_valid_script_node():
    node = ScriptNode(
        id="validate",
        name="Validation",
        type="script",
        command="python ./scripts/validate.py {{ nodes.coding.output }}",
        timeout_seconds=120,
        output={"type": "object", "properties": {"passed": {"type": "boolean"}}, "required": ["passed"]},
    )
    assert node.type == "script"
    assert node.command == "python ./scripts/validate.py {{ nodes.coding.output }}"
    assert node.timeout_seconds == 120


def test_validate_workflow_with_edges():
    spec = WorkflowSpec(
        name="test-wf",
        config=WorkflowConfig(max_retries=2, retry_delay_seconds=10, timeout_seconds=600),
        nodes=[
            {
                "id": "start", "name": "start", "type": "claude", "prompt": "go",
                "output": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
            },
            {
                "id": "end", "name": "end", "type": "script",
                "command": "python ./scripts/noop.py",
                "output": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
            },
        ],
        edges=[
            {"id": "e1", "from": "start", "to": "end"},
        ],
    )
    assert len(spec.nodes) == 2
    assert len(spec.edges) == 1


def test_duplicate_node_ids_raise_error():
    with pytest.raises(ValidationError):
        WorkflowSpec(
            name="bad",
            nodes=[
                {"id": "same", "name": "a", "type": "claude", "prompt": "x",
                 "output": {"type": "object", "properties": {}, "required": []}},
                {"id": "same", "name": "b", "type": "script",
                 "command": "echo test",
                 "output": {"type": "object", "properties": {}, "required": []}},
            ],
            edges=[],
        )


def test_condition_edge_with_template():
    edge = Edge(id="e1", from_="review", to="coding",
                condition="{{ nodes.review.output.approved }} == false")
    assert edge.condition == "{{ nodes.review.output.approved }} == false"


def test_workflow_with_inputs_and_outputs():
    spec = WorkflowSpec(
        name="io-test",
        nodes=[
            {"id": "n1", "name": "n1", "type": "claude", "prompt": "{{ inputs.task }}",
             "output": {"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"]}},
        ],
        edges=[{"id": "e1", "from": "n1", "to": None}],
        inputs={"task": {"type": "string", "required": True}},
        outputs={"final_result": "{{ nodes.n1.output.result }}"},
    )
    assert spec.inputs["task"]["type"] == "string"
