import pytest
from unittest.mock import AsyncMock, MagicMock
from wflow.engine.node_runner import NodeRunner
from wflow.engine.node_handler import (
    ClaudeHandler,
    OpenCodeHandler,
    ScriptHandler,
    HumanReviewHandler,
    build_agent_prompt,
)
from wflow.adapters.claude_cli import ClaudeCLI
from wflow.adapters.opencode_cli import OpenCodeCLI
from wflow.adapters.script_runner import ScriptRunner


@pytest.fixture
def claude_cli():
    cli = MagicMock(spec=ClaudeCLI)
    cli.run = AsyncMock(return_value={"result": "ok", "_session_id": "sess-1"})
    return cli


@pytest.fixture
def script_runner():
    sr = MagicMock(spec=ScriptRunner)
    sr.run = AsyncMock(return_value={"echo": {"msg": "hello"}})
    return sr


@pytest.fixture
def opencode_cli():
    oc = MagicMock(spec=OpenCodeCLI)
    oc.run = AsyncMock(return_value={"result": "ok", "_session_id": "sess-1"})
    return oc


@pytest.fixture
def handlers(claude_cli, script_runner, opencode_cli):
    return {
        "claude": ClaudeHandler(claude_cli),
        "opencode": OpenCodeHandler(opencode_cli),
        "script": ScriptHandler(script_runner),
        "human_review": HumanReviewHandler(),
    }


@pytest.fixture
def node_runner(handlers):
    return NodeRunner(handlers=handlers)


# ══════════════════════════════════════════════════════════════════════════════
# NodeRunner dispatch tests
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_run_agent_node_create_mode(node_runner, claude_cli):
    node_config = {
        "id": "coding", "type": "claude", "prompt": "Write code",
        "tools": {"allowed": ["Read"], "disallowed": []},
        "retry": {"max_retries": 2, "on_error": ["timeout"]},
        "output": {"type": "object", "properties": {}, "required": []},
    }
    context = {"inputs": {}, "nodes": {}, "run": {"id": "run-1"}, "config": {}}

    result = await node_runner.run(node_config, context, session_id=None, upstream_output={"task": "hello"})

    claude_cli.run.assert_called_once()
    call_kwargs = claude_cli.run.call_args.kwargs
    assert "## 角色任务" in call_kwargs["prompt"]
    assert "## 上游节点输出" in call_kwargs["prompt"]
    assert call_kwargs["is_resume"] is False
    assert result["result"] == "ok"


@pytest.mark.asyncio
async def test_run_agent_node_resume_mode(node_runner, claude_cli):
    node_config = {
        "id": "coding", "type": "claude", "prompt": "Fix the bug",
        "tools": {"allowed": ["Edit"], "disallowed": []},
        "retry": {"max_retries": 2, "on_error": []},
        "output": {"type": "object", "properties": {}, "required": []},
    }
    context = {"inputs": {}, "nodes": {}, "run": {"id": "run-1"}, "config": {}}

    result = await node_runner.run(node_config, context, session_id="sess-existing", is_resume=True)

    call_kwargs = claude_cli.run.call_args.kwargs
    assert call_kwargs["is_resume"] is True
    assert call_kwargs["session_id"] == "sess-existing"


@pytest.mark.asyncio
async def test_run_script_node(node_runner, script_runner):
    node_config = {
        "id": "validate", "type": "script",
        "command": "python ./scripts/validate.py",
        "timeout_seconds": 60,
        "output": {"type": "object", "properties": {}, "required": []},
    }
    context = {"inputs": {"msg": "hello"}, "nodes": {}, "run": {"id": "run-1"}, "config": {}}

    result = await node_runner.run(node_config, context, session_id=None, cwd="/tmp/test")

    script_runner.run.assert_called_once()
    call_kwargs = script_runner.run.call_args.kwargs
    assert call_kwargs["command"] == "python ./scripts/validate.py"
    assert call_kwargs["timeout_seconds"] == 60
    assert call_kwargs["cwd"] == "/tmp/test"
    assert call_kwargs["context"]["inputs"]["msg"] == "hello"
    assert result == {"echo": {"msg": "hello"}}


