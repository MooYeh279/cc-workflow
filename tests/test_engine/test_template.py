import pytest
from wflow.engine.template import resolve_template, TemplateContext


def make_context(**overrides) -> TemplateContext:
    defaults = {
        "inputs": {"requirement": "Build a TODO app"},
        "nodes": {
            "coding": {"output": {"files_changed": ["app.py"], "summary": "done"}, "status": "completed", "retry_count": 0},
        },
        "run": {"id": "run-123"},
        "config": {"max_retries": 3},
    }
    defaults.update(overrides)
    return defaults


def test_resolve_inputs_variable():
    result = resolve_template("{{ inputs.requirement }}", make_context())
    assert result == "Build a TODO app"


def test_resolve_node_output():
    result = resolve_template("{{ nodes.coding.output.files_changed }}", make_context())
    assert result == ['app.py']


def test_resolve_node_status():
    result = resolve_template("{{ nodes.coding.status }}", make_context())
    assert result == "completed"


def test_resolve_node_retry_count():
    result = resolve_template("{{ nodes.coding.retry_count }}", make_context())
    assert result == 0


def test_resolve_run_id():
    result = resolve_template("{{ run.id }}", make_context())
    assert result == "run-123"


def test_resolve_config_value():
    result = resolve_template("{{ config.max_retries }}", make_context())
    assert result == 3


def test_resolve_multiple_variables_in_string():
    result = resolve_template("Status: {{ nodes.coding.status }}, by {{ run.id }}", make_context())
    assert result == "Status: completed, by run-123"


def test_resolve_missing_variable_returns_empty_string():
    result = resolve_template("{{ nodes.nonexistent.output.x }}", make_context())
    assert result == ""


def test_resolve_dict_values_recursively():
    args = {
        "task": "{{ inputs.requirement }}",
        "output": "{{ nodes.coding.output }}",
        "retries": "{{ nodes.coding.retry_count }}",
    }
    result = resolve_template(args, make_context())
    assert result["task"] == "Build a TODO app"
    assert result["output"] == {"files_changed": ["app.py"], "summary": "done"}
    assert result["retries"] == 0


def test_no_template_returns_original():
    result = resolve_template("plain string", make_context())
    assert result == "plain string"
