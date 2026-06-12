import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from wflow.adapters.claude_cli import ClaudeCLI, ClaudeCLIError, ClaudeCLITimeout


@pytest.fixture
def claude():
    return ClaudeCLI()


def test_build_command_stream_json(claude):
    cmd = claude._build_command(
        session_id="sess-001",
        is_resume=False,
        tools_allowed=["Read", "Write"],
    )
    assert "claude" in cmd[0].lower()
    assert "--print" in cmd
    assert "--verbose" in cmd
    assert "stream-json" in cmd
    assert "--session-id" in cmd
    assert "sess-001" in cmd
    assert "--allowedTools" in cmd
    assert "Read,Write" in cmd


def test_build_command_resume(claude):
    cmd = claude._build_command(
        session_id="sess-001",
        is_resume=True,
        tools_allowed=["Edit", "Bash"],
    )
    assert "--resume" in cmd
    assert "sess-001" in cmd


def test_build_input_format(claude):
    result = claude._build_input("Write code")
    parsed = json.loads(result.strip())
    assert parsed["type"] == "user"
    assert parsed["message"]["role"] == "user"
    content = parsed["message"]["content"]
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "Write code"


@pytest.mark.asyncio
async def test_stream_and_parse_result_event(claude):
    """Result event is parsed inline during streaming."""
    stream = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "model": "sonnet", "tools": ["Read"]}),
        json.dumps({"type": "result", "result": json.dumps({"status": "done"})}),
    ])

    mock_proc = MagicMock()
    mock_proc.stdout = AsyncMock()
    lines = [l.encode("utf-8") + b"\n" for l in stream.split("\n")]
    lines.append(b"")
    mock_proc.stdout.readline = AsyncMock(side_effect=lines)

    result = await claude._stream_and_parse(mock_proc, "test", logger=None)
    assert result == {"status": "done"}


@pytest.mark.asyncio
async def test_stream_and_parse_fallback_text(claude):
    """Fallback: extract JSON from assistant text when no result event."""
    stream = "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": '```json\n{"summary": "complete"}\n```'}
        ]}}),
    ])

    mock_proc = MagicMock()
    mock_proc.stdout = AsyncMock()
    lines = [l.encode("utf-8") + b"\n" for l in stream.split("\n")]
    lines.append(b"")
    mock_proc.stdout.readline = AsyncMock(side_effect=lines)

    result = await claude._stream_and_parse(mock_proc, "test", logger=None)
    assert result == {"summary": "complete"}


@pytest.mark.asyncio
async def test_stream_and_parse_no_json_raises(claude):
    """Stream with no valid JSON raises error."""
    mock_proc = MagicMock()
    mock_proc.stdout = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(side_effect=[
        b'{"type":"system","subtype":"init"}\n', b"",
    ])

    with pytest.raises(ClaudeCLIError, match="No result found"):
        await claude._stream_and_parse(mock_proc, "test", logger=None)


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_stream_json(mock_subprocess, claude):
    stream_output = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "model": "sonnet", "tools": ["Read"]}),
        json.dumps({"type": "result", "result": json.dumps({"ok": True})}),
    ])

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = AsyncMock()
    mock_proc.stderr = AsyncMock()

    lines = [l.encode("utf-8") + b"\n" for l in stream_output.split("\n")]
    lines.append(b"")
    mock_proc.stdout.readline = AsyncMock(side_effect=lines)
    mock_proc.stderr.read = AsyncMock(return_value=b"")
    mock_proc.wait = AsyncMock()
    mock_subprocess.return_value = mock_proc

    result = await claude.run(
        prompt="Write code",
        node_id="coding",
        session_id=None,
        is_resume=False,
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        tools_allowed=["Read"],
        timeout_seconds=30,
    )

    assert result["ok"] is True
    assert result["_session_id"] is not None
    stdin_call = mock_proc.stdin.write.call_args[0][0]
    assert b'"type"' in stdin_call
    assert b'"user"' in stdin_call


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_resumes_existing_session(mock_subprocess, claude):
    stream_output = json.dumps({
        "type": "result", "result": json.dumps({"fixed": True}),
    }) + "\n"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = AsyncMock()
    mock_proc.stderr = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(side_effect=[
        stream_output.encode("utf-8"), b"",
    ])
    mock_proc.stderr.read = AsyncMock(return_value=b"")
    mock_proc.wait = AsyncMock()
    mock_subprocess.return_value = mock_proc

    result = await claude.run(
        prompt="Fix the bug",
        node_id="coding",
        session_id="existing-sess",
        is_resume=True,
        output_schema={"type": "object", "properties": {"fixed": {"type": "boolean"}}, "required": ["fixed"]},
        tools_allowed=["Edit"],
        timeout_seconds=30,
    )

    assert result["fixed"] is True
    assert result["_session_id"] == "existing-sess"


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_timeout(mock_subprocess, claude):
    import asyncio as aio
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdin = AsyncMock()
    mock_proc.stdout = AsyncMock()
    mock_proc.stderr = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(side_effect=aio.TimeoutError())
    mock_proc.kill = MagicMock()
    mock_subprocess.return_value = mock_proc

    with pytest.raises(ClaudeCLITimeout):
        await claude.run(
            prompt="long task",
            node_id="test",
            session_id=None,
            is_resume=False,
            output_schema={"type": "object", "properties": {}},
            tools_allowed=[],
            timeout_seconds=1,
        )