@pytest.mark.asyncio
async def test_run_opencode_node(node_runner, opencode_cli):
    node_config = {
        "id": "review", "type": "opencode", "prompt": "Review code",
        "model": "deepseek-v4-pro",
        "output": {"type": "object", "properties": {}, "required": []},
    }
    context = {"inputs": {}, "nodes": {}, "run": {"id": "r1"}, "config": {}}

    result = await node_runner.run(node_config, context, session_id=None)
    opencode_cli.run.assert_called_once()
    assert result["result"] == "ok"


@pytest.mark.asyncio
async def test_run_opencode_raises_when_not_registered(claude_cli, script_runner):
    """If no opencode handler is registered, dispatch fails with ValueError."""
    nr = NodeRunner(handlers={
        "claude": ClaudeHandler(claude_cli),
        "script": ScriptHandler(script_runner),
    })
    node_config = {"id": "r", "type": "opencode", "prompt": "test",
                   "output": {"type": "object", "properties": {}}}
    context = {"inputs": {}, "nodes": {}, "run": {"id": "r1"}, "config": {}}

    with pytest.raises(ValueError, match="Unknown node type"):
        await nr.run(node_config, context, session_id=None)


@pytest.mark.asyncio
async def test_run_unknown_type_raises(node_runner):
    node_config = {"id": "bad", "type": "unknown"}
    context = {"inputs": {}, "nodes": {}, "run": {"id": "run-1"}, "config": {}}

    with pytest.raises(ValueError, match="Unknown node type"):
        await node_runner.run(node_config, context, session_id=None)


# ══════════════════════════════════════════════════════════════════════════════
# Prompt building tests (now on module-level functions in node_handler)
# ══════════════════════════════════════════════════════════════════════════════


def test_build_agent_prompt_first_node_with_inputs():
    node = {"id": "coding", "prompt": "你是一个工程师"}
    context = {"inputs": {"requirement": "写一个计算器"}, "nodes": {}, "run": {"id": "r1"}, "config": {}}

    result = build_agent_prompt(node, context, upstream_output=None)

    assert "## 角色任务" in result
    assert "你是一个工程师" in result
    assert "## 用户输入" in result
    assert "写一个计算器" in result


def test_build_agent_prompt_subsequent_node_with_upstream():
    node = {"id": "review", "prompt": "审查代码"}
    context = {"inputs": {}, "nodes": {}, "run": {"id": "r1"}, "config": {}}
    upstream = {"files_changed": ["a.py"], "summary": "完成了"}

    result = build_agent_prompt(node, context, upstream_output=upstream)

    assert "## 角色任务" in result
    assert "审查代码" in result
    assert "## 上游节点输出" in result
    assert "a.py" in result


def test_build_agent_prompt_no_upstream_no_inputs():
    node = {"id": "start", "prompt": "开始"}
    context = {"inputs": {}, "nodes": {}, "run": {"id": "r1"}, "config": {}}

    result = build_agent_prompt(node, context, upstream_output=None)

    assert "## 角色任务" in result
    assert "开始" in result
    assert "## 用户输入" not in result


def test_build_agent_prompt_empty_upstream_uses_inputs():
    node = {"id": "start", "prompt": "开始"}
    context = {"inputs": {"task": "hello"}, "nodes": {}, "run": {"id": "r1"}, "config": {}}

    result = build_agent_prompt(node, context, upstream_output={})

    assert "## 角色任务" in result
    assert "## 用户输入" in result
    assert "hello" in result


def test_build_agent_prompt_with_retry_reason():
    """On retry, error feedback is included so agent can fix output."""
    node = {"id": "x", "prompt": "完成任务"}
    context = {"inputs": {"task": "hello"}, "nodes": {}, "run": {"id": "r1"}, "config": {}}

    result = build_agent_prompt(
        node, context, upstream_output=None,
        retry_reason="No valid JSON found in output",
    )

    assert "## 角色任务" in result
    assert "## 上次输出错误" in result
    assert "No valid JSON found" in result
    assert "请修正后重新输出" in result
    assert "## 用户输入" in result


# ══════════════════════════════════════════════════════════════════════════════
# Template resolution in prompts
# ══════════════════════════════════════════════════════════════════════════════


