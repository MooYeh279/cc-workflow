import pytest
from wflow.engine.template import resolve_template, TemplateContext


def make_context(**overrides) -> TemplateContext:
    defaults = {
        "inputs": {"requirement": "test-requirement"},
        "nodes": {},
        "run": {"id": "run-1"},
        "config": {},
    }
    defaults.update(overrides)
    return defaults


def test_resolve_requirement_input():
    result = resolve_template("{{ inputs.requirement }}", make_context())
    assert result == "test-requirement"


def test_resolve_requirement_in_nested_dict():
    args = {"task": "{{ inputs.requirement }}", "extra": {"desc": "{{ inputs.requirement }}"}}
    result = resolve_template(args, make_context())
    assert result["task"] == "test-requirement"
    assert result["extra"]["desc"] == "test-requirement"


def test_resolve_missing_requirement_key():
    ctx = make_context()
    del ctx["inputs"]["requirement"]
    result = resolve_template("{{ inputs.requirement }}", ctx)
    assert result == ""


def test_resolve_requirement_with_surrounding_text():
    result = resolve_template("Task: {{ inputs.requirement }}, done", make_context())
    assert result == "Task: test-requirement, done"


def test_requirement_not_equals_condition():
    result = resolve_template("{{ inputs.requirement }}", make_context(inputs={"requirement": "other"}))
    assert result == "other"


def test_empty_requirement():
    result = resolve_template("{{ inputs.requirement }}", make_context(inputs={"requirement": ""}))
    assert result == ""