def test_build_agent_prompt_resolves_inputs_template():
    """Prompt can reference {{ inputs.field }} to inject user parameters."""
    node = {"id": "coding", "prompt": "写一个 {{ inputs.task }} 的程序"}
    context = {"inputs": {"task": "计算器"}, "nodes": {}, "run": {"id": "r1"}, "config": {}}

    result = build_agent_prompt(node, context, upstream_output=None)

    assert "写一个 计算器 的程序" in result
    assert "{{ inputs.task }}" not in result


def test_build_agent_prompt_resolves_nodes_output_template():
    """Prompt can reference upstream node output via {{ nodes.X.output.field }}."""
    node = {"id": "review", "prompt": "审查以下文件: {{ nodes.coding.output.files_changed }}"}
    context = {
        "inputs": {},
        "nodes": {
            "coding": {"output": {"files_changed": ["a.py", "b.py"]}, "status": "completed"}
        },
        "run": {"id": "r1"},
        "config": {},
    }
    upstream = {"files_changed": ["a.py", "b.py"]}

    result = build_agent_prompt(node, context, upstream_output=upstream)

    assert "['a.py', 'b.py']" in result
    assert "{{ nodes.coding.output.files_changed }}" not in result


def test_build_agent_prompt_resolves_multiple_templates():
    """Multiple template variables in one prompt are all resolved."""
    node = {
        "id": "report",
        "prompt": (
            "基于 {{ nodes.research.output.topic }} 的研究成果，"
            "按要求 {{ inputs.format }} 格式输出报告"
        ),
    }
    context = {
        "inputs": {"format": "markdown"},
        "nodes": {
            "research": {"output": {"topic": "AI安全"}, "status": "completed"}
        },
        "run": {"id": "r1"},
        "config": {},
    }

    result = build_agent_prompt(node, context, upstream_output=None)

    assert "AI安全" in result
    assert "markdown" in result
    assert "{{" not in result


def test_build_agent_prompt_resolves_config_template():
    """Prompt can reference {{ config.key }} from workflow config."""
    node = {"id": "check", "prompt": "重试次数上限: {{ config.max_retries }}"}
    context = {"inputs": {}, "nodes": {}, "run": {"id": "r1"}, "config": {"max_retries": 5}}

    result = build_agent_prompt(node, context, upstream_output=None)

    assert "重试次数上限: 5" in result
    assert "{{ config.max_retries }}" not in result


def test_build_agent_prompt_missing_template_variable_becomes_empty():
    """Unresolvable template paths become empty strings, not errors."""
    node = {"id": "test", "prompt": "前: {{ nodes.missing.output.x }}:后"}
    context = {"inputs": {}, "nodes": {}, "run": {"id": "r1"}, "config": {}}

    result = build_agent_prompt(node, context, upstream_output=None)

    assert "前: :后" in result


def test_build_agent_prompt_no_template_unchanged():
    """Prompt with no template variables passes through unchanged."""
    node = {"id": "start", "prompt": "普通任务描述，无变量"}
    context = {"inputs": {"task": "hello"}, "nodes": {}, "run": {"id": "r1"}, "config": {}}

    result = build_agent_prompt(node, context, upstream_output=None)

    assert "普通任务描述，无变量" in result


def test_script_handler_command_resolves_template():
    """Script command supports {{ }} template variables."""
    from wflow.engine.node_handler import ScriptHandler

    mock_runner = MagicMock()
    mock_runner.run = AsyncMock(return_value={"status": "ok"})
    handler = ScriptHandler(mock_runner)

    node_config = {"id": "s1", "command": "python run.py {{ nodes.coding.output.result }}"}
    context = {
        "inputs": {},
        "nodes": {"coding": {"output": {"result": "data.csv"}, "status": "completed"}},
        "run": {"id": "r1"},
        "config": {},
    }

    import asyncio
    asyncio.run(handler.run(
        node_config, context,
        session_id=None, is_resume=False,
        upstream_output=None, cwd="/tmp",
        retry_reason=None,
    ))

    called_command = mock_runner.run.call_args[1]["command"]
    assert called_command == "python run.py data.csv"
    assert "{{" not in called_command
